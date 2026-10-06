"""Partner-auth chain: provider configuration and end-to-end provisioning.

Complements tests/test_phr_auth.py, which unit-tests verification. What is
locked here is the wiring: that PARTNER_AUTH_PROVIDERS being env-driven still
resolves to the intended chain, and that a verified portal token provisions the
Identity under the prefixed subject rather than a bare portal id.
"""
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache as django_cache
from jwt.algorithms import RSAAlgorithm
from rest_framework.test import APIClient

from accounts.models import Identity
from accounts.providers import phr, registry
from accounts.providers.firebase import FirebaseTokenProvider
from accounts.providers.phr import PhrTokenProvider
from tests.factories import TrialFactory

ISSUER = 'healthkey-phr'
KID = 'portal-key-1'


@pytest.fixture(autouse=True)
def isolate_auth_state(monkeypatch):
    """Clear the cross-test state PartnerAuthentication and phr keep globally.

    The auth cache is a process-wide LocMemCache keyed on SHA256(token), and
    `portal_token()` is deterministic given identical claims (`exp` has
    one-second resolution), so two tests can mint byte-identical tokens and the
    second would read an entry pointing at an Identity its transaction rolled
    back. Also unplugs httpx — both verbs, so no test here can reach the network
    whatever a fixture sets PHR_ALLOW_INTROSPECTION or PHR_BASE_URL to. Tests
    needing a JWKS document install their own `get` on top of this.
    """
    def _forbidden(*args, **kwargs):
        raise AssertionError('unexpected outbound HTTP call')

    monkeypatch.setattr(phr.httpx, 'get', _forbidden)
    monkeypatch.setattr(phr.httpx, 'post', _forbidden)
    phr._introspect_budget.reset()
    django_cache.clear()
    yield
    phr._introspect_budget.reset()
    django_cache.clear()


@pytest.fixture
def trial(db):
    return TrialFactory(disease='Multiple Myeloma')


def _match(client):
    return client.post(
        '/trials/match/',
        {'patient_info': {'disease': 'multiple myeloma'}},
        format='json',
    )


# ── provider chain resolution ─────────────────────────────────────────

@pytest.fixture
def fresh_registry():
    """get_providers() memoises in a module global — clear it around each test."""
    registry._providers = None
    yield registry
    registry._providers = None


class TestProviderChainConfiguration:
    def test_default_chain_is_firebase_then_phr(self, settings, fresh_registry):
        settings.PARTNER_AUTH_PROVIDERS = [
            'accounts.providers.firebase.FirebaseTokenProvider',
            'accounts.providers.phr.PhrTokenProvider',
        ]
        assert [type(p) for p in fresh_registry.get_providers()] == [
            FirebaseTokenProvider,
            PhrTokenProvider,
        ]

    def test_portal_only_chain_excludes_firebase(self, settings, fresh_registry):
        """How a deployment without Firebase is expected to run (compose does)."""
        settings.PARTNER_AUTH_PROVIDERS = ['accounts.providers.phr.PhrTokenProvider']
        assert [type(p) for p in fresh_registry.get_providers()] == [PhrTokenProvider]

    def test_empty_list_disables_partner_auth_entirely(self, settings, fresh_registry):
        """`PARTNER_AUTH_PROVIDERS=` in a templated deploy config lands here.

        Locks the semantics: no providers means uniform 401s, not a fallback to
        the previous default chain.
        """
        settings.PARTNER_AUTH_PROVIDERS = []
        assert fresh_registry.get_providers() == []

    def test_non_provider_class_is_rejected(self, settings, fresh_registry):
        settings.PARTNER_AUTH_PROVIDERS = ['accounts.models.Identity']
        with pytest.raises(TypeError):
            fresh_registry.get_providers()


@pytest.mark.django_db
class TestPartnerAuthDisabled:
    def test_no_providers_rejects_every_bearer_token(
        self, trial, settings, fresh_registry, monkeypatch
    ):
        settings.PARTNER_AUTH_PROVIDERS = []
        monkeypatch.setattr(
            'accounts.authentication.get_providers', fresh_registry.get_providers
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer anything.at.all')
        assert _match(client).status_code == 401


# ── end-to-end through the portal provider ────────────────────────────

@pytest.fixture(scope='module')
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def portal_token(rsa_key, monkeypatch, settings):
    """A verifiable portal token, with the JWKS document served locally."""
    settings.PHR_ISSUER = ISSUER
    settings.PHR_JWKS_URL = 'https://portal.example/api/v1/auth/jwks/'
    settings.PHR_JWKS_CACHE_TTL = 3600
    settings.PHR_JWKS_MIN_REFRESH_INTERVAL = 60
    settings.PHR_ALLOW_INTROSPECTION = False

    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk['kid'] = KID

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {'keys': [jwk]}

    phr._jwks_cache.reset()
    monkeypatch.setattr(phr.httpx, 'get', lambda url, timeout=None: _Resp())
    monkeypatch.setattr(
        'accounts.authentication.get_providers', lambda: [PhrTokenProvider()]
    )

    def make(**overrides):
        claims = {
            'iss': ISSUER,
            'token_type': 'access',
            'user_id': 42,
            'exp': int(time.time()) + 300,
        }
        claims.update(overrides)
        return jwt.encode(claims, rsa_key, algorithm='RS256', headers={'kid': KID})

    yield make
    phr._jwks_cache.reset()


@pytest.mark.django_db
class TestPortalTokenProvisioning:
    def test_valid_portal_token_authenticates(self, trial, portal_token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {portal_token()}')
        assert _match(client).status_code == 200

    def test_identity_is_provisioned_under_the_prefixed_subject(
        self, trial, portal_token
    ):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {portal_token()}')
        _match(client)
        assert Identity.objects.filter(issuer=ISSUER, sub='phr:42').exists()
        # A bare portal id would occupy the globally-unique `sub` namespace and
        # could collide with another issuer's subject.
        assert not Identity.objects.filter(sub='42').exists()

    def test_returning_user_reuses_the_same_identity(self, trial, portal_token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {portal_token()}')
        _match(client)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {portal_token(jti="second")}')
        _match(client)
        assert Identity.objects.filter(issuer=ISSUER, sub='phr:42').count() == 1

    def test_forged_unsigned_token_does_not_provision_an_identity(
        self, trial, portal_token
    ):
        """The impersonation attempt must leave no trace, not just fail."""
        import base64

        def seg(obj):
            return base64.urlsafe_b64encode(
                json.dumps(obj).encode()
            ).decode().rstrip('=')

        forged = '{}.{}.'.format(
            seg({'alg': 'none'}),
            seg({
                'iss': ISSUER,
                'token_type': 'access',
                'user_id': 1,
                'exp': int(time.time()) + 300,
            }),
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {forged}')
        assert _match(client).status_code == 401
        assert not Identity.objects.filter(sub='phr:1').exists()
