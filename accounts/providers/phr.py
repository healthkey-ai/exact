from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from django.conf import settings
from jwt import PyJWK
from rest_framework.exceptions import AuthenticationFailed

from .base import TokenClaims, TokenProvider, decode_jwt_unverified

logger = logging.getLogger(__name__)


class _JWKSCache:
    """Fetch-once JWKS cache with TTL and rate-limited kid-miss refresh."""

    def __init__(self) -> None:
        self._keys: dict[str, PyJWK] = {}
        # Last *successful* fetch, against which the TTL is measured.
        self._fetched_at: float | None = None
        # Last fetch *attempt*, successful or not — this is what rate-limits
        # refreshes.  Tracked separately so a failing JWKS endpoint doesn't
        # leave the cache permanently stale and refetching on every request.
        self._attempted_at: float | None = None

    def reset(self) -> None:
        """Drop all cached state (tests; not used at runtime)."""
        self._keys = {}
        self._fetched_at = None
        self._attempted_at = None

    def get(
        self, kid: str | None, url: str, ttl: int, min_refresh_interval: int
    ) -> PyJWK | None:
        now = time.monotonic()
        expired = self._fetched_at is None or now - self._fetched_at > ttl
        unknown_kid = not self._keys or (kid is not None and kid not in self._keys)
        if expired or unknown_kid:
            # Rate-limited deliberately.  Routing into this provider only takes
            # an *unverified* `iss` claim, so an unauthenticated caller can send
            # unsigned tokens bearing an arbitrary `kid` — without this floor,
            # each one would drive its own blocking outbound fetch.  A failed
            # fetch is throttled by the same clock, so an unreachable JWKS
            # endpoint costs one request per interval rather than one per token.
            #
            # Deliberately unlocked, though this is check-then-act.  The
            # deployment runs sync workers, so there is no intra-process
            # concurrency today; and under --threads every way of closing the
            # window is worse than leaving it open.  Holding a lock across the
            # fetch queues concurrent callers behind a 5s call; claiming the
            # fetch and letting the others fall through rejects valid tokens
            # that the in-flight document would have verified.  Open, the only
            # cost is that a concurrent burst can fetch more than once — the
            # bound stays one fetch per interval *per burst*, which is what
            # this is for.  Dict rebinding and a float write are atomic, so
            # readers see the old key set or the new one, never a torn one.
            throttled = (
                self._attempted_at is not None
                and now - self._attempted_at < min_refresh_interval
            )
            if not throttled:
                self._refresh(url)

        if kid is not None:
            return self._keys.get(kid)
        # Token has no kid header — usable only when exactly one key is published.
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return None

    def _refresh(self, url: str) -> None:
        self._attempted_at = time.monotonic()
        try:
            resp = httpx.get(url, timeout=5)
            resp.raise_for_status()
            keys = resp.json().get("keys", [])
        except Exception as exc:
            logger.warning("phr JWKS fetch failed (%s): %s", url, exc)
            return

        if not isinstance(keys, list):
            # `{"keys": null}` from a serializer emitting null for an empty
            # list, or an object instead of an array. Treat as empty so the
            # "published no usable key" warning below reports it, rather than
            # throwing out of here into a bare "verify failed" log line that
            # names neither the URL nor the cause.
            logger.warning("phr JWKS at %s: `keys` is not a list", url)
            keys = []

        # Per key, not one comprehension: PyJWK() raises on a key type it
        # cannot build (an Ed25519 entry alongside the RSA ones, say), and a
        # single such entry would otherwise discard the whole document —
        # including the key the portal is actually signing with. The `try`
        # covers reading the entry too, since nothing guarantees it is a dict.
        parsed: dict[str, PyJWK] = {}
        for k in keys:
            try:
                kid = k.get("kid")
                if not kid:
                    continue
                parsed[kid] = PyJWK(k)
            except Exception as exc:
                logger.warning("phr JWKS: skipping unusable key: %s", exc)
        if not parsed:
            # Fetched fine and got nothing usable — a misconfigured
            # PHR_JWKS_URL looks exactly like this, and every sign-in will now
            # fail. Say so once per refresh; the per-token line stays at debug
            # because `kid` there is attacker-controlled.
            logger.warning("phr JWKS at %s published no usable key", url)
            # Keep whatever we already had rather than installing nothing: a
            # momentarily broken document would otherwise evict a key that
            # still verifies, and advancing `_fetched_at` would hold that
            # outage for the whole TTL. Leaving both alone means the next
            # request retries as soon as the refresh interval allows.
            return
        self._keys = parsed
        self._fetched_at = time.monotonic()


_jwks_cache = _JWKSCache()


class PhrTokenProvider(TokenProvider):
    """Verify JWTs issued by the phr identity service.

    phr owns the user accounts for the service family. RS256 tokens are
    verified offline against phr's JWKS document; anything else (e.g. an
    HS256 dev deployment) falls back to phr's RFC 7662 introspection
    endpoint.

    Identity maps as (issuer=PHR_ISSUER, sub="phr:<phr user id>"). The
    "phr:" prefix is descriptive only: Identity is keyed on the composite
    (issuer, sub) since #182, so a bare portal id could not collide with
    another issuer's subject anyway.
    """

    def can_handle(self, token: str, unverified_payload: dict[str, Any] | None) -> bool:
        if unverified_payload is None:
            return False
        return unverified_payload.get("iss", "") == settings.PHR_ISSUER

    def verify(self, token: str) -> TokenClaims | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError:
            return None

        # Each path resolves its own subject: the two sources name it
        # differently and neither order is right for both. A SimpleJWT payload
        # carries `user_id`; an RFC 7662 response carries `sub` — optionally,
        # §2.2 does not require it — and may carry neither.
        if header.get("alg") == "RS256":
            verified = self._verify_jwks(token, header.get("kid"))
        else:
            verified = self._verify_introspect(token)
        if verified is None:
            return None
        claims, sub = verified

        return TokenClaims(
            issuer=settings.PHR_ISSUER,
            sub=f"phr:{sub}",
            email=claims.get("email") or "",
            name=None,
            raw=claims,
        )

    def _verify_jwks(
        self, token: str, kid: str | None
    ) -> tuple[dict[str, Any], str] | None:
        key = _jwks_cache.get(
            kid,
            settings.PHR_JWKS_URL,
            settings.PHR_JWKS_CACHE_TTL,
            settings.PHR_JWKS_MIN_REFRESH_INTERVAL,
        )
        if key is None:
            # debug, not warning: `kid` is attacker-controlled and this is the
            # branch an unauthenticated flood lands on, so a higher level would
            # let anyone write one log line per request.
            logger.debug("phr JWKS has no usable key (kid=%s)", kid)
            return None
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=settings.PHR_ISSUER,
                options={"verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed("phr token expired") from exc
        except jwt.InvalidTokenError as exc:
            logger.debug("phr token rejected: %s", exc)
            return None
        if not _is_access_token(claims):
            return None
        sub = claims.get("user_id") or claims.get("sub")
        # `not sub`, not `is None`: the selection above falls through falsy
        # values, so a terminal empty string would otherwise be accepted and
        # collapse every such user onto a shared "phr:" Identity.
        return (claims, str(sub)) if sub else None

    def _verify_introspect(self, token: str) -> tuple[dict[str, Any], str] | None:
        try:
            resp = httpx.post(
                settings.PHR_INTROSPECT_URL, json={"token": token}, timeout=5
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except Exception as exc:
            logger.warning("phr introspection failed: %s", exc)
            return None
        if not data.get("active"):
            return None

        # RFC 7662's own `token_type` is the OAuth2 type ("Bearer"), not the
        # access/refresh distinction, so the introspection response cannot
        # carry the check that keeps refresh tokens out.  The token can: an
        # `active` response is the portal confirming it issued this JWT, which
        # makes its payload authentic even though this service never verified
        # the signature itself.
        payload = decode_jwt_unverified(token) or {}
        if not _is_access_token(payload):
            return None

        # Response before payload — the response is authoritative where it
        # speaks, and a `user_id` that exists only in the payload must not
        # outrank it.  But §2.2 makes every claim optional and some portals
        # answer with nothing but `active`, so the payload stays as a fallback:
        # without it those deployments 401 on a token the portal just affirmed.
        #
        # Within each source, `user_id` before `sub`, matching the JWKS path
        # exactly.  If the two paths disagreed, moving a deployment off the
        # HS256 fallback onto RS256 would silently re-provision every user
        # under a second Identity — same issuer, different sub, so nothing
        # would flag it.  A portal that publishes only `sub`, differing from
        # its `user_id`, still splits; that cannot be resolved here and needs
        # the portal's contract pinned down.
        sub = (
            data.get("user_id")
            or data.get("sub")
            or payload.get("user_id")
            or payload.get("sub")
        )
        if not sub:  # see the note on the JWKS path
            logger.warning("phr introspection returned no subject")
            return None
        return {**payload, **data}, str(sub)


def _is_access_token(claims: dict[str, Any]) -> bool:
    """Only access tokens grant API access — refresh tokens are for phr.

    Fails closed: a token that does not say what it is does not get in.  The
    portal issues SimpleJWT tokens, which always carry the claim.
    """
    if claims.get("token_type") == "access":
        return True
    logger.debug("phr token rejected: token_type=%r", claims.get("token_type"))
    return False
