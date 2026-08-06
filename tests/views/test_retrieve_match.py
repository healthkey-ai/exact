"""
get_trial_by_id (retrieve / TrialDetailsSerializer) reports the per-patient match.

Previously the detail endpoint returned trial info + goodnessScore but no match
(matchScore/matchingType/matchReasons were null). This surfaces the match via the
**conflict-aware Python matcher** (trial_match_score / trial_match_status), NOT the
list/search `match_score` annotation — retrieve skips filtered_trials, so a
conflicting trial must be reported not_eligible, not mislabeled eligible by a
filled-vs-null completeness score.
"""
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from trials.api.trials_serializers import TrialDetailsSerializer
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

SERVICE_TOKEN = "test-service-token-value"


def _detail_context(patient_info, explain=True):
    return {
        'patient_info': patient_info,
        'explain': explain,
        'counts': {},
        'template': None,
        'distance_units': 'km',
        'recruitment_status': None,
        'search_type': None,
    }


def test_detail_reports_match_for_patient():
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma')
    data = TrialDetailsSerializer(trial, context=_detail_context(pi)).data

    assert data['matchScore'] is not None
    assert data['matchingType'] in ('eligible', 'potential', 'not_eligible')
    assert isinstance(data['matchReasons'], list) and data['matchReasons']


def test_detail_conflicting_patient_is_not_eligible():
    # The key correctness fix: retrieve has no filtered_trials, so a trial the
    # patient conflicts with must come back not_eligible / score 0 — NOT eligible.
    pi = PatientInfo(disease='multiple myeloma', patient_age=10)
    trial = TrialFactory(disease='multiple myeloma', age_low_limit=18)
    data = TrialDetailsSerializer(trial, context=_detail_context(pi)).data

    assert data['matchingType'] == 'not_eligible'
    assert data['matchScore'] == 0


def test_detail_no_explain_leaves_matchreasons_none():
    pi = PatientInfo(disease='multiple myeloma')
    trial = TrialFactory(disease='multiple myeloma')
    data = TrialDetailsSerializer(trial, context=_detail_context(pi, explain=False)).data

    assert data['matchReasons'] is None
    assert data['matchingType'] in ('eligible', 'potential', 'not_eligible')


@override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
def test_retrieve_endpoint_reports_match_via_view():
    """End-to-end through the DRF view: ?person_id resolves the patient (mocked
    PROMOP fetch), and the detail response carries the match fields."""
    trial = TrialFactory(disease='multiple myeloma')
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")
    with patch('trials.services.patient_info.promop_client.PromopClient') as MockClient:
        MockClient.return_value.fetch_patient.return_value = {
            'person_id': 9001, 'disease': 'multiple myeloma',
        }
        resp = client.get(f'/trials/{trial.id}/?person_id=9001&explain=true')

    assert resp.status_code == 200
    body = resp.json()
    assert body['matchScore'] is not None
    assert body['matchingType'] in ('eligible', 'potential', 'not_eligible')
    assert isinstance(body['matchReasons'], list)


@pytest.mark.parametrize('trial_kwargs', [
    {'age_low_limit': 18},                                    # demographic conflict
    {'consent_capability_required': True, 'stages': ['I'],    # boolean/general criteria
     'has_stages': True},
    {'omop_therapy_components_excluded': [1511646],           # OMOP therapy criteria
     'omop_therapies_required': [12345]},
])
def test_detail_no_patient_renders_without_match(db, trial_kwargs):
    """#318: with no patient context the detail view renders the trial's criteria
    but reports NO match (matchScore/matchingType null) — it must neither 500 on a
    None-patient deref (any criterion family) nor fabricate a favourable match from
    default patient state. A conflicting criterion must not read as matched."""
    trial = TrialFactory(disease='multiple myeloma', **trial_kwargs)
    data = TrialDetailsSerializer(trial, context=_detail_context(None)).data

    assert data['matchScore'] is None
    assert data['matchingType'] is None
    # details is a dict of group -> list[field dict]; flatten to field dicts.
    fields = [f for v in data['details'].values() if isinstance(v, list)
              for f in v if isinstance(f, dict)]
    assert fields, 'criteria should still render without a patient'
    # any patient-relative (ufield) criterion that renders must read 'unknown',
    # never a fabricated matched/not-matched from default patient state.
    patient_relative = [f for f in fields if f.get('ufield')]
    assert all(f.get('matchingType') == 'unknown' for f in patient_relative), \
        {f['name']: f.get('matchingType') for f in patient_relative}


def test_detail_no_patient_therapy_criteria_render_unknown(db):
    """#318: OMOP therapy criteria (ufield) render without a patient and read
    'unknown' — the removed therapies() early-return would otherwise hide them."""
    trial = TrialFactory(disease='multiple myeloma',
                         omop_therapy_components_excluded=[1511646],
                         omop_therapies_required=[12345])
    data = TrialDetailsSerializer(trial, context=_detail_context(None)).data
    fields = [f for v in data['details'].values() if isinstance(v, list)
              for f in v if isinstance(f, dict)]
    therapy = [f for f in fields if f.get('ufield') and 'omopConcepts' not in f
               and f.get('matchingType')]
    assert therapy, 'therapy criteria should render without a patient'
    assert all(f['matchingType'] == 'unknown' for f in therapy), \
        {f['name']: f['matchingType'] for f in therapy}


@override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
def test_retrieve_endpoint_no_patient_returns_200():
    """End-to-end: GET /trials/<id>/ with auth but NO patient (no person_id, no
    inline) returns 200 with a null match, not 500 (#318)."""
    trial = TrialFactory(disease='multiple myeloma')
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")
    resp = client.get(f'/trials/{trial.id}/')
    assert resp.status_code == 200
    body = resp.json()
    assert body['matchScore'] is None
    assert body['matchingType'] is None
