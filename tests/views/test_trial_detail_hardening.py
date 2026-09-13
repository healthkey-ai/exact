"""
#201 — trial detail (retrieve) computes score+status in a single matcher pass
(match_score_and_status), with no memoization (re-reflects current patient state),
and the explainer reuses the serializer's matcher instance.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.trial_match_explainer import TrialMatchExplainer
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('trial_kwargs', [
    {},                                             # bare -> partial/potential
    {'disease_progression_active_required': True},  # patient blank -> unknown
    {'age_low_limit': 18},                          # conflict (patient age 10)
])
def test_match_score_and_status_matches_separate_methods(trial_kwargs):
    pi = PatientInfo(disease='multiple myeloma', patient_age=10)
    trial = TrialFactory(disease='multiple myeloma', **trial_kwargs)
    m = UserToTrialAttrMatcher(trial, pi)
    # single-pass result must equal calling the two methods separately
    assert m.match_score_and_status() == (m.trial_match_score(), m.trial_match_status())


def test_match_score_and_status_does_not_cache_patient_state():
    # No memoization: re-evaluates against the current patient on each call.
    trial = TrialFactory(disease='multiple myeloma', disease_progression_active_required=True)
    pi = PatientInfo(disease='multiple myeloma')
    m = UserToTrialAttrMatcher(trial, pi)
    pi.progression = ''
    _, status_blank = m.match_score_and_status()
    pi.progression = 'smoldering'
    _, status_smoldering = m.match_score_and_status()
    assert status_blank != status_smoldering  # reflects the mutation


def test_explainer_reuses_supplied_matcher():
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma')
    m = UserToTrialAttrMatcher(trial, pi)
    ex = TrialMatchExplainer(trial, pi, matcher=m)
    assert ex._matcher is m
