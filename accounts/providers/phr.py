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

# Algorithms the introspection fallback will accept.  The portal's dev
# deployments sign with a shared HS256 secret this service does not hold, so
# such a token can only be checked by asking the portal.  Everything else —
# `none`, an asymmetric alg that should have gone down the JWKS path, a
# garbage string — is rejected locally.  Dispatching on "anything that is not
# RS256" let an unauthenticated caller select the network path, and with it an
# introspection endpoint's own laxness, by writing a single header field.
_INTROSPECTABLE_ALGS = frozenset({"HS256", "HS384", "HS512"})


class _CallBudget:
    """Fixed-window counter bounding outbound introspection calls.

    The JWKS path is protected by a refresh floor; the introspection path had
    nothing.  Routing into this provider takes only an *unverified* `iss`, and
    only *successful* verifications are cached upstream, so every failed
    attempt re-issued a blocking 5s POST.  DRF runs authentication before
    throttling (`APIView.initial`), so `AnonRateThrottle` cannot reach this.

    A budget rather than a one-call-per-interval floor: a floor would reject
    every legitimate concurrent sign-in for the rest of the window.

    What this does **not** do, deliberately:

    * It is not keyed by caller — the provider never sees the request.  So a
      caller who burns the window's budget on junk does deny legitimate
      sign-ins until it rolls over.  That is accepted because the whole
      introspection path is opt-in and off outside DEBUG/local (see
      PHR_ALLOW_INTROSPECTION); what it buys is a hard ceiling on outbound
      volume, not fairness.
    * It bounds *rate*, not *concurrency*: `max_calls` simultaneous 5s POSTs
      still occupy more workers than a deployment has.  Worker exhaustion is
      closed by the path being disabled in deployed environments, not here.
    * The window is per process, so N workers permit N × max_calls, and a
      deploy resets every window.
    * Fixed window, not a sliding one, so a burst straddling the boundary can
      reach 2 × max_calls within one interval.  Sufficient for a ceiling.
    """

    def __init__(self) -> None:
        self._window_started_at: float | None = None
        self._spent = 0
        self._warned = False

    def reset(self) -> None:
        """Drop the current window (tests)."""
        self._window_started_at = None
        self._spent = 0
        self._warned = False

    def claim(self, max_calls: int, interval: int) -> bool:
        """Consume one call from the budget; False when it is exhausted.

        Deliberately unlocked, like `_JWKSCache.get`: `self._spent += 1` is a
        read-modify-write, so under `--threads` concurrent claims can lose an
        update and overshoot.  The overshoot is bounded by the thread count and
        this guards no I/O of its own, so a lock would buy little.
        """
        now = time.monotonic()
        # `max(interval, 1)`: at 0 the window would restart on every call and
        # silently remove the ceiling — the one misconfiguration here that
        # fails open rather than closed.
        if (
            self._window_started_at is None
            or now - self._window_started_at >= max(interval, 1)
        ):
            self._window_started_at = now
            self._spent = 0
            self._warned = False
        if self._spent >= max_calls:
            # Once per window, not per request: reaching this means real
            # sign-ins are being rejected and is worth a warning, but the
            # rejected requests are attacker-drivable and one line each would
            # let anyone flood the log — the same reasoning that keeps the
            # `kid` miss below at debug.
            if not self._warned:
                self._warned = True
                logger.warning(
                    'phr introspection budget exhausted (%s per %ss); '
                    'tokens rejected until the window rolls over',
                    max_calls,
                    interval,
                )
            return False
        self._spent += 1
        return True


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
_introspect_budget = _CallBudget()


class PhrTokenProvider(TokenProvider):
    """Verify JWTs issued by the phr identity service.

    phr owns the user accounts for the service family. RS256 tokens are
    verified offline against phr's JWKS document — this is the only path a
    deployment should rely on.

    An HS256 token is signed with a shared secret this service does not hold
    and can therefore only be checked by asking the portal, which is what the
    introspection fallback is for. It is **off unless PHR_ALLOW_INTROSPECTION
    is set** (defaulting on only for DEBUG/local) because it moves the
    signature check into another service: an `active` response is taken as the
    portal vouching for the token, and where §2.2 leaves the response silent
    about the subject the *unverified* payload supplies it. A portal whose
    introspection endpoint does not fully verify signatures therefore lets a
    forged token through, so the path stays opt-in and out of production.

    Identity maps as (issuer=PHR_ISSUER, sub="phr:<phr user id>"). The
    "phr:" prefix is load-bearing, not cosmetic: `Identity.sub` is
    `unique=True` *globally*, not per issuer, so a bare portal id could
    collide with another issuer's subject. Do not remove it.
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
        #
        # Dispatch on an allowlist, never on "not RS256". The `alg` header is
        # attacker-supplied, so an `else` branch handed the caller the choice
        # of verification path.
        alg = header.get("alg")
        if alg == "RS256":
            verified = self._verify_jwks(token, header.get("kid"))
        # `isinstance` first: `get_unverified_header` only guarantees the header
        # is a JSON *object*, so `alg` can be a list or dict, and testing set
        # membership on one raises TypeError. That would surface as an uncaught
        # exception here — logged at warning by the caller, once per request,
        # for an anonymous caller who chose the header. The `else` below is
        # where a non-string belongs: rejected, at debug.
        elif isinstance(alg, str) and alg in _INTROSPECTABLE_ALGS:
            verified = self._verify_introspect(token)
        else:
            logger.debug("phr token rejected: unsupported alg=%r", alg)
            verified = None
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
                # `require`, because PyJWT's expiry check is a no-op when the
                # claim is simply absent — a token minted without `exp`, or one
                # a portal-side regression stops stamping, would otherwise be
                # accepted forever. The introspection path checks the same
                # thing locally, so both paths agree.
                options={"verify_aud": False, "require": ["exp"]},
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
        if not settings.PHR_ALLOW_INTROSPECTION:
            logger.debug("phr introspection fallback disabled; token rejected")
            return None

        # Everything checkable without the network happens first, so a token
        # that cannot possibly verify costs no outbound request.  These read an
        # *unverified* payload and may therefore only ever reject — never
        # accept, and never widen what the portal would have allowed.
        #
        # RFC 7662's own `token_type` is the OAuth2 type ("Bearer"), not the
        # access/refresh distinction, so the introspection response cannot
        # carry the check that keeps refresh tokens out.  The token can.
        payload = decode_jwt_unverified(token) or {}
        if not _is_access_token(payload):
            return None
        if not _has_live_exp(payload):
            return None

        if not _introspect_budget.claim(
            settings.PHR_INTROSPECT_MAX_CALLS,
            settings.PHR_INTROSPECT_RATE_INTERVAL,
        ):
            return None

        try:
            resp = httpx.post(
                settings.PHR_INTROSPECT_URL, json={"token": token}, timeout=5
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except Exception as exc:
            logger.warning("phr introspection failed: %s", exc)
            return None
        if not isinstance(data, dict):
            # §2.2 specifies a JSON object; a list or a bare string would
            # otherwise reach .get() below. base.py guards the same shape.
            logger.warning("phr introspection returned a non-object response")
            return None
        # `is not True`, not falsiness: §2.2 specifies a boolean, and a portal
        # answering the string "false" would otherwise read as truthy and
        # authenticate a token it just declined.
        active = data.get("active")
        if active is not True:
            if active is not False and active is not None:
                # A portal answering `1` or `"true"` now 401s every sign-in.
                # Say so, or the only symptom is a deployment that rejects
                # every token with nothing to distinguish it from `active:
                # false` — which stays at debug, being the normal answer.
                logger.warning(
                    "phr introspection returned a non-boolean `active`: %r", active
                )
            return None

        # An `active` response is the portal confirming it issued this JWT,
        # which is what makes the payload above usable despite this service
        # never having verified the signature itself.
        #
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


def _has_live_exp(claims: dict[str, Any]) -> bool:
    """Reject a missing or past `exp` locally, before any outbound call.

    Reads an unverified payload, so it may only reject: a forged token still
    has to carry a plausible expiry to be worth asking the portal about, and
    one that does not is junk the portal would refuse anyway.  `bool` is
    excluded explicitly because it is an `int` subclass in Python.
    """
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        logger.debug("phr token rejected: exp=%r", exp)
        return False
    if exp <= time.time():
        logger.debug("phr token rejected: expired")
        return False
    return True
