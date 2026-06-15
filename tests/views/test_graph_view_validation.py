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
        with patch('trials.api.graph_view.resolve_patient_info', return_value=MagicMock()):
            resp = authed_client.get('/trials-graph/graph/?n=not-a-number')
        assert resp.status_code == 400
        assert "'n'" in str(resp.data)
