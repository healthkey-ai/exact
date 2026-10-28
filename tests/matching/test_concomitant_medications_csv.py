"""A comma-separated concomitant-medication answer is several items (#5554).

CB stores the multi-select as text; the SQL filter splits it before comparing.
The per-trial matcher compared the whole string as one item, so a trial excluding
one of the selected drugs still looked `matched`.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory


@pytest.mark.django_db
@pytest.mark.parametrize('excluded, expected', [
    (['anticoagulants'], 'not_matched'),
    (['foo'], 'not_matched'),
    (['corticosteroids'], 'matched'),
])
def test_csv_items_are_compared_individually(excluded, expected):
    patient = PatientInfo(disease='multiple myeloma',
                          concomitant_medications='anticoagulants, foo')
    trial = TrialFactory(concomitant_medications_excluded=excluded)

    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'concomitant_medications') == expected
