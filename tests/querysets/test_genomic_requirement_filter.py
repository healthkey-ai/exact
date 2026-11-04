"""Genomic-requirement eligibility filter (#5675).

A trial requiring BRCA1/2 or ESR1 is not necessarily typed as genomic, so the
trial-type filter answered "Genomic / Molecular Characterization" with nothing.
This filter narrows on the eligibility lists instead, independently of type.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from rest_framework.test import APIRequestFactory, force_authenticate

from tests.factories import TrialFactory, TrialTypeFactory
from trials.api.trials_views import TrialsViewSet
from trials.models import Trial
from trials.services.study_preferences import (
    StudyPreferences,
    study_preferences_from_query_params,
)

pytestmark = pytest.mark.django_db


def search(params='', action='search'):
    request = APIRequestFactory().get(f'/api/v1/trials/{params}')
    force_authenticate(request, user=SimpleNamespace(
        pk=1, is_authenticated=True, is_active=True))
    view = TrialsViewSet.as_view({'get': action})
    with patch('trials.api.trials_views.resolve_patient_info', return_value=None):
        return view(request)


class TestTheParamIsParsed:
    def test_required_reaches_the_study_preferences(self):
        prefs = study_preferences_from_query_params(
            {'genomicRequirement': 'required'})
        assert prefs.genomic_requirement == 'required'

    @pytest.mark.parametrize('params', [{}, {'genomicRequirement': ''}])
    def test_absent_or_empty_is_no_filter(self, params):
        assert study_preferences_from_query_params(params).genomic_requirement is None


class TestTheQuerysetFilter:
    def _trials(self):
        mutation = TrialFactory(
            mutation_genes_required=['brca1', 'brca2'],
            molecular_markers_required=[],
            cytogenic_markers_required=[],
        )
        molecular = TrialFactory(
            mutation_genes_required=[],
            molecular_markers_required=['ESR1'],
            cytogenic_markers_required=[],
        )
        cytogenic = TrialFactory(
            mutation_genes_required=[],
            molecular_markers_required=[],
            cytogenic_markers_required=['t(4;14)'],
        )
        none = TrialFactory(
            mutation_genes_required=[],
            molecular_markers_required=[],
            cytogenic_markers_required=[],
        )
        return mutation, molecular, cytogenic, none

    def test_each_list_is_enough(self):
        mutation, molecular, cytogenic, none = self._trials()
        kept = set(Trial.objects.all().by_genomic_requirement('required')
                   .values_list('pk', flat=True))
        assert kept == {mutation.pk, molecular.pk, cytogenic.pk}
        assert none.pk not in kept

    @pytest.mark.parametrize('value', [None, '', 'any'])
    def test_an_unset_or_unknown_value_narrows_nothing(self, value):
        self._trials()
        assert Trial.objects.all().by_genomic_requirement(value).count() == 4

    def test_a_trial_without_a_type_is_still_found(self):
        """The 81 breast-cancer trials the trial-type filter could not reach."""
        row = TrialFactory(
            trial_type=None,
            mutation_genes_required=['brca1'],
            brief_title='',
        )
        kept = Trial.objects.all().by_genomic_requirement('required')
        assert list(kept.values_list('pk', flat=True)) == [row.pk]

    @pytest.mark.parametrize('stored', ['null', '{}', 'false', '0', '" "'])
    def test_a_non_array_value_is_not_a_requirement(self, stored):
        """NOT NULL does not forbid JSON null, objects or scalars, and
        `field != '[]'` would have counted every one of them as a match."""
        row = TrialFactory(
            mutation_genes_required=[],
            molecular_markers_required=[],
            cytogenic_markers_required=[],
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE trials_trial SET mutation_genes_required = %s::jsonb "
                "WHERE id = %s",
                [stored, row.pk],
            )
        assert not Trial.objects.all().by_genomic_requirement('required').filter(
            pk=row.pk).exists()

    def test_combines_with_trial_type_as_an_intersection(self):
        trial_type = TrialTypeFactory(code='qa_genomic_type')
        typed = TrialFactory(
            mutation_genes_required=['brca1'], trial_type=trial_type)
        TrialFactory(mutation_genes_required=['brca1'], trial_type=None)
        prefs = StudyPreferences(
            trial_type=trial_type.code, genomic_requirement='required')
        query, _ = Trial.objects.all().filter_by_study_info(prefs)
        assert list(query.values_list('pk', flat=True)) == [typed.pk]

    def test_the_standard_search_path_applies_it(self):
        mutation, molecular, cytogenic, _ = self._trials()
        prefs = StudyPreferences(genomic_requirement='required')
        query, _ = Trial.objects.all().filter_by_study_info(prefs)
        assert set(query.values_list('pk', flat=True)) == {
            mutation.pk, molecular.pk, cytogenic.pk,
        }

    def test_the_type_all_path_applies_it(self):
        """`?type=all` must not ignore a filter the reader asked for."""
        mutation, molecular, cytogenic, _ = self._trials()
        prefs = StudyPreferences(genomic_requirement='required')
        query, _ = Trial.objects.all().filter_for_admin(prefs, None)
        assert set(query.values_list('pk', flat=True)) == {
            mutation.pk, molecular.pk, cytogenic.pk,
        }

    def test_traces_report_the_drop(self):
        self._trials()
        prefs = StudyPreferences(genomic_requirement='required')
        _, traces = Trial.objects.all().filter_by_study_info(prefs, add_traces=True)
        trace = next(t for t in traces if t['attr'] == 'study_info.genomic_requirement')
        assert trace['val'] == 'required'
        assert trace['records'] == 3
        assert trace['dropped'] == 1


class TestTheApiAnswersConsistently:
    def test_search_and_count_agree(self):
        mutation = TrialFactory(
            mutation_genes_required=['brca1'], brief_title='')
        molecular = TrialFactory(
            mutation_genes_required=[], molecular_markers_required=['ESR1'],
            trial_type=None)
        TrialFactory(mutation_genes_required=[])
        response = search('?genomicRequirement=required')
        assert response.status_code == 200, response.data
        assert response.data['itemsTotalCount'] == 2
        assert {item['trialId'] for item in response.data['results']} == {
            mutation.pk, molecular.pk,
        }
        count = search('?genomicRequirement=required', action='count')
        assert count.status_code == 200
        assert count.data['count'] == 2

    def test_pagination_carries_the_same_total(self):
        rows = [
            TrialFactory(mutation_genes_required=['brca1']) for _ in range(3)
        ]
        TrialFactory(mutation_genes_required=[])
        first = search('?genomicRequirement=required&limit=2')
        second = search('?genomicRequirement=required&limit=2&page=2')
        assert first.status_code == second.status_code == 200
        assert first.data['itemsTotalCount'] == 3
        assert second.data['itemsTotalCount'] == 3
        seen = [item['trialId'] for page in (first, second) for item in page.data['results']]
        assert sorted(seen) == sorted(row.pk for row in rows)

    def test_the_param_does_not_change_an_unfiltered_search(self):
        TrialFactory(mutation_genes_required=['brca1'])
        TrialFactory(mutation_genes_required=[])
        response = search('')
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 2
