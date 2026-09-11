"""Integration tests for `POST /trials/{id}/match/` (detail alias).

The detail `match` action is a POST alias for `retrieve` that exists so a
host can carry `patient_info` in the request body when loading the trial
detail page (GET-with-body is forbidden by the Fetch spec and silently
dropped by axios's XHR adapter). It rebinds `self.action = 'retrieve'` so
the eligibility table (`details.trialEligibilityAttributes`, built from the
patient context) is populated. These tests lock the HTTP surface — auth,
detail response shape, and that the body's `patient_info` reaches the
serializer.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='detail-match-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestTrialDetailMatchPost:
    def test_unauthenticated_post_returns_401(self):
        trial = TrialFactory(disease='Multiple Myeloma')
        client = APIClient()
        response = client.post(f'/trials/{trial.id}/match/', {}, format='json')
        assert response.status_code == 401

    def test_authed_post_returns_detail_for_the_trial(self, authed_client):
        trial = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert response.data['trialId'] == trial.id
        assert 'briefTitle' in response.data

    def test_response_carries_eligibility_table(self, authed_client):
        """Headline: the detail response includes the grouped `details` with the
        per-patient eligibility table. This is what the detail action exists to
        deliver (the plain list endpoint doesn't carry it).
        """
        trial = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert 'details' in response.data
        assert 'trialEligibilityAttributes' in response.data['details']
        assert 'groupNames' in response.data
        for required in ('matchScore', 'goodnessScore'):
            assert required in response.data, f'missing field {required}'
        # `match_score` is annotated only on the list/retrieve queryset path;
        # a regression that drops it from `retrieve` makes the detail page show
        # "N/A" for every trial's Matching Score.
        assert response.data['matchScore'] is not None, (
            'matchScore must be annotated on the detail path, not null'
        )

    def test_response_carries_the_high_risk_mcl_breakdown(self, authed_client):
        """The federated detail page renders a panel from this key, so its
        shape is a contract now rather than an internal detail.

        Per-criterion status is reported in ELIGIBILITY terms — on an excluded
        criterion, confirmed absence is `matched`. The panel inverts the
        phrasing for the reader; what must not drift is which side reports
        which.
        """
        trial = TrialFactory(
            disease='mantle cell lymphoma',
            high_risk_mcl_criteria_required=['tp53_mutation', 'del17p'],
            high_risk_mcl_criteria_excluded=['blastoid'],
            high_risk_mcl_criteria_min_count=1,
        )
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {
                'disease': 'mantle cell lymphoma',
                'molecular_markers': 'tp53Mutation',
                'morphologic_variant': 'classic',
            }},
            format='json',
        )
        assert response.status_code == 200
        breakdown = response.data['highRiskMclCriteriaBreakdown']
        assert breakdown['aggregate'] == 'matched'
        assert breakdown['minCount'] == 1
        assert breakdown['matchedCount'] == 1
        assert breakdown['required'] == [
            {'code': 'tp53_mutation', 'status': 'matched'},
            # `unknown`, not `not_matched`: this payload carries no
            # cytogenetics at all, so del(17p) was never ruled out — it was
            # never looked at. The panel says so rather than showing an
            # absence the patient could not have known about.
            {'code': 'del17p', 'status': 'unknown'},
        ]
        # Clear of the exclusion, which is the good outcome and reads `matched`.
        assert breakdown['excluded'] == [{'code': 'blastoid', 'status': 'matched'}]
        assert breakdown['sufficientAny'] == []

    def test_no_breakdown_for_a_trial_that_gates_on_no_criteria(self, authed_client):
        """Null, not an empty skeleton — the panel keys off its absence, and an
        empty one would render a heading over nothing for every MM trial."""
        trial = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert response.data['highRiskMclCriteriaBreakdown'] is None
