"""MM ``Myeloma Related Disorders Excluded`` exclusion-only Named-ORs (#5521)."""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

FIELDS = (
    'mgus',
    'smoldering_multiple_myeloma',
    'waldenstrom_macroglobulinemia',
    'igm_multiple_myeloma',
    'al_amyloidosis',
    'poems_syndrome',
)


def _patient(**answers):
    pi = PatientInfo(disease='multiple myeloma', **answers)
    normalize_patient_info(pi)
    return pi


def _status(trial, pi):
    return UserToTrialAttrMatcher(trial=trial, patient_info=pi).attr_match_status('myeloma_related_disorders')


class TestMyelomaRelatedDisordersMatcher:
    @pytest.mark.django_db
    def test_present_excluded_condition_is_not_matched(self):
        trial = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        assert _status(trial, _patient(mgus=True)) == 'not_matched'

    @pytest.mark.django_db
    def test_confirmed_absent_is_matched(self):
        trial = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        assert _status(trial, _patient(mgus=False)) == 'matched'

    @pytest.mark.django_db
    def test_unknown_is_unknown(self):
        trial = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        assert _status(trial, _patient()) == 'unknown'

    @pytest.mark.django_db
    def test_yes_outside_the_trial_list_is_ignored(self):
        trial = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        assert _status(trial, _patient(mgus=False, al_amyloidosis=True)) == 'matched'

    @pytest.mark.django_db
    def test_present_wins_over_unknown(self):
        trial = TrialFactory(
            disease='multiple myeloma',
            myeloma_related_disorders_excluded=['mgus', 'al_amyloidosis'],
        )
        assert _status(trial, _patient(mgus=True)) == 'not_matched'

    @pytest.mark.django_db
    def test_no_trial_list_is_matched(self):
        trial = TrialFactory(disease='multiple myeloma')
        assert _status(trial, _patient(mgus=True)) == 'matched'


class TestMyelomaRelatedDisordersDerivation:
    def test_all_no_is_the_none_sentinel(self):
        pi = _patient(**{field: False for field in FIELDS})
        assert pi.myeloma_related_disorders == 'none'

    def test_all_unknown_is_blank(self):
        assert _patient().myeloma_related_disorders is None

    def test_partial_unknown_without_yes_is_blank(self):
        assert _patient(mgus=False).myeloma_related_disorders is None

    def test_yes_is_emitted_with_other_unknowns(self):
        assert _patient(mgus=True, poems_syndrome=True).myeloma_related_disorders == (
            'mgus,poems_syndrome'
        )


class TestMyelomaRelatedDisordersQueryset:
    @pytest.mark.django_db
    def test_excludes_a_trial_whose_list_contains_a_yes(self):
        t = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        ids = set(
            Trial.objects.all()
            .eligible_for_myeloma_related_disorders(['mgus'])
            .values_list('id', flat=True)
        )
        assert t.id not in ids

    @pytest.mark.django_db
    def test_keeps_a_trial_excluding_a_different_condition(self):
        t = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['poems_syndrome'])
        ids = set(
            Trial.objects.all()
            .eligible_for_myeloma_related_disorders(['mgus'])
            .values_list('id', flat=True)
        )
        assert t.id in ids

    @pytest.mark.django_db
    def test_unknown_keeps_the_trial_for_the_matcher(self):
        t = TrialFactory(disease='multiple myeloma', myeloma_related_disorders_excluded=['mgus'])
        ids = set(
            Trial.objects.all()
            .eligible_for_myeloma_related_disorders([])
            .values_list('id', flat=True)
        )
        assert t.id in ids
