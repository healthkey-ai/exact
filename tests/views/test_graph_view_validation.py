"""Input-validation tests for the graph endpoint (#158).

Pre-fix, `/trials-graph/graph/` did `int(n)` on the raw query param (500 on
non-numeric) and serialized the patient even when none was resolved. Both are
now 400s with a stable error body.
"""
import pytest
from unittest.mock import MagicMock, patch

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='graph-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestGraphViewValidation:
    def test_unauthenticated_returns_401(self):
        assert APIClient().get('/trials-graph/graph/').status_code == 401

    def test_missing_patient_context_returns_400(self, authed_client):
        resp = authed_client.get('/trials-graph/graph/')
        assert resp.status_code == 400
        assert 'patient' in str(resp.data).lower()

    def test_non_numeric_n_returns_400(self, authed_client):
        # Patient context present so we exercise the n-validation branch.
        # graph resolves via the shared TrialsViewSet._resolve_patient_info,
        # which calls resolve_patient_info imported into trials_views.
        with patch('trials.api.trials_views.resolve_patient_info', return_value=MagicMock()):
            resp = authed_client.get('/trials-graph/graph/?n=not-a-number')
        assert resp.status_code == 400
        assert "'n'" in str(resp.data)


@pytest.mark.django_db
class TestGraphPostAlias:
    """`POST /trials-graph/graph/match/` — the only way a federated caller can
    reach the graph at all.

    An inline patient payload travels in a body, GET-with-body is forbidden by
    the Fetch spec, and `?person_id=` is gated off outside DEBUG because it
    would let any authenticated caller read another patient's record.
    """

    def test_unauthenticated_returns_401(self):
        assert APIClient().post('/trials-graph/graph/match/', {}, format='json').status_code == 401

    def test_it_draws_the_graph_from_an_inline_patient(self, authed_client):
        from tests.factories import TrialFactory

        TrialFactory(disease='multiple myeloma')
        resp = authed_client.post(
            '/trials-graph/graph/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert resp.status_code == 200
        assert 'patient' in resp.data
        assert len(resp.data['trials']) == 1
        node = resp.data['trials'][0]
        assert set(node['match']) == {'matched', 'notMatched', 'missing'}

    def test_it_refuses_without_a_patient_like_the_get_form(self, authed_client):
        resp = authed_client.post('/trials-graph/graph/match/', {}, format='json')
        assert resp.status_code == 400
        assert 'patient' in str(resp.data).lower()

    def test_it_honours_n_and_the_filters(self, authed_client):
        from tests.factories import TrialFactory

        for i in range(3):
            TrialFactory(disease='multiple myeloma', study_id=f'NCT{i}')
        TrialFactory(disease='breast cancer', study_id='OTHER')

        resp = authed_client.post(
            '/trials-graph/graph/match/?n=2',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert resp.status_code == 200
        assert len(resp.data['trials']) == 2
        assert all(n['studyId'] != 'OTHER' for n in resp.data['trials'])


@pytest.mark.django_db
class TestGraphViewSetSurface:
    """The graph viewset inherits `TrialsViewSet`, so widening it to POST
    published every inherited POST action under `/trials-graph/` as well — a
    second, untested copy of the trials API at a URL nobody asked for."""

    @pytest.mark.parametrize('path', [
        '/trials-graph/match/',
        '/trials-graph/search/match/',
    ])
    def test_the_inherited_post_aliases_are_not_published_here(self, authed_client, path):
        # 404 where the router never registered the path, 405 where it falls
        # through to the detail route with `match` read as a pk. Either way the
        # action does not run, which is the claim.
        assert authed_client.post(path, {}, format='json').status_code in (404, 405)

    def test_the_graph_alias_itself_still_answers(self, authed_client):
        from tests.factories import TrialFactory

        TrialFactory(disease='multiple myeloma')
        resp = authed_client.post(
            '/trials-graph/graph/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        )
        assert resp.status_code == 200
