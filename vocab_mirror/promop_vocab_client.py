"""HTTP client for promop's vocabulary-release snapshot API (promop#334, #250).

Distinct from the patient client (``trials.services.patient_info.promop_client``):
the vocab-releases / snapshot endpoints are vocabulary-scoped, so this uses its
own OAuth client + scope (``PROMOP_VOCAB_OAUTH_*``). It reuses that module's
generic ``client_credentials`` token minter (DRY) but nothing else.

Two calls the sync needs (promop#334):
- ``get_latest_release(if_none_match=...)`` — conditional poll of
  ``GET /api/v1/vocab-releases/latest/``; a ``304`` means the mirror is current.
- ``stream_snapshot(release_id, table)`` — stream
  ``GET /api/v1/vocab-releases/{id}/snapshot/{table}/`` as NDJSON, yielding one
  parsed object per line (the final ``{"__done": true, "rows": N}`` sentinel is
  yielded too — the loader uses it to detect a truncated stream).
"""
import json
import logging
from dataclasses import dataclass

import requests
from django.conf import settings

from trials.services.patient_info.promop_client import _get_service_access_token

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
# Per-read timeout for a streamed snapshot; a stalled connection must not hang
# the sync forever, but a full concept_relationship stream is large.
STREAM_TIMEOUT_SECONDS = 300


class VocabSyncError(Exception):
    """Any hard failure talking to the vocab-releases API (fail-closed)."""


class VocabReleaseSuperseded(Exception):
    """The requested release is no longer the latest — promop's snapshot endpoint
    is latest-only and returned 409 (promop#371/#373). NOT a failure: the caller
    should re-resolve ``/latest`` and restart the sync on the new release. Kept
    distinct from ``VocabSyncError`` so the sync flow never marks the release
    FAILED for this."""


@dataclass
class LatestRelease:
    not_modified: bool
    manifest: dict | None
    etag: str | None


class PromopVocabClient:
    def __init__(self, base_url=None, oauth_client_id=None, oauth_client_secret=None,
                 oauth_scope=None, oauth_token_url=None, timeout=DEFAULT_TIMEOUT_SECONDS):
        self.base_url = (
            base_url if base_url is not None
            else (getattr(settings, 'PROMOP_VOCAB_BASE', '')
                  or getattr(settings, 'PROMOP_API_BASE', '')
                  or getattr(settings, 'PROMOP_BASE', ''))
        ).rstrip('/')
        self.oauth_client_id = (oauth_client_id if oauth_client_id is not None
                                else getattr(settings, 'PROMOP_VOCAB_OAUTH_CLIENT_ID', ''))
        self.oauth_client_secret = (oauth_client_secret if oauth_client_secret is not None
                                    else getattr(settings, 'PROMOP_VOCAB_OAUTH_CLIENT_SECRET', ''))
        self.oauth_scope = (oauth_scope if oauth_scope is not None
                            else getattr(settings, 'PROMOP_VOCAB_OAUTH_SCOPE', 'system/*.read'))
        self.oauth_token_url = (
            (oauth_token_url if oauth_token_url is not None
             else getattr(settings, 'PROMOP_VOCAB_OAUTH_TOKEN_URL', ''))
            or (f'{self.base_url}/o/token/' if self.base_url else '')
        )
        self.timeout = timeout

    def _authorization(self):
        tok = _get_service_access_token(
            self.oauth_token_url, self.oauth_client_id, self.oauth_client_secret,
            self.oauth_scope, self.timeout,
        )
        if not tok:
            raise VocabSyncError('could not mint a vocab OAuth token (fail closed)')
        return f'Bearer {tok}'

    def _headers(self, accept='application/json', extra=None):
        h = {'Authorization': self._authorization(), 'Accept': accept}
        if extra:
            h.update(extra)
        return h

    def get_latest_release(self, if_none_match=None):
        if not self.base_url:
            raise VocabSyncError('PROMOP_VOCAB_BASE / PROMOP_API_BASE is not configured')
        url = f'{self.base_url}/api/v1/vocab-releases/latest/'
        headers = self._headers()
        if if_none_match:
            headers['If-None-Match'] = if_none_match
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise VocabSyncError(f'vocab-releases/latest request failed: {exc}') from exc
        if resp.status_code == 304:
            return LatestRelease(not_modified=True, manifest=None, etag=if_none_match)
        if resp.status_code == 404:
            raise VocabSyncError('no published vocab release yet (404)')
        if not resp.ok:
            raise VocabSyncError(f'vocab-releases/latest -> {resp.status_code} {resp.reason}')
        try:
            manifest = resp.json()
        except ValueError as exc:
            raise VocabSyncError('vocab-releases/latest returned non-JSON') from exc
        return LatestRelease(not_modified=False, manifest=manifest, etag=resp.headers.get('ETag'))

    def stream_snapshot(self, release_id, table):
        """Return an iterator of parsed NDJSON objects for one table snapshot
        (incl. the sentinel).

        The request and its **status + header checks are eager** (done here, before
        the iterator is returned), NOT inside the generator: a supersede (409) or a
        bad response is raised at call time, so it can never abort a mid-flight COPY
        on the consumer side. Only the row iteration is lazy.
        """
        if not self.base_url:
            raise VocabSyncError('PROMOP_VOCAB_BASE / PROMOP_API_BASE is not configured')
        url = f'{self.base_url}/api/v1/vocab-releases/{int(release_id)}/snapshot/{table}/'
        try:
            # Accept ndjson but also `*/*`: the client parses the NDJSON stream
            # itself regardless of the response media type, and promop's snapshot
            # view streams `application/x-ndjson` without registering a matching
            # DRF renderer — a bare `application/x-ndjson` Accept fails content
            # negotiation there with 406. Offering `*/*` keeps the client robust
            # to the server's renderer config.
            resp = requests.get(
                url, headers=self._headers(accept='application/x-ndjson, */*'),
                stream=True, timeout=STREAM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise VocabSyncError(f'snapshot {table} request failed: {exc}') from exc

        if resp.status_code == 409:
            # Latest-only snapshots (promop#371/#373): a newer release published
            # since we resolved /latest. Re-resolve + restart, never fail closed.
            resp.close()
            raise VocabReleaseSuperseded(
                f'snapshot {table}: release {int(release_id)} is no longer the '
                f'latest published release (409)')
        if not resp.ok:
            code, reason = resp.status_code, resp.reason
            resp.close()
            raise VocabSyncError(f'snapshot {table} -> {code} {reason}')
        # Every snapshot response stamps the release it reflects (promop#373);
        # verify it is the one we asked for, else promop served a different
        # release's rows under our label — fail closed.
        served = resp.headers.get('X-Vocab-Release-Id')
        if served is not None and str(served).strip() != str(int(release_id)):
            resp.close()
            raise VocabSyncError(
                f'snapshot {table}: X-Vocab-Release-Id {served!r} != requested '
                f'release {int(release_id)}')
        return self._iter_snapshot(resp, table)

    @staticmethod
    def _iter_snapshot(resp, table):
        # `with resp` closes the connection when the generator finishes OR is
        # closed early (the loader breaks at the sentinel), so a streamed response
        # is never left checked out.
        with resp:
            try:
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError as exc:
                        raise VocabSyncError(f'snapshot {table}: malformed NDJSON line') from exc
            except requests.RequestException as exc:
                # A mid-stream network failure (dropped connection, read timeout).
                raise VocabSyncError(f'snapshot {table} stream error: {exc}') from exc
