"""Pre-existing condition codes a surface hands over are not discarded (#5497).

CB's PROMOP surface has no `PatientInfo` row, so it seeds the matcher's
`_pre_existing_condition_codes_cache` from `PatientRecord.preexisting_conditions`.
The reader used to consult that memo only inside the related-manager branch, so a
surface that provided codes was answered `[]`: a populated exclusion read as
`unknown` and the trial was never excluded.
"""
import types

import pytest

pytestmark = pytest.mark.django_db

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory


def _surface(**fields):
    """A PROMOP-shaped patient: CB's schema, values handed over, no related managers."""
    base = {
        '_meta': PatientInfo._meta,
        'disease': 'multiple myeloma',
        'prior_therapy': None,
        'no_pre_existing_conditions': None,
        '_pre_existing_condition_codes_cache': [],
    }
    base.update(fields)
    return types.SimpleNamespace(**base)


def test_codes_seeded_by_a_surface_are_read_back():
    attrs = PatientInfoAttributes(_surface(_pre_existing_condition_codes_cache=['infections']))

    assert attrs.get_value('pre_existing_condition_categories') == ['infections']


def test_an_empty_seed_stays_blank():
    attrs = PatientInfoAttributes(_surface())

    assert attrs.get_value('pre_existing_condition_categories') == []
    assert attrs.is_attr_blank('pre_existing_condition_categories') is True


def test_an_explicit_negative_answer_wins_over_the_seed():
    attrs = PatientInfoAttributes(_surface(no_pre_existing_conditions=True))

    assert attrs.get_value('pre_existing_condition_categories') == ['none']


def test_cb_native_patient_still_reads_its_related_manager():
    patient = PatientInfo(disease='multiple myeloma')
    attrs = PatientInfoAttributes(patient)

    assert attrs.get_value('pre_existing_condition_categories') == []


def test_a_populated_exclusion_is_enforced_not_unknown():
    trial = TrialFactory(disease='multiple myeloma',
                         pre_existing_conditions_excluded=['infections'])

    populated = UserToTrialAttrMatcher(
        trial, _surface(_pre_existing_condition_codes_cache=['infections']))
    empty = UserToTrialAttrMatcher(trial, _surface())

    assert populated.attr_match_status('pre_existing_condition_categories') == 'not_matched'
    assert empty.attr_match_status('pre_existing_condition_categories') == 'unknown'


def test_an_explicit_negative_answer_clears_the_exclusion():
    trial = TrialFactory(disease='multiple myeloma',
                         pre_existing_conditions_excluded=['infections'])
    matcher = UserToTrialAttrMatcher(trial, _surface(no_pre_existing_conditions=True))

    assert matcher.attr_match_status('pre_existing_condition_categories') == 'matched'
