"""Patient-resolution error handling + memoization (#156, #159, #160).

#156: a supplied-but-unbuildable patient payload must surface as a 400, not be
swallowed into a silent None that runs the matcher with no patient context
(which returns an unfiltered/unscored trial list that looks valid).

#159/#160: patient context must be resolved at most once per request, even
though both get_queryset and get_serializer_context need it.
"""
import pytest
from unittest.mock import patch

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='resolve-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestPatientResolveErrors:
    def test_unbuildable_inline_payload_returns_400(self, authed_client):
        # A non-dict patient_info can't be built -> 400, not a silent 200 with
        # an unfiltered list.
        resp = authed_client.post(
            '/trials/match/', {'patient_info': 'not-a-dict'}, format='json'
        )
        assert resp.status_code == 400
        assert 'patient' in str(resp.data).lower()

    def test_patient_info_resolved_once_per_request(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.api.trials_views.resolve_patient_info', return_value=None
        ) as mock_resolve:
            resp = authed_client.get('/trials/')
        assert resp.status_code == 200
        assert mock_resolve.call_count == 1

    def test_non_inline_resolve_error_is_not_masked_as_400(self, authed_client):
        # When no inline payload was supplied (e.g. the person_id/CTOMOP path),
        # an unexpected build error is a real server/upstream bug and must NOT
        # be relabeled as a client 400 — it should surface as a 500 (#156).
        authed_client.raise_request_exception = False
        with patch(
            'trials.api.trials_views.resolve_patient_info',
            side_effect=RuntimeError('ctomop adapter blew up'),
        ):
            resp = authed_client.get('/trials/')
        assert resp.status_code == 500
