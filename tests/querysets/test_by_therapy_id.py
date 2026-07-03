"""Tests for TrialQuerySet.by_therapy_id and ?therapy_id= StudyPreferences parsing."""

import pytest

from trials.models import Trial
from trials.querysets.trial import TrialQuerySet
from trials.services.study_preferences import study_preferences_from_query_params
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
