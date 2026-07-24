"""Release-pinned cache over ConceptGraphClient (#234 slice 2; promop ADR 0001 / promop#279).

Access is API + cache: promop is the source of truth; this is the consumer-side cache.
Per the ADR's consumer-conformance rules:

- **Per-source caching.** Each source concept_id's expansion is cached independently, so a
  regimen that recurs across many trials is fetched once and reused. A request expands the
  cache-miss ids in a single batched client call, then assembles the full result.
- **Release-pinned key.** The cache key includes an explicit ``release`` pin (the promop
  release id, once promop emits one — until then a caller-supplied stand-in such as a pinned
  vocabulary version). ``release`` is REQUIRED: caching vocabulary data without a version is
  a correctness bug (a release bump silently serves stale expansions), so an empty ``release``
  raises. Two sides matched against each other (patient vs trial) must pin the SAME release.
  LIMITATION: the pin is a cache NAMESPACE + the TTL bounds intra-release staleness; it is
  not sent to promop or verified against the response, because promop does not yet accept a
  release param nor stamp a release id (promop#279). Until it does, a release bump is
  invalidated by the new key + old entries expiring via TTL — not by end-to-end enforcement.

KNOWN LIMITATION (fix before matcher wiring): ``versions`` is cached per source but is the
batch-level union the client returns, not the source's own versions — a single-source hit
can report versions from other concepts fetched in the same batch. Harmless while
``versions`` is observability-only; authoritative provenance moves to the promop release id
(promop#279), which supersedes this field.
- **Fail-closed.** Client failures (``ConceptGraphUnavailable``) are never cached and always
  propagate — the caller applies its fail-closed policy. A **truncated** source (its result
  hit promop's node cap → incomplete) is never cached, so a partial projection is not
  persisted; it stays in the result's ``truncated`` list for the caller to fail-closed on.

Backend: Django's cache framework (``caches[alias]``). Production points the alias at a
**DB-backed cache** (shared across Redis-less Cloud Run instances); tests use LocMemCache.
"""
import hashlib
import json

from django.conf import settings
from django.core.cache import InvalidCacheBackendError, caches

from trials.services.omop.concept_graph_client import (
    ConceptGraphClient,
    ConceptGraphResult,
    _positive_ints,
)

DEFAULT_TTL_SECONDS = 3600  # 1h; a release bump invalidates via the release-pinned key
_KEY_PREFIX = 'cgc'
# Bump when the cache key or stored-value shape changes, to invalidate old entries.
_CACHE_SCHEMA = 'v1'


def _key_prefix(release, direction, relationship_ids, vocabulary_ids,
                concept_class_ids, max_levels) -> str:
    """Stable hash of everything that scopes an expansion except the source id.

    Full SHA-256 (no truncation) so distinct scopes cannot collide; the schema tag
    lets a shape change invalidate every entry.
    """
    payload = json.dumps([
        _CACHE_SCHEMA, str(release), direction,
        sorted(relationship_ids or []),
        sorted(vocabulary_ids or []),
        sorted(concept_class_ids or []),
        max_levels,
    ], sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class CachedConceptGraphClient:
    def __init__(self, client: ConceptGraphClient | None = None,
                 cache_alias: str | None = None, ttl: int = DEFAULT_TTL_SECONDS):
        self.client = client or ConceptGraphClient()
        # Prod must point CONCEPT_GRAPH_CACHE_ALIAS at a SHARED (DB-backed) cache:
        # the 'default' alias is Redis-when-configured else per-process LocMemCache
        # (see exact/cache_config.py), so on Redis-less Cloud Run the default is
        # per-worker and defeats cross-instance reuse. Configuring the DB-backed
        # alias + its cache table is a follow-up (slice 2b). Falls back to 'default'
        # so an unconfigured alias never raises.
        alias = cache_alias or getattr(settings, 'CONCEPT_GRAPH_CACHE_ALIAS', 'default')
        try:
            self.cache = caches[alias]
        except InvalidCacheBackendError:
            self.cache = caches['default']
        self.ttl = ttl

    def descendants(self, concept_ids, *, release, relationship_ids=None, **kwargs):
        return self.expand(concept_ids, 'descendants', release=release,
                           relationship_ids=relationship_ids, **kwargs)

    def ancestors(self, concept_ids, *, release, relationship_ids=None, **kwargs):
        return self.expand(concept_ids, 'ancestors', release=release,
                           relationship_ids=relationship_ids, **kwargs)

    def expand(self, concept_ids, direction, *, release,
               relationship_ids=None, vocabulary_ids=None, concept_class_ids=None,
               max_levels=None) -> ConceptGraphResult:
        """Cached counterpart to ``ConceptGraphClient.expand``.

        ``release`` (required, non-empty) pins the promop release the cache is keyed on.
        Raises ``ValueError`` for an empty ``release`` or a bad ``direction``, and lets
        ``ConceptGraphUnavailable`` from the underlying client propagate (never cached).
        """
        if not release:
            raise ValueError('release pin is required (cache must be release-keyed)')
        if direction not in ('ancestors', 'descendants'):
            raise ValueError(f'bad direction: {direction!r}')

        source_ids = _positive_ints(concept_ids)
        if not source_ids:
            return ConceptGraphResult(groups={}, truncated=[], versions=frozenset())

        prefix = _key_prefix(release, direction, relationship_ids,
                             vocabulary_ids, concept_class_ids, max_levels)

        groups: dict[int, list[int]] = {}
        versions: set[str] = set()
        truncated: set[int] = set()
        missing: list[int] = []

        for cid in source_ids:
            entry = self.cache.get(f'{_KEY_PREFIX}:{prefix}:{cid}')
            if entry is None:
                missing.append(cid)
            else:
                groups[cid] = entry['groups']
                versions.update(entry['versions'])

        if missing:
            # One batched fetch for the misses; raises → nothing cached, propagates.
            res = self.client.expand(
                missing, direction,
                relationship_ids=relationship_ids,
                vocabulary_ids=vocabulary_ids,
                concept_class_ids=concept_class_ids,
                max_levels=max_levels,
            )
            versions.update(res.versions)
            res_versions = sorted(res.versions)
            for cid in missing:
                g = res.groups.get(cid, [])
                groups[cid] = g
                if cid in res.truncated:
                    # Incomplete — surface but do NOT cache a partial projection.
                    truncated.add(cid)
                else:
                    self.cache.set(
                        f'{_KEY_PREFIX}:{prefix}:{cid}',
                        {'groups': g, 'versions': res_versions},
                        self.ttl,
                    )

        return ConceptGraphResult(
            groups={cid: groups[cid] for cid in source_ids},
            truncated=sorted(truncated),
            versions=frozenset(versions),
        )
