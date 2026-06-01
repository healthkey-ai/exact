"""Integration tests for `POST /trials/match/` (PR #121).

The action is a thin POST alias for the list endpoint that exists so
the federation harness can carry `patient_info` in the request body
(GET-with-body is forbidden by the Fetch spec and silently dropped by
axios's XHR adapter). These tests lock the HTTP surface — auth,
response shape, disease-scoping — so a regression that breaks the
`self.action = 'list'` rebind or the routing wiring would fail CI.
"""
import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = User.objects.get_or_create(username='match-tester')
    user.set_password('pw')
    user.save()
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestTrialsMatchPost:
    def test_unauthenticated_post_returns_401(self):
        client = APIClient()
        response = client.post('/trials/match/', {}, format='json')
        assert response.status_code == 401

    def test_authed_post_returns_200(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            '/trials/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert 'results' in response.data
        assert 'itemsTotalCount' in response.data

    def test_inline_patient_info_scopes_to_disease(self, authed_client):
        """Headline: the POST body's `patient_info` reaches the matcher
        and the response is disease-scoped. This is the bug PR #121
        exists to fix — pre-PR, the same body via GET-with-body silently
        dropped, the matcher saw no patient context, and the response
        was the disease-agnostic union.
        """
        mm = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Breast Cancer')
        TrialFactory(disease='Chronic Lymphocytic Leukemia')
        response = authed_client.post(
            '/trials/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        result_ids = {t['trialId'] for t in response.data['results']}
        # Strict equality, not membership: a regression that broadens the
        # response to include BC + CLL trials would silently pass an
        # `mm.id in result_ids` check. We want it to fail loudly.
        assert result_ids == {mm.id}, (
            'expected MM-only scoping; got ids='
            f'{result_ids} (titles='
            f'{[t["briefTitle"] for t in response.data["results"]]})'
        )

    def test_response_shape_matches_list(self, authed_client):
        """The match endpoint must return the same fields as `GET /trials/`
        so the frontend can interoperate. Spot-check `matchingType`,
        `matchScore`, `goodnessScore`, `attributesToFillIn`.
        """
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            '/trials/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.data['results'], 'expected at least one trial'
        trial = response.data['results'][0]
        for required in ('trialId', 'briefTitle', 'matchingType',
                         'matchScore', 'goodnessScore', 'attributesToFillIn'):
            assert required in trial, f'missing field {required}'
