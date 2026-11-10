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

    def test_match_score_sort_ranks_by_the_score_the_cards_show(
        self, authed_client, monkeypatch,
    ):
        """`?sort=matchScore` orders by the card's number, not the annotation.

        The SQL `match_score` annotation counts every mapped column the trial
        constrains; the card shows `criteria_match_score`, which counts the
        criteria the detail renders. They disagree, so ordering by the
        annotation listed 66% above 80% inside one page. The scores here are
        stubbed so the assertion is about the order the endpoint emits, not
        about which clinical fixtures happen to differ.
        """
        from trials.services.trial_details import match_score as match_score_module

        trials = [TrialFactory(disease='Multiple Myeloma') for _ in range(3)]
        scores = {trials[0].id: 50, trials[1].id: 90, trials[2].id: 70}
        monkeypatch.setattr(
            match_score_module,
            'criteria_match_score',
            lambda trial, patient_info, attrs_to_fill_in=None, template=None: scores[trial.id],
        )

        response = authed_client.post(
            '/trials/search/match/?sort=matchScore',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )

        assert response.status_code == 200
        rows = response.data['results']
        assert [row['trialId'] for row in rows] == [
            trials[1].id, trials[2].id, trials[0].id,
        ]
        assert [row['matchScore'] for row in rows] == [90, 70, 50]
