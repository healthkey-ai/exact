"""No HIV / No Hepatitis B / No Hepatitis C are INVERTED views (#5663).

The record stores the positive status. Trials ask the negated question ("No
Hepatitis B required"), so EXACT's `no_*_status` is derived from the positive
column, not stored. Reading a removed column as its class default made every
patient a confirmed negative — including one with a positive status, which is a
false match on an eligibility gate.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.promop_adapter import (
    PROMOP_NEGATED_FIELDS,
    _boolean_answer,
    build_patient_info_from_promop_row,
    normalize_promop_row,
)
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

HEADER = {'disease': 'multiple myeloma', 'patient_age': 60}


@pytest.mark.parametrize('negated,positive', sorted(PROMOP_NEGATED_FIELDS.items()))
@pytest.mark.parametrize('stored,shown', [
    (True, False),   # has the virus -> "No <virus>" is False
    (False, True),   # does not have it -> "No <virus>" is True
    (None, None),    # nobody answered -> unknown, never "No"
])
def test_the_negation_is_derived_from_the_positive_status(negated, positive, stored, shown):
    row = normalize_promop_row({**HEADER, positive: stored})

    assert row[negated] is shown
    assert positive not in row, 'the positive column has no PatientInfo field'


@pytest.mark.parametrize('negated,positive', sorted(PROMOP_NEGATED_FIELDS.items()))
@pytest.mark.parametrize('stored,shown', [
    ('no', True), ('No', True), ('NO', True), ('  no  ', True),
    ('false', True), ('False', True), ('negative', True), ('0', True),
    (False, True), (0, True),
    ('yes', False), ('Yes', False), ('true', False), ('TRUE', False),
    ('positive', False), ('  yes  ', False), ('1', False), (True, False), (1, False),
])
def test_the_common_answer_spellings_keep_their_polarity(negated, positive, stored, shown):
    """A serializer that sends "No" instead of false must not flip the answer.

    `bool("False")` is True; inverting that would mark a patient who answered
    "no" as having the virus.
    """
    assert normalize_promop_row({**HEADER, positive: stored})[negated] is shown


def test_an_unreadable_answer_is_unknown_not_a_negative():
    row = normalize_promop_row({**HEADER, 'hiv_status': 'not sure'})

    assert row['no_hiv_status'] is None
    assert _boolean_answer('not sure') is None
    assert _boolean_answer(2) is None
    assert _boolean_answer(1.5) is None
    assert _boolean_answer('') is None


def test_normalizing_twice_does_not_flip_the_answer_back():
    row = normalize_promop_row({**HEADER, 'hiv_status': True})
    again = normalize_promop_row(dict(row))

    assert again['no_hiv_status'] is False


def test_a_missing_positive_column_leaves_the_negation_unknown():
    """The removed `no_*` columns are not resurrected as their old default.

    `PatientInfo` used to default to True ("No HIV: Yes") for every patient
    who had never answered. The matcher read that as a confirmed answer.
    """
    row = normalize_promop_row(dict(HEADER))

    assert 'no_hiv_status' not in row
    assert PatientInfo(**HEADER).no_hiv_status is None


@pytest.mark.parametrize('negated,positive', sorted(PROMOP_NEGATED_FIELDS.items()))
def test_the_record_boundary_carries_the_negation_through(negated, positive):
    pi = build_patient_info_from_promop_row({**HEADER, positive: True})

    assert getattr(pi, negated) is False
    # The derived attribute is what the trial row shows.
    assert PatientInfoAttributes(pi).get_value(negated) is False


@pytest.mark.parametrize('stored,verdict', [
    (True, 'not_matched'),
    (False, 'matched'),
    (None, 'unknown'),
])
def test_a_requirement_of_no_hiv_follows_the_positive_status(stored, verdict):
    pi = build_patient_info_from_promop_row({**HEADER, 'hiv_status': stored})
    trial = TrialFactory(disease='multiple myeloma', no_hiv_required=True)

    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('no_hiv_status') == verdict


@pytest.mark.parametrize('stored,verdict', [
    (True, 'not_matched'),
    (False, 'matched'),
    (None, 'unknown'),
])
def test_the_same_holds_for_hepatitis_c(stored, verdict):
    pi = build_patient_info_from_promop_row({**HEADER, 'hepatitis_c_status': stored})
    trial = TrialFactory(disease='multiple myeloma', no_hepatitis_c_required=True)

    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('no_hepatitis_c_status') == verdict
