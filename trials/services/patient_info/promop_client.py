"""HTTP client for PROMOP/promop's patient endpoint.

Used by `resolve_patient_info` when a request carries `?person_id=` (or a body
field `person_id`) instead of an inline `patient_info` payload.

Always reads `GET /api/v1/patient-records/{person_id}/` (#387). Only the
credential differs, chosen by configuration (#237):
- **static service token** (preferred, #448): `PROMOP_SERVICE_TOKEN` sent as a
  bearer — EXACT's own named promop credential. Only this path authenticates as
  `urn:service|exact`, because promop builds that identity from the matched
  credential's service id. promop accepts it on v1, so it is no reason to fall
  back to the deprecated `/api/patient-info/` prefix.
- **OAuth2** (alternative): when `PROMOP_OAUTH_CLIENT_ID` / `_SECRET` are set, the
  client mints via `client_credentials` against promop `/o/token/` instead. Its
  principal is whatever user the OAuth Application is bound to — a different
  identity in promop's audit. It wins whenever both are configured, so leave it
  unset unless it is deliberately what you want.

Service identity (#448): whichever credential is configured, it authenticates
EXACT *as a service* (`urn:service|exact`) and nothing else. promop no longer
honours an unsigned `actor_iss`/`actor_sub` claim from a service credential, so
this client never sends one — it sends no request body and no provenance headers
at all, only `Accept` and `Authorization`. Attributing a read to an end user
would require forwarding that user's own verified token (or a token exchanged
for it), which this client does not do; see the authorization boundary in
`resolve.py`. Missing or half-configured credentials fail closed: no credential
means no request, never an anonymous one.

Either way `fetch_patient(person_id)` returns the flat row dict (the shape
`normalize_promop_row` expects) or `None` on any error path (network failure,
4xx/5xx, malformed JSON, missing config, OAuth token failure). `None` is this
client's only failure vocabulary; deciding what it means for the HTTP response
belongs to the resolver, which turns it into a 502 rather than a patientless
search (#448) — see `resolve.py`.

Config comes from Django settings (each read from the matching env var, empty
defaults): `PROMOP_BASE`, `PROMOP_SERVICE_TOKEN` (the named token), and
`PROMOP_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` / `_SCOPE` / `_TOKEN_URL` (OAuth).
Uses `requests` (already in requirements) rather than adding `httpx`.
"""
import logging
import threading
import time

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SECONDS = 10
# Refresh a service token this many seconds before its stated expiry, so an
# in-flight request never rides a just-expired token.
_TOKEN_EXPIRY_MARGIN_SECONDS = 30

# Process-local cache of service access tokens, keyed by (token_url, client_id,
# scope). Tokens are short-lived; a per-process cache is enough (no need to
# share across workers) and avoids a token round-trip on every patient fetch.
_token_lock = threading.Lock()
_token_cache: dict[tuple, tuple[str, float]] = {}


def _clear_token_cache() -> None:
    """Drop all cached service tokens (used by tests)."""
    with _token_lock:
        _token_cache.clear()


def _get_service_access_token(token_url, client_id, client_secret, scope, timeout):
    """Return a cached-or-freshly-minted OAuth2 client_credentials access token.

    Returns `None` (never raises) on any failure so callers fail closed to a
    missing-patient result. The token endpoint is hit at most once per token
    lifetime per process; the fetch is serialized under a lock to avoid a
    refresh stampede.
    """
    key = (token_url, client_id, scope)
    now = time.time()
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        # HTTP Basic client auth + grant_type=client_credentials (RFC 6749 §4.4).
        # allow_redirects=False: never let the token endpoint bounce the request
        # (and the Basic-auth client_secret) to another host/scheme.
        try:
            resp = requests.post(
                token_url,
                data={'grant_type': 'client_credentials', 'scope': scope},
                auth=(client_id, client_secret),
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning('PromopClient OAuth token request failed: %s', exc)
            return None
        if not resp.ok:
            logger.warning('PromopClient OAuth token non-OK response: %s %s',
                           resp.status_code, resp.reason)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning('PromopClient OAuth token non-JSON body')
            return None

        access_token = data.get('access_token') if isinstance(data, dict) else None
        if not access_token:
            logger.warning('PromopClient OAuth token response missing access_token')
            return None
        try:
            ttl = float(data.get('expires_in') or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        _token_cache[key] = (access_token, now + max(0.0, ttl - _TOKEN_EXPIRY_MARGIN_SECONDS))
        return access_token


class PromopClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 oauth_client_id: str | None = None, oauth_client_secret: str | None = None,
                 oauth_scope: str | None = None, oauth_token_url: str | None = None):
        self.base_url = (base_url if base_url is not None
                         else getattr(settings, 'PROMOP_BASE', '')).rstrip('/')
        self.token = token if token is not None else getattr(settings, 'PROMOP_SERVICE_TOKEN', '')
        self.timeout = timeout
        self.oauth_client_id = (oauth_client_id if oauth_client_id is not None
                                else getattr(settings, 'PROMOP_OAUTH_CLIENT_ID', ''))
        self.oauth_client_secret = (oauth_client_secret if oauth_client_secret is not None
                                    else getattr(settings, 'PROMOP_OAUTH_CLIENT_SECRET', ''))
        self.oauth_scope = (oauth_scope if oauth_scope is not None
                            else getattr(settings, 'PROMOP_OAUTH_SCOPE', 'patient/*.read'))
        self.oauth_token_url = (
            (oauth_token_url if oauth_token_url is not None
             else getattr(settings, 'PROMOP_OAUTH_TOKEN_URL', ''))
            or (f'{self.base_url}/o/token/' if self.base_url else '')
        )
        # A half-configured OAuth setup (exactly one of id/secret) used to drop
        # to the static token. Under per-service credentials that silently
        # substitutes one service identity for another — a dropped secret would
        # revive the shared credential we are migrating off (#448) — so it is
        # now a hard misconfiguration: no credential, no request.
        if self.oauth_config_incomplete:
            logger.warning(
                'PromopClient: partial OAuth config (only %s set); patient '
                'requests will be refused until both are set.',
                'client_id' if self.oauth_client_id else 'client_secret',
            )

    @property
    def use_oauth(self) -> bool:
        """OAuth when both client credentials are set; else the static token."""
        return bool(self.oauth_client_id and self.oauth_client_secret)

    @property
    def oauth_config_incomplete(self) -> bool:
        """Exactly one of client_id/client_secret is set — a broken credential.

        Distinct from `not use_oauth`: no OAuth config at all is the valid
        static-token mode, while half of one is a misconfiguration that must not
        fall through to another credential (#448).
        """
        return bool(self.oauth_client_id) != bool(self.oauth_client_secret)

    def _patient_url(self, person_id_int: int) -> str:
        # Always v1. The URL is deliberately NOT tied to `use_oauth` (#387):
        # promop routes both prefixes to the same DRF viewset, so
        # `ScopedTokenPermission` short-circuits on the static service token and
        # authenticates it against v1 exactly as against the legacy path. Picking
        # the URL off the auth mode left every OAuth-less deployment talking to a
        # sunsetting endpoint for no reason.
        return f'{self.base_url}/api/v1/patient-records/{person_id_int}/'

    def _authorization(self) -> str | None:
        """Bearer header value, or None when it can't be built (caller fails closed).

        None means *no usable credential* — the caller must then make no request
        at all (#448). Three ways to get there: the OAuth pair is half-configured,
        an OAuth token can't be minted, or static-token mode has an empty token.
        """
        if self.oauth_config_incomplete:
            # __init__ already warned about the config, and `fetch_patient`
            # warns about the refused request — no third line per request.
            return None
        if self.use_oauth:
            tok = _get_service_access_token(
                self.oauth_token_url, self.oauth_client_id,
                self.oauth_client_secret, self.oauth_scope, self.timeout,
            )
            return f'Bearer {tok}' if tok else None
        return f'Bearer {self.token}' if self.token else None

    def fetch_patient(self, person_id) -> dict | None:
        """Fetch the patient row and return the flat JSON row (or None on any error).

        The response wraps the row in a `{"patient_info": {...}}` envelope with a
        sibling `user` block (identical on the legacy prefix this used to call —
        the two share one viewset). This method
        unwraps the `patient_info` envelope (ignoring siblings), so callers always
        receive a flat row matching `normalize_promop_row` — without unwrapping,
        every real field would be nested one level too deep and silently dropped
        (#144).

        `person_id` MUST be a positive integer (the patient primary-key shape);
        anything else returns None without a network call — a guard against URL
        path injection that would otherwise leak the Bearer token to a crafted path.

        Returns None when: `PROMOP_BASE` is unset; no usable credential is
        configured (an empty static token, a half-configured OAuth pair, or an
        OAuth token that can't be minted — #448); the network call fails; the
        status is non-2xx; or the body isn't a JSON object. Logs at WARNING —
        that log is where an operator tells these apart, because the caller only
        ever sees `None`.
        """
        if not self.base_url:
            logger.warning(
                'PromopClient.fetch_patient called with no PROMOP_BASE configured; '
                'returning None (person_id=%s)', person_id,
            )
            return None

        try:
            person_id_int = int(person_id)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                'PromopClient.fetch_patient rejected non-integer person_id %r', person_id,
            )
            return None
        if person_id_int <= 0:
            logger.warning(
                'PromopClient.fetch_patient rejected non-positive person_id %r', person_id,
            )
            return None

        authorization = self._authorization()
        if authorization is None:
            # No credential ⇒ no request, in every mode (#448). The static-token
            # mode used to send an unauthenticated GET here, which promop answers
            # with a 401 anyway — but it put a person_id on the wire (and in
            # promop's logs) under no identity at all, and it read as a working
            # transport in local setups whose promop happened to allow anonymous
            # reads. An unauthenticated patient read is never what we want.
            logger.warning(
                'PromopClient has no usable credential; returning None without a '
                'request (person_id=%s)', person_id,
            )
            return None

        # Only these two headers, and never a body: a service credential proves
        # the service, so there is nothing for an actor/provenance field to say
        # that promop would honour (#448).
        headers = {'Accept': 'application/json', 'Authorization': authorization}

        url = self._patient_url(person_id_int)
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning('PromopClient network error for person_id=%s: %s', person_id, exc)
            return None

        if not response.ok:
            logger.warning(
                'PromopClient non-OK response for person_id=%s: %s %s',
                person_id, response.status_code, response.reason,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning('PromopClient non-JSON body for person_id=%s', person_id)
            return None

        if not isinstance(data, dict):
            logger.warning(
                'PromopClient response for person_id=%s is %s, expected dict',
                person_id, type(data).__name__,
            )
            return None

        # Unwrap the `{"patient_info": {...}}` envelope (both v0 and v1 emit it)
        # so the adapter receives a flat row. Unwrap whenever a dict-valued
        # `patient_info` key is present — not only when it is the sole key — so
        # v1's sibling `user` block (and any other envelope metadata) is ignored
        # rather than reverting to the all-defaults bug. A flat psql-path row has
        # no `patient_info` key and passes straight through (#144).
        inner = data.get('patient_info')
        if isinstance(inner, dict):
            logger.debug(
                'PromopClient unwrapped patient_info envelope for person_id=%s', person_id,
            )
            return inner

        return data
