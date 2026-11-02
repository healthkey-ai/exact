"""`POST /trials/search/match/` is the federated UI's inline-patient path.

The UI posts `{patient_info: ...}` here because GET-with-body is forbidden by
the Fetch spec and dropped by axios's XHR adapter. The alias must bind
`self.action = 'search'` (so `?sort=` and search-only branches fire) and return
the same shape as the GET endpoint.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='search-match-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestTrialsSearchMatchPost:
    def test_unauthenticated_post_returns_401(self):
        response = APIClient().post('/trials/search/match/', {}, format='json')
        assert response.status_code == 401

    def test_authed_post_returns_200_with_search_shape(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert 'results' in response.data
        assert 'itemsTotalCount' in response.data

    def test_inline_patient_info_scopes_to_disease(self, authed_client):
        mm = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Breast Cancer')
        TrialFactory(disease='Chronic Lymphocytic Leukemia')
        response = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 200
        assert {t['trialId'] for t in response.data['results']} == {mm.id}
