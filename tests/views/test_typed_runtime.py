"""Real native/backend plumbing with synthetic, server-injected claims only."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from exact_matching.backend import ExactMatcher
from exact_matching.request_evaluation import RequestEvaluation, TrialPredicates
from tests.contracts.fixtures import context, trial as predicate
from tests.contracts.test_request_evaluation import predicates
from tests.factories import TrialFactory
from trials.api.graph_view import TrialsGraphViewSet
from trials.api.trials_views import TrialsViewSet
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.study_preferences import StudyPreferences

pytestmark = pytest.mark.django_db


@pytest.fixture
def runtime(settings):
    patient = PatientInfo(disease='breast cancer', patient_age=40)
    calls, batches, evaluations = [], [], []
    docs = {}

    def resolver(request, resolved_patient):
        calls.append(request)

        def load(identities):
            batches.append(identities)
            return {item.trial_id: TrialPredicates(item.row_sha256, docs.get(item.trial_id, predicates()))
                    for item in identities}

        evaluation = RequestEvaluation(context(), [], resolved_patient, load)
        evaluations.append(evaluation)
        return evaluation

    settings.EXACT_TYPED_MATCHING_ENABLED = True
    settings.EXACT_TYPED_MATCHING_RESOLVER = resolver

    def invoke(action='list', *, params='', pk=None, html=False):
        factory = APIRequestFactory()
        request = factory.get('/api/v1/trials/' + params,
                              HTTP_ACCEPT='text/html' if html else 'application/json')
        force_authenticate(request, user=SimpleNamespace(pk=1, is_authenticated=True, is_active=True))
        view = (TrialsGraphViewSet if action == 'graph' else TrialsViewSet).as_view({'get': action})
        with patch('trials.api.trials_views.resolve_patient_info', return_value=patient):
            return view(request, **({'pk': pk} if pk is not None else {}))

    return SimpleNamespace(invoke=invoke, patient=patient, calls=calls, batches=batches,
                           docs=docs, evaluations=evaluations, resolver=resolver)


def test_every_candidate_is_evaluated_before_page_count_and_rank(runtime):
    rejected = [TrialFactory(disease='breast cancer', age_low_limit=60) for _ in range(5)]
    best = TrialFactory(disease='breast cancer')
    gap = TrialFactory(disease='breast cancer')
    runtime.docs[gap.pk] = predicates(her2_statuses_required=predicate('her2_statuses_required'))
    response = runtime.invoke(params='?limit=1&type=eligible')
    assert response.status_code == 200, response.data
    assert response.data['itemsTotalCount'] == 2
    assert response.data['matchingCounts'] == {'eligible': 1, 'unavailable': 1}
    assert response.data['results'][0]['trialId'] == best.pk
    assert response.data['results'][0]['matchingType'] == 'eligible'
    assert len(runtime.calls) == 1
    assert len(runtime.batches) == 1 and len(runtime.batches[0]) == 7
    second = runtime.invoke(params='?limit=1&type=eligible&page=2')
    item, = second.data['results']
    assert item['trialId'] == gap.pk and item['matchingType'] == 'unavailable'
    assert item['matchScore'] is None and item['attributesToFillIn'] == []
    assert item['systemIssues']


def test_count_uses_same_textual_scope_as_list_and_search(runtime):
    keep = TrialFactory(disease='breast cancer', brief_title='Chosen text')
    TrialFactory(disease='breast cancer', brief_title='Other text')
    for action in ('list', 'search'):
        response = runtime.invoke(action, params='?search=Chosen')
        assert response.status_code == 200, response.data
        assert response.data['itemsTotalCount'] == 1
        assert response.data['results'][0]['trialId'] == keep.pk
    response = runtime.invoke('count', params='?search=Chosen')
    assert response.status_code == 200 and response.data['count'] == 1


@pytest.mark.parametrize('template', ['', 'all_attributes_in_groups'])
def test_detail_uses_same_final_result_and_never_legacy_templates(runtime, template):
    row = TrialFactory(disease='breast cancer', age_low_limit=60)
    runtime.docs[row.pk] = predicates(her2_statuses_required=predicate('her2_statuses_required'))
    with patch('trials.services.trial_details.trial_templates.TrialTemplates.details_and_group_names',
               side_effect=AssertionError('legacy templates')), patch(
               'trials.services.trial_match_explainer.TrialMatchExplainer.explain',
               side_effect=AssertionError('legacy explainer')):
        response = runtime.invoke('retrieve', pk=row.pk, params='?view=' + template + '&explain=true')
    assert response.status_code == 200, response.data
    assert (response.data['matchingType'], response.data['matchScore']) == ('not_eligible', 0)
    assert response.data['systemIssues'] and response.data['matchReasons']
    rows = response.data['details']['trialEligibilityAttributes']
    her2 = next(item for item in rows if item['ufield'] == 'her2Status')
    assert her2['matchingType'] == 'unavailable'
    assert her2['uvalue'] is None  # no guessed legacy code or reverse conversion
    assert len(runtime.calls) == len(runtime.batches) == 1


def test_computed_verdict_is_readonly_and_existing_source_metadata_is_exposed(runtime):
    row = TrialFactory(disease='breast cancer')
    response = runtime.invoke('retrieve', pk=row.pk)
    assert response.status_code == 200, response.data
    bmi = next(item for item in response.data['details']['trialEligibilityAttributes'] if item['ufield'] == 'bmi')
    assert bmi['ureadonly']
    assert {item['name'] for item in bmi['subform_details']} == {'weight', 'height'}
    # Trial field hr_statuses_required differs from patient attribute hr_status;
    # metadata falls back to the field-keyed source config where appropriate.
    hr = next(item for item in response.data['details']['trialEligibilityAttributes'] if item['ufield'] == 'hrStatus')
    assert {item['name'] for item in hr['subform_details']} == {'estrogenReceptorStatus', 'progesteroneReceptorStatus'}


def test_graph_slices_after_evaluation_and_system_gaps_have_separate_bucket(runtime):
    TrialFactory(disease='breast cancer', age_low_limit=60)
    best = TrialFactory(disease='breast cancer')
    gap = TrialFactory(disease='breast cancer')
    runtime.docs[gap.pk] = predicates(her2_statuses_required=predicate('her2_statuses_required'))
    response = runtime.invoke('graph', params='?n=1&sort=matchScore')
    assert response.status_code == 200, response.data
    assert [row['trialId'] for row in response.data['trials']] == [best.pk]
    response = runtime.invoke('graph', params='?n=10&sort=matchScore')
    item = next(item for item in response.data['trials'] if item['trialId'] == gap.pk)
    assert item['matchingType'] == 'unavailable' and item['matchScore'] is None
    assert item['match']['systemUnavailable']
    assert not any(row['patientField'] == 'her2Status' for row in item['match']['missing'])


def test_bounded_candidates_fail_explicitly_not_as_a_partial_page(runtime):
    TrialFactory(disease='breast cancer')
    TrialFactory(disease='breast cancer')
    original = runtime.resolver

    def limited(request, patient):
        evaluation = original(request, patient)
        evaluation._max_trials = 1
        return evaluation

    with override_settings(EXACT_TYPED_MATCHING_RESOLVER=limited):
        response = runtime.invoke(params='?limit=1')
    assert response.status_code == 503 and response.data['code'] == 'candidate_budget'
    assert not runtime.batches


@pytest.mark.parametrize('resolver', [None, lambda request, patient: None, 'missing.resolver.path'])
def test_enabled_without_trusted_server_context_never_falls_back(runtime, resolver):
    TrialFactory()
    with override_settings(EXACT_TYPED_MATCHING_RESOLVER=resolver):
        response = runtime.invoke()
    assert response.status_code == 503


def test_client_claims_cannot_activate_typed_matching_when_disabled(runtime):
    TrialFactory(disease='breast cancer')
    with override_settings(EXACT_TYPED_MATCHING_ENABLED=False):
        response = runtime.invoke(params='?typed_matching=true&context_id=synthetic')
    assert response.status_code == 200
    assert not runtime.calls and not runtime.batches
    assert 'schema' not in response.data['results'][0]


def test_packaged_backend_agrees_with_native_patient_surface(runtime):
    row = TrialFactory(disease='breast cancer')
    evaluation = runtime.resolver(None, runtime.patient)
    patient = SimpleNamespace(as_patient_info=lambda: runtime.patient, geo_point=None)
    prefs = SimpleNamespace(query_params={}, study_info=StudyPreferences(), search_type=None, recruitment_status=None)
    backend = ExactMatcher(evaluation=evaluation)
    finalized = backend.search(Trial.objects.all(), patient, prefs)
    assert len(finalized) == 1
    result = evaluation.result_for(row.pk)
    native = runtime.invoke().data['results'][0]
    assert (result.score, result.status) == (native['matchScore'], native['matchingType'])
    assert backend.match_status(finalized[0], patient) == result.status
    assert backend.attrs_to_fill_in(finalized[0], patient, {}) == list(result.missing_attributes)


def test_query_count_does_not_grow_per_trial(runtime):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    TrialFactory(disease='breast cancer')
    with CaptureQueriesContext(connection) as first:
        assert runtime.invoke().status_code == 200
    for _ in range(9):
        TrialFactory(disease='breast cancer')
    with CaptureQueriesContext(connection) as second:
        assert runtime.invoke().status_code == 200
    assert len(second) == len(first)


@pytest.mark.parametrize('action', ['search', 'graph'])
def test_distance_sort_without_patient_coordinates_is_null_not_500(runtime, action):
    row = TrialFactory(disease='breast cancer')
    response = runtime.invoke(action, params='?sort=distance')
    assert response.status_code == 200, response.data
    rows = response.data['trials' if action == 'graph' else 'results']
    assert rows[0]['trialId'] == row.pk


def test_backend_selection_uses_same_copied_patient_as_evaluation(runtime):
    breast = TrialFactory(disease='breast cancer')
    TrialFactory(disease='mantle cell lymphoma')
    evaluation = runtime.resolver(None, runtime.patient)
    runtime.patient.disease = 'mantle cell lymphoma'
    patient = SimpleNamespace(as_patient_info=lambda: runtime.patient, geo_point=None)
    prefs = SimpleNamespace(query_params={}, study_info=StudyPreferences(), search_type='all', recruitment_status=None)
    finalized = ExactMatcher(evaluation=evaluation).search(Trial.objects.all(), patient, prefs)
    assert [row.pk for row in finalized] == [breast.pk]
    assert evaluation.result_for(breast.pk).status == 'eligible'


def test_backend_distance_and_goodness_use_snapshot_geography(runtime):
    from django.contrib.gis.geos import Point
    from exact_matching.querysets.trial import TrialQuerySet
    TrialFactory(disease='breast cancer')
    evaluation = runtime.resolver(None, runtime.patient)
    external = Point(-100, 30)
    patient = SimpleNamespace(as_patient_info=lambda: runtime.patient, geo_point=external)
    prefs = SimpleNamespace(query_params={}, study_info=StudyPreferences(), search_type='all', recruitment_status=None)
    seen = []
    original = TrialQuerySet.with_goodness_score_optimized

    def capture(self, *args, **kwargs):
        seen.append(kwargs['geo_point'])
        return original(self, *args, **kwargs)

    with patch.object(TrialQuerySet, 'with_goodness_score_optimized', capture):
        ExactMatcher(evaluation=evaluation).search(Trial.objects.all(), patient, prefs)
    assert seen == [None]


def test_cached_context_cannot_be_rebound_to_another_patient(runtime):
    from trials.services.matching.typed_runtime import resolve_evaluation, TypedMatchingUnavailable
    request = APIRequestFactory().get('/api/v1/trials/')
    resolve_evaluation(request, runtime.patient)
    with pytest.raises(TypedMatchingUnavailable):
        resolve_evaluation(request, PatientInfo(disease='mantle cell lymphoma'))
    with pytest.raises(TypedMatchingUnavailable):
        resolve_evaluation(request, runtime.patient)  # Request stays failed, no context switching back.
    assert len(runtime.calls) == 1


@pytest.mark.parametrize('action', ['list', 'search'])
def test_typed_list_preserves_geography_keys_without_reading_legacy_stage(runtime, action):
    from django.contrib.gis.geos import Point
    from tests.factories import LocationFactory
    from trials.models import LocationTrial
    row = TrialFactory(disease='breast cancer', stages=['legacy-code-not-a-typed-display'])
    location = LocationFactory(geo_point=Point(-96.8, 32.8))
    LocationTrial.objects.create(trial=row, location=location, recruitment_status='RECRUITING')
    response = runtime.invoke(action)
    assert response.status_code == 200, response.data
    item, = response.data['results']
    assert item['stage'] is None  # No unreviewed OMOP -> legacy display conversion.
    assert item['closestLocationGeoPoint'] == {'longitude': -96.8, 'latitude': 32.8}
