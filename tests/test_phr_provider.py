"""Adversarial unit tests for the PHR token provider.

Covers the auth-critical behaviour hardened after review: alg routing
(RS256 -> offline JWKS, HS256 -> opt-in introspection, everything else ->
rejected), the opt-in introspection guard (no outbound call when unconfigured),
RFC 7662 form-encoding + client auth, and access-vs-refresh enforcement.
"""
import base64
import json

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from accounts.providers import phr
from accounts.providers.phr import PhrTokenProvider

ISSUER = "healthkey-phr"

# One RSA keypair for the whole module — keygen is the slow part.
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_RSA_PRIV = _RSA_KEY
_RSA_PUB = _RSA_KEY.public_key()


def _seg(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def _none_token(payload):
    """A JWT with alg=none and an empty signature."""
    return f"{_seg({'alg': 'none'})}.{_seg(payload)}."


def _hs256(payload, secret="dev-secret-at-least-32-bytes-long!!"):
    return jwt.encode(payload, secret, algorithm="HS256")


def _rs256(payload, alg="RS256"):
    return jwt.encode(payload, _RSA_PRIV, algorithm=alg, headers={"kid": "k1"})


def _verify(token):
    return PhrTokenProvider().verify(token)


class _Resp:
    def __init__(self, data):
        self._data = data
        self.captured = None

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def no_network(monkeypatch):
    """Record outbound calls so a test can assert none happened.

    The assertion must run AFTER verify() returns, not inside the patched
    function: `_verify_introspect` wraps the call in `except Exception`, which
    would swallow an AssertionError raised here and let the test pass vacuously.
    """
    calls = []

    def _record(*a, **k):
        calls.append((a, k))
        return _Resp({"active": False})

    monkeypatch.setattr(phr.httpx, "post", _record)
    monkeypatch.setattr(phr.httpx, "get", _record)
    return calls


# ── can_handle: routing on the unverified issuer only ────────────────────
@override_settings(PHR_ISSUER=ISSUER)
def test_can_handle_matches_issuer():
    p = PhrTokenProvider()
    assert p.can_handle("t", {"iss": ISSUER}) is True
    assert p.can_handle("t", {"iss": "someone-else"}) is False
    assert p.can_handle("t", None) is False


# ── alg routing: only RS256 / HS256 are ever processed ───────────────────
@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect")
def test_alg_none_is_rejected_without_network(no_network):
    # alg=none must never reach introspection (would be an amplification lever).
    assert _verify(_none_token({"iss": ISSUER, "token_type": "access", "user_id": "1"})) is None
    assert no_network == []  # revert-guard: routed-to-introspect would record a call


@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect")
def test_unsupported_alg_rejected_without_network(no_network):
    token = _rs256({"iss": ISSUER, "token_type": "access", "user_id": "1"}, alg="RS384")
    assert _verify(token) is None
    assert no_network == []


# ── H1: introspection is opt-in — no URL means no outbound call ──────────
@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="")
def test_hs256_without_introspect_url_makes_no_call(no_network):
    token = _hs256({"iss": ISSUER, "token_type": "access", "user_id": "7"})
    assert _verify(token) is None
    assert no_network == []  # opt-in guard: empty URL must make no outbound call


# ── RS256 offline path ───────────────────────────────────────────────────
@override_settings(PHR_ISSUER=ISSUER)
def test_rs256_valid_access_token_authenticates(no_network, monkeypatch):
    monkeypatch.setattr(phr._jwks_cache, "get", lambda *a, **k: _RSA_PUB)
    claims = _verify(_rs256({"iss": ISSUER, "token_type": "access", "user_id": "42",
                             "email": "p@example.com"}))
    assert claims is not None
    assert claims.sub == "phr:42"
    assert claims.email == "p@example.com"


@override_settings(PHR_ISSUER=ISSUER)
def test_rs256_refresh_token_rejected(no_network, monkeypatch):
    monkeypatch.setattr(phr._jwks_cache, "get", lambda *a, **k: _RSA_PUB)
    assert _verify(_rs256({"iss": ISSUER, "token_type": "refresh", "user_id": "42"})) is None


@override_settings(PHR_ISSUER=ISSUER)
def test_rs256_wrong_issuer_rejected(no_network, monkeypatch):
    monkeypatch.setattr(phr._jwks_cache, "get", lambda *a, **k: _RSA_PUB)
    assert _verify(_rs256({"iss": "evil", "token_type": "access", "user_id": "42"})) is None


# ── HS256 introspection path (opt-in, configured) ────────────────────────
@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect",
                   PHR_INTROSPECT_AUTH="client:secret")
def test_hs256_introspection_active_access_authenticates(monkeypatch):
    resp = _Resp({"active": True, "user_id": "99"})
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls.update(kwargs)
        return resp

    monkeypatch.setattr(phr.httpx, "post", fake_post)
    claims = _verify(_hs256({"iss": ISSUER, "token_type": "access", "user_id": "99"}))
    assert claims is not None
    assert claims.sub == "phr:99"
    # RFC 7662: form body, not JSON; client-authenticated.
    assert "data" in calls and "json" not in calls
    assert calls["data"]["token_type_hint"] == "access_token"
    assert calls["auth"] == ("client", "secret")


@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect")
def test_hs256_introspection_inactive_rejected(monkeypatch):
    monkeypatch.setattr(phr.httpx, "post", lambda *a, **k: _Resp({"active": False}))
    assert _verify(_hs256({"iss": ISSUER, "token_type": "access", "user_id": "99"})) is None


@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect")
def test_hs256_introspection_refresh_token_rejected(monkeypatch):
    # active, but a refresh token must not grant API access.
    monkeypatch.setattr(phr.httpx, "post", lambda *a, **k: _Resp({"active": True, "user_id": "99"}))
    assert _verify(_hs256({"iss": ISSUER, "token_type": "refresh", "user_id": "99"})) is None


@override_settings(PHR_ISSUER=ISSUER, PHR_INTROSPECT_URL="https://phr/introspect")
def test_hs256_introspection_response_subject_outranks_payload(monkeypatch):
    # Response subject is authoritative over the (unverified) payload subject.
    monkeypatch.setattr(phr.httpx, "post", lambda *a, **k: _Resp({"active": True, "user_id": "resp-sub"}))
    claims = _verify(_hs256({"iss": ISSUER, "token_type": "access", "user_id": "payload-sub"}))
    assert claims is not None and claims.sub == "phr:resp-sub"
