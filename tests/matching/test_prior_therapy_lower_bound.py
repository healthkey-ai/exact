"""A recorded line count is a lower bound until the patient confirms completeness (#5483).

`therapy_history_complete` lives on the patient surface: CancerBot's own
`PatientInfo` has no such attribute (its explicitly entered count stays exact),
while a PROMOP record carries it and is NULL until confirmed.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def _patient(username_suffix, **fields):
    # `PatientInfo` has no `therapy_history_complete` column: the flag belongs to
    # the PROMOP surface. Setting it on the instance is exactly what that surface
    # exposes to the matcher, so it exercises the same branch.
    values = {'disease': 'multiple myeloma', 'patient_age': 65,
              'prior_therapy': 'One line'}
    values.update(fields)
    return PatientInfo(**values)


def _status(patient, **trial_fields):
    trial = TrialFactory(disease='multiple myeloma', **trial_fields)
    return UserToTrialAttrMatcher(trial, patient).trial_match_status()


def test_unconfirmed_history_does_not_exclude_on_a_minimum():
    patient = _patient('lb_min')
    patient.therapy_history_complete = None
    assert _status(patient, therapy_lines_count_min=2) == 'potential'


def test_unconfirmed_history_does_not_claim_eligibility_on_a_maximum():
    patient = _patient('lb_max')
    patient.therapy_history_complete = None
    assert _status(patient, therapy_lines_count_max=1) == 'potential'


def test_confirmed_history_still_compares_exactly():
    patient = _patient('lb_complete')
    patient.therapy_history_complete = True
    assert _status(patient, therapy_lines_count_min=2) == 'not_eligible'
    assert _status(patient, therapy_lines_count_max=1) == 'eligible'


def test_a_surface_without_the_flag_keeps_exact_behaviour():
    # CB-native `PatientInfo` has no completeness concept; its answer is exact.
    assert _status(_patient('lb_native'), therapy_lines_count_min=2) == 'not_eligible'


def test_provably_exceeding_a_maximum_still_excludes():
    patient = _patient('lb_over_max', prior_therapy='Two lines')
    patient.therapy_history_complete = None
    assert _status(patient, therapy_lines_count_max=1) == 'not_eligible'
