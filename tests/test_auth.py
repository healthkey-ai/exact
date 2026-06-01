"""House OIDC Identity auth: service-token + partner (Firebase) DRF classes.

Locks the auth contract on the protected `POST /trials/match/` surface:
- a shared service token authenticates server-to-server calls,
- a verified partner (Firebase) token authenticates and provisions an Identity,
- an anonymous request is rejected (default-deny).
"""
import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.authentication import ServiceTokenAuthentication
from accounts.models import Identity
from accounts.providers.base import TokenClaims
from tests.factories import TrialFactory

SERVICE_TOKEN = "test-service-token-value"


@pytest.fixture
def trial(db):
    return TrialFactory(disease="Multiple Myeloma")


def _match(client):
    return client.post(
        "/trials/match/",
        {"patient_info": {"disease": "multiple myeloma"}},
        format="json",
    )


@pytest.mark.django_db
class TestServiceTokenAuth:
    @override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
    def test_valid_service_token_authenticates(self, trial):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")
        assert _match(client).status_code == 200

    @override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
    def test_valid_service_token_yields_synthetic_identity(self, trial):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")
        _match(client)
        assert Identity.objects.filter(
            issuer=ServiceTokenAuthentication.SERVICE_ISSUER,
            sub=ServiceTokenAuthentication.SERVICE_SUB,
        ).exists()

    @override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
    def test_wrong_service_token_rejected(self, trial):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-the-token")
        assert _match(client).status_code == 401

    @override_settings(SERVICE_AUTH_TOKEN="")
    def test_unset_service_token_does_not_authenticate(self, trial):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer anything")
        assert _match(client).status_code == 401


class _FakeFirebaseProvider:
    """Stands in for FirebaseTokenProvider — verifies any token to fixed claims."""

    ISSUER = "https://securetoken.google.com/exact-test"
    SUB = "firebase-uid-123"

    def can_handle(self, token, unverified_payload):
        return True

    def verify(self, token):
        return TokenClaims(
            issuer=self.ISSUER, sub=self.SUB,
            email="patient@example.com", name="Pat Example", raw={},
        )


@pytest.mark.django_db
class TestPartnerAuth:
    def test_verified_partner_token_authenticates_and_provisions_identity(
        self, trial, monkeypatch
    ):
        monkeypatch.setattr(
            "accounts.authentication.get_providers",
            lambda: [_FakeFirebaseProvider()],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer fake.partner.jwt")
        assert _match(client).status_code == 200
        assert Identity.objects.filter(
            issuer=_FakeFirebaseProvider.ISSUER, sub=_FakeFirebaseProvider.SUB,
        ).exists()


@pytest.mark.django_db
class TestDefaultDeny:
    def test_anonymous_request_rejected(self, trial):
        assert _match(APIClient()).status_code == 401
