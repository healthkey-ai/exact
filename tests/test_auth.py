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


def _jwt_with_payload(payload: dict) -> str:
    """A JWT-shaped string whose payload decodes to *payload*.

    The signature is nonsense on purpose: `can_handle` runs on the payload
    decoded WITHOUT verification, so routing happens before any signature is
    checked. That is what makes every field here attacker-controlled.
    """
    def _b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f'{_b64({"alg": "none"})}.{_b64(payload)}.not-a-signature'


class TestUnverifiedClaimTypes:
    """A hostile `iss` type must route, not raise (#405).

    `decode_jwt_unverified` already refuses a payload that is not an object,
    but an object can still carry a claim of the wrong type. `.startswith` on
    a list or an int raises `AttributeError` before any provider verifies
    anything -- an anonymous caller turning one header into a 500.
    """

    HOSTILE = [
        ["https://securetoken.google.com/p"],
        5,
        {"nested": "object"},
        None,
        True,
    ]

    @pytest.mark.parametrize("iss", HOSTILE)
    def test_every_registered_provider_survives_a_hostile_iss(self, iss):
        """Asserted across the registry, not just Firebase.

        `can_handle` is the only place today that reads the unverified payload
        before verification. Iterating every configured provider is what keeps
        it that way when a second provider is added.
        """
        from accounts.providers.base import decode_jwt_unverified
        from accounts.providers.registry import get_providers

        # Every claim hostile, not just `iss`: a second provider routing on
        # `aud` or a custom claim would reproduce #405 exactly, and a test that
        # only poisons `iss` would pass while it did.
        payload = {key: iss for key in ("iss", "aud", "sub", "tid", "azp")}
        token = _jwt_with_payload(payload)
        unverified = decode_jwt_unverified(token)

        assert unverified is not None, "the payload is a valid object; only its claims are hostile"

        providers = get_providers()
        assert providers, (
            "an empty registry would make this loop assert nothing; "
            "PARTNER_AUTH_PROVIDERS must be configured for this guard to guard"
        )

        for provider in providers:
            # The assertion is that this does not raise. A provider may
            # legitimately answer True or False.
            provider.can_handle(token, unverified)

    @pytest.mark.django_db
    def test_a_hostile_iss_is_rejected_not_a_500(self, trial):
        """One representative value end to end.

        `test_every_registered_provider_survives_a_hostile_iss` already covers
        every hostile type at unit level; running all five through the HTTP
        stack and the DB fixture would buy nothing but wall-clock.
        """
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {_jwt_with_payload({"iss": ["x"], "sub": "u1"})}'
        )

        response = _match(client)

        assert response.status_code == 401, (
            "a hostile unverified claim must be an authentication refusal, "
            "not a server error"
        )

    def test_a_well_formed_iss_still_routes_to_firebase(self):
        """The guard must not break routing -- otherwise it would 'fix' #405
        by making every Firebase token unroutable."""
        from accounts.providers.base import decode_jwt_unverified
        from accounts.providers.firebase import FirebaseTokenProvider

        token = _jwt_with_payload(
            {"iss": "https://securetoken.google.com/exact-test", "sub": "u1"}
        )

        assert FirebaseTokenProvider().can_handle(
            token, decode_jwt_unverified(token)
        ) is True

    def test_a_missing_iss_does_not_route(self):
        from accounts.providers.base import decode_jwt_unverified
        from accounts.providers.firebase import FirebaseTokenProvider

        token = _jwt_with_payload({"sub": "u1"})

        assert FirebaseTokenProvider().can_handle(
            token, decode_jwt_unverified(token)
        ) is False


@pytest.mark.django_db
class TestNonAsciiBearerIsNotA500:
    """`Authorization: Bearer <non-ascii>` must be a refusal, not a crash.

    `hmac.compare_digest` raises TypeError when either str argument is
    non-ASCII, and Django hands the header over latin-1-decoded, so any byte in
    0x80-0xFF arrives as a non-ASCII str. `ServiceTokenAuthentication` runs
    FIRST in DEFAULT_AUTHENTICATION_CLASSES, so this needs no JWT shape and no
    provider at all -- one header from an anonymous caller.

    Same class as #405, found while reviewing its fix.
    """

    # What actually arrives. An HTTP header carries bytes; Django decodes them
    # latin-1, so a client sending UTF-8 Cyrillic produces a str of characters
    # in 0x80-0xFF -- the mojibake below, not the original text. Parametrising
    # on unencodable strings would only test Django's test client, which
    # refuses to encode them at all.
    @pytest.mark.parametrize(
        "bearer",
        [
            "\u00e9",
            "caf\u00e9",
            "\u0442\u043e\u043a\u0435\u043d".encode("utf-8").decode("latin-1"),
            "\U0001f600".encode("utf-8").decode("latin-1"),
            "\u00ff" * 64,
        ],
    )
    def test_non_ascii_bearer_is_rejected(self, trial, bearer):
        with override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")

            assert _match(client).status_code == 401

    def test_the_real_service_token_still_authenticates(self, trial):
        """The byte comparison must not break the credential it guards."""
        with override_settings(SERVICE_AUTH_TOKEN=SERVICE_TOKEN):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {SERVICE_TOKEN}")

            assert _match(client).status_code == 200

    def test_a_non_ascii_service_token_still_authenticates(self, trial):
        """The inverse of the server's decode must be latin-1, not utf-8.

        gunicorn (`str(b, 'latin1')`) and Django's ASGI handler both decode
        header bytes latin-1. An honest client holding a non-ASCII token sends
        its UTF-8 bytes; those arrive decoded latin-1. Re-encoding them utf-8
        double-encodes -- `b'caf\xc3\xa9'` becomes `b'caf\xc3\x83\xc2\xa9'` --
        and the token then never matches, forever, with nothing in the log.
        A silent permanent 401 is a worse failure than the 500 this replaced.
        """
        token = "café-token"
        wire_bytes = token.encode("utf-8")

        with override_settings(SERVICE_AUTH_TOKEN=token):
            client = APIClient()
            # What the server sees after its own latin-1 decode.
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {wire_bytes.decode('latin-1')}"
            )

            assert _match(client).status_code == 200
