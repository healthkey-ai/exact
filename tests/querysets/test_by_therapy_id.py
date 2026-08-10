"""Tests for TrialQuerySet.by_therapy_id and ?therapy_id= StudyPreferences parsing."""

import pytest

from trials.models import Trial
from trials.querysets.trial import TrialQuerySet
from trials.services.study_preferences import StudyPreferences, study_preferences_from_query_params
from tests.factories import TrialFactory


@pytest.mark.django_db
class TestByTherapyId:
    def test_returns_trial_with_matching_concept_id(self):
        t = TrialFactory(omop_intervention_concept_ids=[19140573])
        TrialFactory(omop_intervention_concept_ids=[])

        qs = Trial.objects.by_therapy_id([19140573])
        assert t in qs
        assert qs.count() == 1

    def test_no_op_when_list_empty(self):
        TrialFactory(omop_intervention_concept_ids=[19140573])
        total = Trial.objects.count()

        qs = Trial.objects.by_therapy_id([])
        assert qs.count() == total

    def test_or_semantics_across_multiple_ids(self):
        t1 = TrialFactory(omop_intervention_concept_ids=[19140573])
        t2 = TrialFactory(omop_intervention_concept_ids=[19136050])
        TrialFactory(omop_intervention_concept_ids=[99999999])

        qs = Trial.objects.by_therapy_id([19140573, 19136050])
        assert set(qs) == {t1, t2}

    def test_does_not_match_trial_with_empty_field(self):
        TrialFactory(omop_intervention_concept_ids=[])
        qs = Trial.objects.by_therapy_id([19140573])
        assert qs.count() == 0

    def test_ignores_non_integer_therapy_id_in_query_params(self):
        prefs = study_preferences_from_query_params({'therapy_id': 'notanumber'})
        assert prefs.therapy_id == []

    def test_parses_single_therapy_id_from_query_params(self):
        class FakeParams(dict):
            def getlist(self, key):
                v = self.get(key)
                return [v] if v is not None else []

        prefs = study_preferences_from_query_params(FakeParams({'therapy_id': '19140573'}))
        assert prefs.therapy_id == [19140573]

    def test_parses_multiple_therapy_ids_from_query_params(self):
        class MultiParams:
            def getlist(self, key):
                if key == 'therapy_id':
                    return ['19140573', '19136050']
                return []
            def get(self, key, default=None):
                return default

        prefs = study_preferences_from_query_params(MultiParams())
        assert prefs.therapy_id == [19140573, 19136050]


@pytest.mark.django_db
class TestTherapyIdHostAgnostic:
    """R2: ?therapy_id= is an EXACT-native OMOP search param; a downstream host whose
    study_info has no therapy_id field (CB's StudyInfo model) must still be able to run
    the shared filtered_trials / filter_by_study_info body. The package reads therapy_id
    via getattr(..., None), so it no-ops on such a host instead of AttributeError-ing.
    Modelled by deleting therapy_id from a real StudyPreferences instance.
    """

    def test_filter_by_study_info_tolerates_study_info_without_therapy_id(self):
        TrialFactory(omop_intervention_concept_ids=[19140573])
        prefs = StudyPreferences()
        del prefs.therapy_id  # a host (CB StudyInfo model) that has no therapy_id field
        qs, _ = Trial.objects.all().filter_by_study_info(prefs)  # must NOT AttributeError
        assert qs.count() >= 1  # read as None -> by_therapy_id no-op

    def test_filtered_trials_all_path_tolerates_missing_therapy_id(self):
        TrialFactory()
        prefs = StudyPreferences()
        del prefs.therapy_id
        qs, _ = Trial.objects.all().filtered_trials(
            search_options={}, study_info=prefs, patient_info=None, search_type='all')
        assert qs is not None  # ran through by_therapy_id + filter_for_admin without raising

    def test_therapy_id_still_filters_when_present(self):
        t = TrialFactory(omop_intervention_concept_ids=[19140573])
        TrialFactory(omop_intervention_concept_ids=[])
        prefs = StudyPreferences(therapy_id=[19140573])
        qs, _ = Trial.objects.all().filter_by_study_info(prefs)
        assert t in qs and qs.count() == 1  # EXACT behavior preserved


@pytest.mark.django_db
class TestFilterForAdminTrialPurpose:
    """R2: filter_for_admin gained an apply_trial_purpose flag so favorites / my_trials
    (hand-picked lists) skip the trial-purpose filter — matches CB's filtered_trials,
    which passes apply_trial_purpose=False for those. Without it the drain would drop
    saved trials with a non-matching purpose.
    """

    def _spy_by_trial_purpose(self, monkeypatch):
        calls = []
        orig = TrialQuerySet.by_trial_purpose
        monkeypatch.setattr(TrialQuerySet, 'by_trial_purpose',
                            lambda self, tp: (calls.append(tp), orig(self, tp))[1])
        return calls

    def test_apply_trial_purpose_true_applies_filter(self, monkeypatch):
        calls = self._spy_by_trial_purpose(monkeypatch)
        Trial.objects.all().filter_for_admin(
            StudyPreferences(trial_purpose='treatment'), None, apply_trial_purpose=True)
        assert calls == ['treatment']

    def test_apply_trial_purpose_false_skips_filter(self, monkeypatch):
        calls = self._spy_by_trial_purpose(monkeypatch)
        Trial.objects.all().filter_for_admin(
            StudyPreferences(trial_purpose='treatment'), None, apply_trial_purpose=False)
        assert calls == []  # skipped for favorites / my_trials

    def test_default_applies_trial_purpose(self, monkeypatch):
        calls = self._spy_by_trial_purpose(monkeypatch)
        Trial.objects.all().filter_for_admin(StudyPreferences(trial_purpose='treatment'), None)
        assert calls == ['treatment']  # default True
