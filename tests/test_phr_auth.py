"""Locks the PhrTokenProvider verification contract (#359 review).

The provider is ~270 lines of original signature-verification, caching and
rate-limiting logic, so the interesting cases are the ones an attacker picks,
not the happy path: which verification path a token gets routed to, what a
token has to carry to cost an outbound request, and what the JWKS cache does
when the portal misbehaves.

No network: `httpx.get`/`httpx.post` are replaced per test and the call lists
are asserted on, because "returns 401" and "returns 401 without touching the
network" are different guarantees and only one of them bounds the DoS.
"""
import base64
import json
import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from rest_framework.exceptions import AuthenticationFailed

from accounts.providers import phr
from accounts.providers.phr import PhrTokenProvider

ISSUER = 'healthkey-phr'
JWKS_URL = 'https://portal.example/api/v1/auth/jwks/'
INTROSPECT_URL = 'https://portal.example/api/v1/auth/introspect/'
KID = 'portal-key-1'
# 32+ bytes: PyJWT warns below that for HS256, and a warning per minted token
# buries anything the suite actually wants to say.
HS_SECRET = 'dev-shared-secret-long-enough-for-hs256'


@pytest.fixture(autouse=True)
def phr_settings(settings):
    """Fix the provider's config so tests don't inherit the ambient deployment.

    A fixture rather than `@override_settings` on the class: that form only
    works on SimpleTestCase subclasses, not plain pytest classes.
    """
    settings.PHR_ISSUER = ISSUER
    settings.PHR_JWKS_URL = JWKS_URL
    settings.PHR_INTROSPECT_URL = INTROSPECT_URL
    settings.PHR_JWKS_CACHE_TTL = 3600
    settings.PHR_JWKS_MIN_REFRESH_INTERVAL = 60
    settings.PHR_ALLOW_INTROSPECTION = False
    settings.PHR_INTROSPECT_MAX_CALLS = 30
    settings.PHR_INTROSPECT_RATE_INTERVAL = 60
    return settings


# ── token construction ────────────────────────────────────────────────

def _claims(**overrides):
    base = {
        'iss': ISSUER,
        'token_type': 'access',
        'user_id': 42,
        'exp': int(time.time()) + 300,
    }
    base.update(overrides)
    return base


def _seg(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')


def handcrafted_token(header, claims):
    """A JWT with an arbitrary header and an empty signature.

    Built by hand rather than via jwt.encode: several tests need a header PyJWT
    would refuse to produce (`alg: "none"`) or a header/signature pairing it
    would never emit (RS256 claims relabelled HS256).
    """
    return f'{_seg(header)}.{_seg(claims)}.'


@pytest.fixture(scope='module')
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope='module')
def other_rsa_key():
    """A second keypair the portal never published — forgery source."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope='module')
def public_jwk(rsa_key):
    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk['kid'] = KID
    return jwk


@pytest.fixture
def rs256(rsa_key):
    def make(kid=KID, key=None, **overrides):
        return jwt.encode(
            _claims(**overrides),
            key or rsa_key,
            algorithm='RS256',
            headers={'kid': kid},
        )
    return make


@pytest.fixture
def rs256_without(rsa_key):
    """An RS256 token with *claim* removed rather than overridden."""
    def make(claim, **overrides):
        payload = _claims(**overrides)
        del payload[claim]
        return jwt.encode(payload, rsa_key, algorithm='RS256', headers={'kid': KID})
    return make


@pytest.fixture
def hs256():
    def make(**overrides):
        return jwt.encode(_claims(**overrides), HS_SECRET, algorithm='HS256')
    return make


@pytest.fixture
def hs256_without():
    def make(claim, **overrides):
        payload = _claims(**overrides)
        del payload[claim]
        return jwt.encode(payload, HS_SECRET, algorithm='HS256')
    return make


# ── network doubles ───────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Double:
    def __init__(self, body):
        self.calls = []
        self.body = body
        self.error = None


@pytest.fixture(autouse=True)
def isolate_provider_state(monkeypatch):
    """Reset the process-wide cache/budget and unplug httpx around each test.

    Unplugging by default means a test that forgets to install a double fails
    loudly instead of reaching the real network.
    """
    phr._jwks_cache.reset()
    phr._introspect_budget.reset()

    def _forbidden(*args, **kwargs):
        raise AssertionError('unexpected outbound HTTP call')

    monkeypatch.setattr(phr.httpx, 'get', _forbidden)
    monkeypatch.setattr(phr.httpx, 'post', _forbidden)
    yield
    phr._jwks_cache.reset()
    phr._introspect_budget.reset()


@pytest.fixture
def jwks(monkeypatch, public_jwk):
    double = _Double({'keys': [public_jwk]})

    def fake_get(url, timeout=None):
        double.calls.append(url)
        if double.error is not None:
            raise double.error
        return _Resp(double.body)

    monkeypatch.setattr(phr.httpx, 'get', fake_get)
    return double


@pytest.fixture
def introspect(monkeypatch):
    double = _Double({'active': True, 'user_id': 7})

    def fake_post(url, **kwargs):
        double.calls.append(kwargs.get('json'))
        if double.error is not None:
            raise double.error
        return _Resp(double.body)

    monkeypatch.setattr(phr.httpx, 'post', fake_post)
    return double


@pytest.fixture
def provider():
    return PhrTokenProvider()


def clock_ahead(offset):
    """A `time` stand-in whose monotonic clock is *offset* seconds ahead.

    The cache measures TTL and floor with monotonic(); wall-clock time() has to
    stay real, or a token minted "now" would read as expired.
    """
    real_monotonic, real_time = time.monotonic, time.time

    class _Clock:
        @staticmethod
        def monotonic():
            return real_monotonic() + offset

        @staticmethod
        def time():
            return real_time()

    return _Clock


# ── routing: which path does a token reach? ───────────────────────────

# The last two are not strings: `get_unverified_header` guarantees only a JSON
# object, so a set-membership test on the raw value raises TypeError — which
# would reach the caller as one warning-level log line per anonymous request.
NON_ALLOWLISTED_ALGS = [
    'none', 'ES256', 'PS256', 'RS512', 'HS128', 'garbage', None, ['HS256'], {'a': 1},
]


class TestPathDispatch:
    """The `alg` header is attacker-supplied, so it must not select the path.

    Pre-review the dispatch was `RS256 → JWKS, everything else →
    introspection`, which let any anonymous caller pick the network path — and
    with it whatever laxness the portal's introspection endpoint has.

    These run with introspection **enabled**, which is the only configuration
    that can tell the allowlist apart from the PHR_ALLOW_INTROSPECTION gate:
    with the gate closed, a token routed to introspection is rejected there
    anyway, so every assertion below would hold even with the allowlist gone.
    """

    @pytest.fixture(autouse=True)
    def introspection_enabled(self, phr_settings):
        phr_settings.PHR_ALLOW_INTROSPECTION = True

    @pytest.mark.parametrize('alg', NON_ALLOWLISTED_ALGS)
    def test_unexpected_algs_are_rejected_without_an_outbound_call(
        self, provider, introspect, jwks, alg
    ):
        """Rejected locally — not by the portal, and not by the JWKS document.

        The call-list assertions are the point: `is None` alone would also hold
        if the token were dispatched to introspection and declined there.
        """
        assert provider.verify(handcrafted_token({'alg': alg}, _claims())) is None
        assert introspect.calls == []
        assert jwks.calls == []

    def test_unsigned_token_naming_a_victim_never_reaches_the_portal(
        self, provider, introspect
    ):
        """The impersonation shape: no signature, a subject of the attacker's
        choosing, and an `iss` that routes into this provider.

        A portal whose introspection endpoint does not fully verify signatures
        would answer `active` here, so this must not be asked at all.
        """
        introspect.body = {'active': True, 'user_id': 1}
        assert provider.verify(handcrafted_token({'alg': 'none'}, _claims(user_id=1))) is None
        assert introspect.calls == []

    def test_rs256_relabelled_hs256_does_not_reach_the_jwks_path(
        self, provider, rs256, jwks, introspect
    ):
        """A valid RS256 token with its header rewritten must not verify there.

        HS256 *is* allowlisted, so this one legitimately routes to the portal —
        what matters is that it is never checked against the JWKS document,
        which is what an HS/RS key-confusion attack needs.
        """
        _, payload, signature = rs256().split('.')
        forged = f"{_seg({'alg': 'HS256', 'kid': KID})}.{payload}.{signature}"
        introspect.body = {'active': False}
        assert provider.verify(forged) is None
        assert jwks.calls == []

    def test_rs256_still_routes_to_offline_verification(
        self, provider, rs256, jwks, introspect
    ):
        """The allowlist must not have cost the primary path its offline check."""
        assert provider.verify(rs256()) is not None
        assert introspect.calls == []
        assert jwks.calls != []

    def test_wrong_issuer_is_declined_before_verification(self, provider, rs256):
        token = rs256(iss='https://accounts.google.com')
        payload = jwt.decode(token, options={'verify_signature': False})
        assert provider.can_handle(token, payload) is False

    def test_non_jwt_token_is_rejected(self, provider):
        assert provider.verify('not-a-jwt') is None


class TestPathDispatchWithIntrospectionDisabled:
    """The gate is the second, independent line of defence on the same tokens."""

    @pytest.mark.parametrize('alg', NON_ALLOWLISTED_ALGS + ['HS256'])
    def test_nothing_verifies_and_nothing_leaves_the_process(self, provider, alg):
        # The autouse isolate fixture makes any httpx call raise, so reaching
        # the network here fails the test rather than passing quietly.
        assert provider.verify(handcrafted_token({'alg': alg}, _claims())) is None


# ── RS256 / JWKS path ─────────────────────────────────────────────────

class TestJwksVerification:
    def test_valid_access_token_verifies_to_prefixed_subject(
        self, provider, rs256, jwks
    ):
        claims = provider.verify(rs256())
        assert claims is not None
        assert claims.issuer == ISSUER
        # The "phr:" prefix is load-bearing: Identity.sub is unique globally,
        # not per issuer, so a bare portal id could collide with another
        # issuer's subject.
        assert claims.sub == 'phr:42'

    def test_email_claim_is_carried_through(self, provider, rs256, jwks):
        assert provider.verify(rs256(email='patient@example.com')).email == 'patient@example.com'

    def test_token_signed_by_an_unpublished_key_is_rejected(
        self, provider, rs256, other_rsa_key, jwks
    ):
        """Same `kid`, different private key — the forgery a JWKS exists to stop."""
        assert provider.verify(rs256(key=other_rsa_key)) is None

    def test_expired_token_raises_authentication_failed(self, provider, rs256, jwks):
        with pytest.raises(AuthenticationFailed):
            provider.verify(rs256(exp=int(time.time()) - 1))

    def test_token_without_exp_is_rejected(self, provider, rs256_without, jwks):
        """PyJWT's expiry check is a no-op when the claim is absent, so `exp`
        has to be *required* — otherwise such a token never expires."""
        assert provider.verify(rs256_without('exp')) is None

    def test_not_yet_valid_token_is_rejected(self, provider, rs256, jwks):
        assert provider.verify(rs256(nbf=int(time.time()) + 300)) is None

    def test_refresh_token_is_rejected(self, provider, rs256, jwks):
        assert provider.verify(rs256(token_type='refresh')) is None

    def test_token_without_token_type_is_rejected(self, provider, rs256_without, jwks):
        assert provider.verify(rs256_without('token_type')) is None

    def test_empty_subject_is_rejected(self, provider, rs256, jwks):
        """Otherwise every such user collapses onto one shared "phr:" Identity."""
        assert provider.verify(rs256(user_id='', sub='')) is None

    def test_sub_is_used_when_user_id_is_absent(self, provider, rs256_without, jwks):
        assert provider.verify(rs256_without('user_id', sub='99')).sub == 'phr:99'

    def test_audience_is_not_validated(self, provider, rs256, jwks):
        """Documents accepted risk, so adding a check stays a deliberate change.

        A token the portal minted for a different relying party is currently
        accepted here as full access; there is no PHR_AUDIENCE setting yet.
        """
        assert provider.verify(rs256(aud='some-other-service')) is not None

    def test_unreachable_jwks_fails_closed(self, provider, rs256, jwks):
        jwks.error = RuntimeError('portal unreachable')
        assert provider.verify(rs256()) is None


# ── JWKS cache: the refresh floor is the DoS bound ────────────────────

class TestJwksCache:
    def test_unknown_kid_burst_drives_one_fetch(self, provider, jwks):
        """A `kid` miss triggers a refresh, and `kid` is attacker-controlled —
        without the floor each bogus token would cost its own blocking fetch."""
        for n in range(25):
            provider.verify(handcrafted_token({'alg': 'RS256', 'kid': f'k{n}'}, _claims()))
        assert len(jwks.calls) == 1

    def test_unknown_kid_burst_does_not_grow_the_cache(self, provider, jwks):
        for n in range(50):
            provider.verify(handcrafted_token({'alg': 'RS256', 'kid': f'x{n}'}, _claims()))
        # Keys come from the fetched document, never from the token, so the
        # cache is bounded by what the portal publishes.
        assert set(phr._jwks_cache._keys) == {KID}

    def test_failed_refresh_keeps_the_previously_good_key(
        self, provider, rs256, jwks, monkeypatch
    ):
        assert provider.verify(rs256()) is not None
        fetches = len(jwks.calls)

        # Expire the TTL and break the endpoint: a momentarily broken document
        # must not evict a key that still verifies.
        monkeypatch.setattr(phr, 'time', clock_ahead(7200))
        jwks.error = RuntimeError('portal down')
        assert provider.verify(rs256()) is not None
        assert len(jwks.calls) > fetches

    @pytest.mark.parametrize('doc', [
        {'keys': None},
        {'keys': {}},
        {'keys': []},
        {'keys': [{'kty': 'RSA'}]},          # no kid
        {'keys': [{'kty': 'nonsense'}]},     # PyJWK cannot build it
        {},
    ])
    def test_malformed_jwks_documents_do_not_raise(self, provider, rs256, jwks, doc):
        jwks.body = doc
        assert provider.verify(rs256()) is None

    def test_one_unusable_key_does_not_discard_the_good_one(
        self, provider, rs256, jwks, public_jwk
    ):
        jwks.body = {'keys': [{'kty': 'nonsense', 'kid': 'bad'}, public_jwk]}
        assert provider.verify(rs256()) is not None

    def test_rotation_is_picked_up_after_the_ttl(
        self, provider, rs256, jwks, public_jwk, monkeypatch
    ):
        assert provider.verify(rs256()) is not None
        jwks.body = {'keys': [{**public_jwk, 'kid': 'portal-key-2'}]}
        monkeypatch.setattr(phr, 'time', clock_ahead(7200))
        assert provider.verify(rs256(kid='portal-key-2')) is not None


# ── introspection fallback ────────────────────────────────────────────

class TestIntrospectionGate:
    """Off by default: the path delegates the signature check to the portal.

    Both PHR_*_URLs derive from one PHR_BASE_URL, so before the gate existed
    every RS256 deployment silently enabled this too — and the caller chose it.
    """

    def test_disabled_rejects_without_calling_the_portal(
        self, provider, hs256, introspect
    ):
        assert provider.verify(hs256()) is None
        assert introspect.calls == []

    def test_enabled_verifies_an_active_token(
        self, provider, hs256, introspect, phr_settings
    ):
        phr_settings.PHR_ALLOW_INTROSPECTION = True
        claims = provider.verify(hs256())
        assert claims is not None
        assert claims.sub == 'phr:7'
        assert len(introspect.calls) == 1


class TestIntrospectionVerification:
    @pytest.fixture(autouse=True)
    def enabled(self, phr_settings):
        phr_settings.PHR_ALLOW_INTROSPECTION = True

    def test_inactive_token_is_rejected(self, provider, hs256, introspect):
        introspect.body = {'active': False}
        assert provider.verify(hs256()) is None

    def test_refresh_token_is_rejected_without_calling_the_portal(
        self, provider, hs256, introspect
    ):
        """`token_type` is checkable locally, so it costs no outbound request —
        RFC 7662's own `token_type` is the OAuth2 type and cannot carry this."""
        assert provider.verify(hs256(token_type='refresh')) is None
        assert introspect.calls == []

    def test_expired_token_is_rejected_without_calling_the_portal(
        self, provider, hs256, introspect
    ):
        assert provider.verify(hs256(exp=int(time.time()) - 1)) is None
        assert introspect.calls == []

    def test_token_without_exp_is_rejected_without_calling_the_portal(
        self, provider, hs256_without, introspect
    ):
        assert provider.verify(hs256_without('exp')) is None
        assert introspect.calls == []

    def test_response_subject_outranks_the_payload(self, provider, hs256, introspect):
        """A `user_id` that exists only in the unverified payload must not
        outrank the portal's own answer."""
        introspect.body = {'active': True, 'user_id': 7}
        assert provider.verify(hs256(user_id=9)).sub == 'phr:7'

    def test_payload_subject_is_the_fallback_when_the_response_is_silent(
        self, provider, hs256, introspect
    ):
        """§2.2 makes every claim optional; some portals answer only `active`."""
        introspect.body = {'active': True}
        assert provider.verify(hs256(user_id=9)).sub == 'phr:9'

    def test_no_subject_anywhere_is_rejected(
        self, provider, hs256_without, introspect
    ):
        introspect.body = {'active': True}
        assert provider.verify(hs256_without('user_id')) is None

    @pytest.mark.parametrize('body', [[], 'ok', None, 42])
    def test_non_object_response_is_rejected(self, provider, hs256, introspect, body):
        introspect.body = body
        assert provider.verify(hs256()) is None

    @pytest.mark.parametrize('active', ['false', 'true', 1, 'yes', [], {}])
    def test_non_boolean_active_is_rejected(
        self, provider, hs256, introspect, active
    ):
        """§2.2 specifies a boolean. Falsiness would read `"false"` — a string —
        as truthy and authenticate a token the portal had just declined."""
        introspect.body = {'active': active, 'user_id': 7}
        assert provider.verify(hs256()) is None

    @pytest.mark.parametrize('active', ['true', 1, 'yes'])
    def test_non_boolean_active_is_reported(
        self, provider, hs256, introspect, caplog, active
    ):
        """A portal answering `1` would 401 every sign-in; without a log line
        that is indistinguishable from the portal declining the token."""
        introspect.body = {'active': active}
        with caplog.at_level(logging.WARNING, logger='accounts.providers.phr'):
            provider.verify(hs256())
        assert any('non-boolean' in r.message for r in caplog.records), \
            f'active={active!r} was rejected silently'

    def test_a_plain_false_active_is_not_reported_as_a_misconfiguration(
        self, provider, hs256, introspect, caplog
    ):
        """The normal decline stays at debug — it is not an operator problem."""
        introspect.body = {'active': False}
        with caplog.at_level(logging.WARNING, logger='accounts.providers.phr'):
            assert provider.verify(hs256()) is None
        assert caplog.records == []

    def test_transport_failure_is_rejected(self, provider, hs256, introspect):
        introspect.error = RuntimeError('timeout')
        assert provider.verify(hs256()) is None

    def test_outbound_calls_are_capped_per_interval(
        self, provider, hs256, introspect, phr_settings
    ):
        """The DoS bound. DRF authenticates before it throttles, so
        AnonRateThrottle cannot reach this path; without the budget an
        anonymous caller could hold every sync worker in a 5s POST.
        """
        phr_settings.PHR_INTROSPECT_MAX_CALLS = 5
        for _ in range(40):
            provider.verify(hs256())
        assert len(introspect.calls) == 5

    def test_budget_refills_after_the_interval(
        self, provider, hs256, introspect, phr_settings, monkeypatch
    ):
        phr_settings.PHR_INTROSPECT_MAX_CALLS = 2
        for _ in range(5):
            provider.verify(hs256())
        assert len(introspect.calls) == 2

        monkeypatch.setattr(phr, 'time', clock_ahead(120))
        assert provider.verify(hs256()) is not None
        assert len(introspect.calls) == 3

    def test_a_zero_interval_still_caps_calls(
        self, provider, hs256, introspect, phr_settings
    ):
        """At interval 0 the window would restart on every call, removing the
        ceiling entirely — the one misconfiguration here that fails open."""
        phr_settings.PHR_INTROSPECT_MAX_CALLS = 3
        phr_settings.PHR_INTROSPECT_RATE_INTERVAL = 0
        for _ in range(40):
            provider.verify(hs256())
        assert len(introspect.calls) == 3

    @pytest.mark.parametrize('max_calls', [0, -1])
    def test_a_non_positive_budget_admits_nothing(
        self, provider, hs256, introspect, phr_settings, max_calls
    ):
        phr_settings.PHR_INTROSPECT_MAX_CALLS = max_calls
        assert provider.verify(hs256()) is None
        assert introspect.calls == []

    def test_exhaustion_is_logged_once_per_window_not_per_request(
        self, provider, hs256, introspect, phr_settings, caplog, monkeypatch
    ):
        """The rejected requests are attacker-drivable, so one line each would
        let an anonymous caller flood the log — the same reasoning that keeps
        the `kid` miss at debug."""
        phr_settings.PHR_INTROSPECT_MAX_CALLS = 1
        with caplog.at_level(logging.WARNING, logger='accounts.providers.phr'):
            for _ in range(30):
                provider.verify(hs256())
            exhausted = [r for r in caplog.records if 'budget exhausted' in r.message]
            assert len(exhausted) == 1, \
                f'{len(exhausted)} warnings for 29 rejections'

            # A new window re-arms the warning — silence forever would hide a
            # deployment that is permanently over its cap.
            monkeypatch.setattr(phr, 'time', clock_ahead(120))
            for _ in range(5):
                provider.verify(hs256())
            exhausted = [r for r in caplog.records if 'budget exhausted' in r.message]
            assert len(exhausted) == 2
