"""HTTP client for promop's concept-graph API (#234; promop ADR 0001 / promop#279).

Access is **API + cache**: promop is the single source of truth for vocabulary and
concept-graph data; EXACT keeps no local copy. This module is slice 1 — the thin API
client. The cache layer (DB-backed, release-pinned) and the matcher/backfill wiring
land in follow-up slices of #234.

**Failure contract differs deliberately from `PromopClient`.** `PromopClient.fetch_patient`
returns ``None`` on any failure because a missing patient payload is benign (public trial
browsing still works). The concept graph instead feeds a PERSISTED eligibility projection
(trial-side backfill) and per-trial matching; silently returning an empty expansion would
fail *open* — dropping a required-component constraint or persisting a partial projection.
So this client **raises** ``ConceptGraphUnavailable`` on any hard failure, and surfaces
promop's per-source node-cap ``truncated`` signal in the result. Callers apply the
fail-closed policy explicitly:

- backfill: let ``ConceptGraphUnavailable`` / a non-empty ``truncated`` propagate → fail the
  run and preserve existing column values (never persist a partial projection);
- request-time: catch → treat as ``unknown`` (never fail-open to matched).

Config falls back to the ``PROMOP_*`` settings (same promop host) until the dedicated
OAuth2 path (#237) lands. Cache identity must eventually key on a promop **release id**
(promop#279); until promop emits one, callers can only observe per-vocabulary
``versions`` — see the ``ConceptGraphResult.versions`` TODO.
"""
import logging
from dataclasses import dataclass

import requests
from django.conf import settings


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10
# promop's batch graph endpoint accepts at most this many source ids per request.
GRAPH_MAX_SOURCE_IDS = 200
_DIRECTIONS = frozenset({'ancestors', 'descendants'})


class ConceptGraphError(Exception):
    """Base class for concept-graph client failures."""


class ConceptGraphUnavailable(ConceptGraphError):
    """promop could not be reached or returned an unusable response.

    Raised for: unconfigured base URL, network error/timeout, non-2xx status,
    non-JSON body, a non-object payload, or a structurally invalid payload
    (missing/non-dict ``results``, a non-list node list, a non-object node, or a
    non-list ``truncated``). Callers fail-closed on this — never fall through to
    an empty expansion, which would fail open.
    """


@dataclass(frozen=True)
class ConceptGraphResult:
    """Outcome of a graph traversal.

    ``groups`` maps each source concept_id to its de-duplicated, sorted list of
    related concept_ids. ``truncated`` lists source ids whose result hit promop's
    per-source node cap (their ``groups`` entry is incomplete — treat as
    fail-closed at the call site). ``versions`` is the set of per-vocabulary
    versions seen across the returned nodes (observability only; the real cache
    key is a promop release id once promop#279 emits one).
    """
    groups: dict[int, list[int]]
    truncated: list[int]
    versions: frozenset[str]


def _positive_ints(values):
    """Coerce an iterable to a de-duplicated, order-preserving list of positive ints.

    Non-integer / non-positive values are dropped (they cannot be concept_ids and
    must not reach the URL as query params)."""
    seen: set[int] = set()
    out: list[int] = []
    for v in values or []:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv > 0 and iv not in seen:
            seen.add(iv)
            out.append(iv)
    return out


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class ConceptGraphClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = (
            base_url if base_url is not None
            else (getattr(settings, 'PROMOP_API_BASE', '')
                  or getattr(settings, 'PROMOP_BASE', ''))
        ).rstrip('/')
        self.token = (
            token if token is not None
            else getattr(settings, 'PROMOP_SERVICE_TOKEN', '')
        )
        self.timeout = timeout

    # ── public API ──────────────────────────────────────────────────────────

    def descendants(self, concept_ids, relationship_ids=None, **kwargs) -> ConceptGraphResult:
        """Downstream traversal (regimen → components, class → members)."""
        return self.expand(concept_ids, 'descendants', relationship_ids=relationship_ids, **kwargs)

    def ancestors(self, concept_ids, relationship_ids=None, **kwargs) -> ConceptGraphResult:
        """Upstream traversal (component → drug class / superclass)."""
        return self.expand(concept_ids, 'ancestors', relationship_ids=relationship_ids, **kwargs)

    def expand(self, concept_ids, direction, relationship_ids=None,
               vocabulary_ids=None, concept_class_ids=None,
               max_levels=None) -> ConceptGraphResult:
        """Batch-traverse the concept graph via ``GET /api/v1/concepts/graph/``.

        Chunks source ids to promop's ``GRAPH_MAX_SOURCE_IDS`` cap and merges the
        responses. Raises ``ValueError`` on a bad ``direction`` and
        ``ConceptGraphUnavailable`` on any transport/payload failure. Returns an
        empty result (no network call) when no valid source ids are supplied.
        """
        if direction not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}, got {direction!r}")

        source_ids = _positive_ints(concept_ids)
        if not source_ids:
            return ConceptGraphResult(groups={}, truncated=[], versions=frozenset())
        if not self.base_url:
            raise ConceptGraphUnavailable('PROMOP_API_BASE / PROMOP_BASE is not configured')

        groups: dict[int, set[int]] = {cid: set() for cid in source_ids}
        truncated: set[int] = set()
        versions: set[str] = set()

        for chunk in _chunks(source_ids, GRAPH_MAX_SOURCE_IDS):
            params: list[tuple[str, object]] = [('direction', direction)]
            params += [('concept_id', cid) for cid in chunk]
            params += [('relationship_id', r) for r in (relationship_ids or [])]
            params += [('vocabulary_id', v) for v in (vocabulary_ids or [])]
            params += [('concept_class_id', c) for c in (concept_class_ids or [])]
            if max_levels is not None:
                params.append(('max_levels', max_levels))

            payload = self._get(params)

            # Strict structural validation. A 2xx body with the wrong shape
            # (missing/!dict `results`, a non-list node list, a non-object node,
            # a non-list `truncated`) is an UNUSABLE response — surface it as
            # ConceptGraphUnavailable so callers keep their fail-closed policy,
            # rather than letting a raw AttributeError/TypeError escape or a
            # `results`-less body read as an empty (fail-open) expansion.
            results = payload.get('results')
            if not isinstance(results, dict):
                raise ConceptGraphUnavailable("response is missing a 'results' object")
            for key, nodes in results.items():
                try:
                    src = int(key)
                except (TypeError, ValueError):
                    continue
                if src not in groups:
                    # Ignore source ids we did not request (echo / injection).
                    continue
                if not isinstance(nodes, list):
                    raise ConceptGraphUnavailable(f"'results[{key}]' is not a list")
                bucket = groups[src]
                for node in nodes:
                    if not isinstance(node, dict):
                        raise ConceptGraphUnavailable("a graph node is not an object")
                    ncid = node.get('concept_id')
                    if ncid is not None:
                        try:
                            bucket.add(int(ncid))
                        except (TypeError, ValueError):
                            pass
                    ver = node.get('vocabulary_version')
                    if ver:
                        versions.add(ver)

            raw_truncated = payload.get('truncated', [])
            if not isinstance(raw_truncated, list):
                raise ConceptGraphUnavailable("'truncated' is not a list")
            for t in raw_truncated:
                try:
                    tv = int(t)
                except (TypeError, ValueError):
                    continue
                if tv in groups:  # only requested ids
                    truncated.add(tv)

        if truncated:
            logger.warning('ConceptGraphClient: %d source concept(s) truncated at the '
                           'node cap (%s): %s', len(truncated), direction, sorted(truncated))

        return ConceptGraphResult(
            groups={cid: sorted(v) for cid, v in groups.items()},
            truncated=sorted(truncated),
            versions=frozenset(versions),
        )

    # ── internal ────────────────────────────────────────────────────────────

    def _get(self, params) -> dict:
        url = f'{self.base_url}/api/v1/concepts/graph/'
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning('ConceptGraphClient network error: %s', exc)
            raise ConceptGraphUnavailable(f'network error: {exc}') from exc

        if not resp.ok:
            logger.warning('ConceptGraphClient non-OK response: %s %s',
                           resp.status_code, resp.reason)
            raise ConceptGraphUnavailable(f'{resp.status_code} {resp.reason}')

        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning('ConceptGraphClient non-JSON body')
            raise ConceptGraphUnavailable('non-JSON response') from exc

        if not isinstance(data, dict):
            raise ConceptGraphUnavailable(f'expected a JSON object, got {type(data).__name__}')
        return data
