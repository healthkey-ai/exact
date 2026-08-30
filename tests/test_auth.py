"""House OIDC Identity auth: service-token + partner (Firebase) DRF classes.

Locks the auth contract on the protected `POST /trials/match/` surface:
- a shared service token authenticates server-to-server calls,
- a verified partner (Firebase) token authenticates and provisions an Identity,
- an anonymous request is rejected (default-deny).
"""
import base64
import json

import pytest
from django.core.cache import cache as django_cache
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


@pytest.fixture(autouse=True)
def _clear_token_cache():
    """The verified-token cache is LocMem and lives for the whole process.

    Without this, a test that authenticates leaves an entry keyed by its bearer
    behind, and a later test reusing the same bearer silently becomes a cache
    hit — so correctness would rest on collection order.
    """
    django_cache.clear()
    yield
    django_cache.clear()


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

    def test_jwt_shaped_token_with_non_object_payload_is_rejected(self, trial):
        # A JWT-shaped token whose middle segment decodes to a JSON list (not
        # an object) must read as unauthenticated (401), never crash provider
        # routing with a 500. Regression guard for decode_jwt_unverified.
        def seg(obj):
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

        token = f"{seg({'alg': 'RS256'})}.{seg(['not', 'an', 'object'])}.sig"
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert _match(client).status_code == 401


@pytest.mark.django_db
class TestInactiveIdentityIsRejected:
    """`is_active=False` must revoke API access on both house backends.

    `IsAuthenticated` only consults `is_authenticated`, which `AbstractBaseUser`
    hardcodes to True — so without an explicit check the admin's `is_active`
    toggle is a control that silently does nothing, and an offboarded partner
    keeps full access. DRF's own backends reject inactive users; these tests
    lock the house backends to the same behaviour.
    """

    @override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN)
    def test_deactivated_service_identity_is_rejected(self, trial):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")
        assert _match(client).status_code == 200

        Identity.objects.filter(
            issuer=ServiceTokenAuthentication.SERVICE_ISSUER,
            sub=ServiceTokenAuthentication.SERVICE_SUB,
        ).update(is_active=False)

        assert _match(client).status_code == 401

    def test_deactivated_partner_identity_is_rejected_via_the_cached_path(
        self, trial, monkeypatch
    ):
        """Deactivated after a successful request, so the 401 comes from the
        cache-hit branch. The cold-cache counterpart is the test below it."""
        monkeypatch.setattr(
            "accounts.authentication.get_providers",
            lambda: [_FakeFirebaseProvider()],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer fake.partner.jwt")
        assert _match(client).status_code == 200

        Identity.objects.filter(
            issuer=_FakeFirebaseProvider.ISSUER, sub=_FakeFirebaseProvider.SUB,
        ).update(is_active=False)

        assert _match(client).status_code == 401

    def test_already_inactive_partner_identity_is_rejected_on_fresh_verification(
        self, trial, monkeypatch
    ):
        """Cold cache: the identity is already inactive before the first request.

        Covers the freshly-verified path independently of the cached one — the
        provider verifies the token successfully and the rejection has to come
        from the is_active check on `_get_or_create_identity`'s result.
        """
        Identity.objects.create(
            issuer=_FakeFirebaseProvider.ISSUER,
            sub=_FakeFirebaseProvider.SUB,
            is_active=False,
        )
        monkeypatch.setattr(
            "accounts.authentication.get_providers",
            lambda: [_FakeFirebaseProvider()],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer fake.partner.jwt")
        assert _match(client).status_code == 401

    def test_deactivation_is_not_deferred_by_the_token_cache(self, trial, monkeypatch):
        """A deactivation must bite immediately, not after AUTH_TOKEN_CACHE_TTL.

        The first request populates the verified-token cache; the second is a
        cache hit that never reaches `provider.verify`. Without the check on
        the cached path the deactivated identity would keep access for up to
        `AUTH_TOKEN_CACHE_TTL` seconds.
        """
        verify_calls = []

        class _CountingProvider(_FakeFirebaseProvider):
            def verify(self, token):
                verify_calls.append(token)
                return super().verify(token)

        monkeypatch.setattr(
            "accounts.authentication.get_providers",
            lambda: [_CountingProvider()],
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer fake.partner.jwt")

        assert _match(client).status_code == 200
        assert _match(client).status_code == 200
        # Second request was served from the cache, so the deactivation below
        # exercises the cached path rather than a fresh verification.
        assert len(verify_calls) == 1

        Identity.objects.filter(
            issuer=_FakeFirebaseProvider.ISSUER, sub=_FakeFirebaseProvider.SUB,
        ).update(is_active=False)

        assert _match(client).status_code == 401
        assert len(verify_calls) == 1
