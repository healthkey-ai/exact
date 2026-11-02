"""The bounded `trial_ids` filter used by the federated Favorites/Registered tabs.

The ids live in PROMOP; EXACT narrows the matching set inside the queryset so
counting, sorting and pagination all describe the requested set. An empty list
means "none saved", not "no filter" — the difference is the whole corpus.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='trial-ids-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _post(client, **body):
    return client.post(
        '/trials/search/match/',
        {'patient_info': {'disease': 'multiple myeloma'}, **body},
        format='json',
    )


@pytest.mark.django_db
class TestTrialIdsFilter:
    def test_absent_filter_returns_the_matching_set(self, authed_client):
        a = TrialFactory(disease='Multiple Myeloma')
        b = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Breast Cancer')
        response = _post(authed_client)
        assert response.status_code == 200
        assert {t['trialId'] for t in response.data['results']} == {a.id, b.id}

    def test_empty_list_returns_no_trials(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = _post(authed_client, trial_ids=[])
        assert response.status_code == 200
        assert response.data['results'] == []
        assert response.data['count'] == 0

    def test_subset_narrows_and_count_agrees(self, authed_client):
        wanted = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = _post(authed_client, trial_ids=[wanted.id])
        assert response.status_code == 200
        assert [t['trialId'] for t in response.data['results']] == [wanted.id]
        assert response.data['count'] == 1

    def test_numeric_strings_are_accepted(self, authed_client):
        wanted = TrialFactory(disease='Multiple Myeloma')
        response = _post(authed_client, trial_ids=[str(wanted.id)])
        assert response.status_code == 200
        assert [t['trialId'] for t in response.data['results']] == [wanted.id]

    @pytest.mark.parametrize('bad', [True, False, 3.5, -1, 2 ** 63, 'abc', ' 1x '])
    def test_malformed_ids_are_rejected(self, authed_client, bad):
        response = _post(authed_client, trial_ids=[bad])
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    def test_query_string_is_rejected(self, authed_client):
        response = authed_client.post(
            '/trials/search/match/?trial_ids=1',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    @pytest.mark.parametrize('search_type', ['favorites', 'my_trials', 'not_eligible'])
    def test_user_scoped_search_types_are_rejected(self, authed_client, search_type):
        response = authed_client.post(
            f'/trials/search/match/?type={search_type}',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert response.status_code == 400
        assert 'type' in response.data
