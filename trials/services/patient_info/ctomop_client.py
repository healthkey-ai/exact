"""HTTP client for CTOMOP/promop's patient endpoint.

Used by `resolve_patient_info` when a request carries `?person_id=` (or a body
field `person_id`) instead of an inline `patient_info` payload.

Two transport modes, chosen by configuration (#237):
- **v1 + OAuth2** (preferred): when `CTOMOP_OAUTH_CLIENT_ID` / `_SECRET` are set,
  the client authenticates via OAuth2 `client_credentials` against promop
  `/o/token/` (scope `patient/*.read`) and reads
  `GET /api/v1/patient-records/{person_id}/`.
- **legacy** (deprecated, sunsets 2026-09-01): otherwise it falls back to the
  static `CTOMOP_SERVICE_TOKEN` and `GET /api/patient-info/{person_id}/`.

Either way `fetch_patient(person_id)` returns the flat row dict (the shape
`normalize_ctomop_row` expects) or `None` on any error path (network failure,
4xx/5xx, malformed JSON, missing config, OAuth token failure). Returning `None`
rather than raising lets the resolver treat the failure like a missing payload —
the caller can proceed without patient context (e.g. public trial browsing).

Config comes from Django settings (each read from the matching env var, empty
defaults): `CTOMOP_BASE`, `CTOMOP_SERVICE_TOKEN` (legacy), and
`CTOMOP_OAUTH_CLIENT_ID` / `_CLIENT_SECRET` / `_SCOPE` / `_TOKEN_URL` (OAuth).
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
            logger.warning('CtomopClient OAuth token request failed: %s', exc)
            return None
        if not resp.ok:
            logger.warning('CtomopClient OAuth token non-OK response: %s %s',
                           resp.status_code, resp.reason)
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.warning('CtomopClient OAuth token non-JSON body')
            return None

        access_token = data.get('access_token') if isinstance(data, dict) else None
        if not access_token:
            logger.warning('CtomopClient OAuth token response missing access_token')
            return None
        try:
            ttl = float(data.get('expires_in') or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        _token_cache[key] = (access_token, now + max(0.0, ttl - _TOKEN_EXPIRY_MARGIN_SECONDS))
        return access_token


class CtomopClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 oauth_client_id: str | None = None, oauth_client_secret: str | None = None,
                 oauth_scope: str | None = None, oauth_token_url: str | None = None):
        self.base_url = (base_url if base_url is not None
                         else getattr(settings, 'CTOMOP_BASE', '')).rstrip('/')
        self.token = token if token is not None else getattr(settings, 'CTOMOP_SERVICE_TOKEN', '')
        self.timeout = timeout
        self.oauth_client_id = (oauth_client_id if oauth_client_id is not None
                                else getattr(settings, 'CTOMOP_OAUTH_CLIENT_ID', ''))
        self.oauth_client_secret = (oauth_client_secret if oauth_client_secret is not None
                                    else getattr(settings, 'CTOMOP_OAUTH_CLIENT_SECRET', ''))
        self.oauth_scope = (oauth_scope if oauth_scope is not None
                            else getattr(settings, 'CTOMOP_OAUTH_SCOPE', 'patient/*.read'))
        self.oauth_token_url = (
            (oauth_token_url if oauth_token_url is not None
             else getattr(settings, 'CTOMOP_OAUTH_TOKEN_URL', ''))
            or (f'{self.base_url}/o/token/' if self.base_url else '')
        )
        # A half-configured OAuth setup (exactly one of id/secret) silently
        # drops to the legacy path — surface it so the misconfiguration isn't
        # mistaken for a working OAuth deployment.
        if bool(self.oauth_client_id) != bool(self.oauth_client_secret):
            logger.warning(
                'CtomopClient: partial OAuth config (only %s set); falling back '
                'to legacy static-token transport.',
                'client_id' if self.oauth_client_id else 'client_secret',
            )

    @property
    def use_oauth(self) -> bool:
        """v1 + OAuth when both client credentials are configured; else legacy."""
        return bool(self.oauth_client_id and self.oauth_client_secret)

    def _patient_url(self, person_id_int: int) -> str:
        if self.use_oauth:
            return f'{self.base_url}/api/v1/patient-records/{person_id_int}/'
        return f'{self.base_url}/api/patient-info/{person_id_int}/'

    def _authorization(self) -> str | None:
        """Bearer header value, or None when it can't be built (caller fails closed)."""
        if self.use_oauth:
            tok = _get_service_access_token(
                self.oauth_token_url, self.oauth_client_id,
                self.oauth_client_secret, self.oauth_scope, self.timeout,
            )
            return f'Bearer {tok}' if tok else None
        return f'Bearer {self.token}' if self.token else None

    def fetch_patient(self, person_id) -> dict | None:
        """Fetch the patient row and return the flat JSON row (or None on any error).

        v1 (`/api/v1/patient-records/{id}/`) and legacy (`/api/patient-info/{id}/`)
        both wrap the row in a `{"patient_info": {...}}` envelope; v1 additionally
        carries a sibling `user` block and drops `patient_info_id`. This method
        unwraps the `patient_info` envelope (ignoring siblings), so callers always
        receive a flat row matching `normalize_ctomop_row` — without unwrapping,
        every real field would be nested one level too deep and silently dropped
        (#144).

        `person_id` MUST be a positive integer (the patient primary-key shape);
        anything else returns None without a network call — a guard against URL
        path injection that would otherwise leak the Bearer token to a crafted path.

        Returns None when: `CTOMOP_BASE` is unset; the OAuth token can't be
        obtained (OAuth mode); the network call fails; the status is non-2xx; or
        the body isn't a JSON object. Logs at WARNING so failures surface without
        short-circuiting the caller.
        """
        if not self.base_url:
            logger.warning(
                'CtomopClient.fetch_patient called with no CTOMOP_BASE configured; '
                'returning None (person_id=%s)', person_id,
            )
            return None

        try:
            person_id_int = int(person_id)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                'CtomopClient.fetch_patient rejected non-integer person_id %r', person_id,
            )
            return None
        if person_id_int <= 0:
            logger.warning(
                'CtomopClient.fetch_patient rejected non-positive person_id %r', person_id,
            )
            return None

        headers = {'Accept': 'application/json'}
        authorization = self._authorization()
        if self.use_oauth and authorization is None:
            logger.warning(
                'CtomopClient could not obtain an OAuth token; returning None (person_id=%s)',
                person_id,
            )
            return None
        if authorization:
            headers['Authorization'] = authorization

        url = self._patient_url(person_id_int)
        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning('CtomopClient network error for person_id=%s: %s', person_id, exc)
            return None

        if not response.ok:
            logger.warning(
                'CtomopClient non-OK response for person_id=%s: %s %s',
                person_id, response.status_code, response.reason,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning('CtomopClient non-JSON body for person_id=%s', person_id)
            return None

        if not isinstance(data, dict):
            logger.warning(
                'CtomopClient response for person_id=%s is %s, expected dict',
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
                'CtomopClient unwrapped patient_info envelope for person_id=%s', person_id,
            )
            return inner

        return data
