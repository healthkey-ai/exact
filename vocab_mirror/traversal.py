"""Local OMOP concept-graph traversal over the release-pinned mirror (#251).

Replaces the retired per-request promop concept-graph API + result cache (#234):
EXACT now traverses its own local mirror (#248-#250), pinned to one release
generation, instead of calling promop per match. The public shape mirrors the
old ``ConceptGraphClient`` (``descendants`` / ``ancestors`` / ``expand`` →
``ConceptGraphResult``) so the Phase-T wiring (#252) is a drop-in swap.

Consistency (ADR 0002): every traversal is pinned to a single ``release_id`` —
either passed explicitly (a request binds ``active_release_id()`` once and
threads it through) or resolved to the active release per call. If no release is
active the traversal **fails closed** (``ConceptGraphUnavailable``), never an
empty/fail-open result and never a live promop call.

Two traversal modes:
- ``expand`` / ``descendants`` / ``ancestors`` walk ``concept_relationship`` by
  ``relationship_id`` (BFS, bounded by ``max_levels``, cycle-guarded) — the
  regimen→component style expansion.
- ``closure_descendants`` / ``closure_ancestors`` read the precomputed transitive
  closure in ``concept_ancestor`` in one query.
"""
import logging
from dataclasses import dataclass

from vocab_mirror.activation import active_release_id
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)

logger = logging.getLogger(__name__)

_DB = 'default'
_DIRECTIONS = ('descendants', 'ancestors')


class ConceptGraphUnavailable(Exception):
    """No active mirror release to traverse — callers must fail closed."""


@dataclass(frozen=True)
class ConceptGraphResult:
    """Outcome of a traversal.

    ``groups`` maps each source concept_id to its de-duplicated, sorted list of
    related concept_ids. ``truncated`` is always empty for the local mirror (no
    per-source node cap, unlike the old API); it is kept for shape-compatibility
    with the retired client. ``versions`` is the set of vocabulary versions in
    the pinned release (observability).
    """
    groups: dict[int, list[int]]
    truncated: list[int]
    versions: frozenset[str]


def _positive_ints(values):
    seen, out = set(), []
    for v in values or []:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv > 0 and iv not in seen:
            seen.add(iv)
            out.append(iv)
    return out


class LocalConceptGraph:
    """Traverse the mirror's concept graph, pinned to one release."""

    def __init__(self, release_id=None):
        self._release_id = release_id
        # Pin resolved lazily on first use and cached for the instance's lifetime,
        # so a multi-call request can never straddle an activation (all traversals
        # on one instance see one generation). #252 constructs one per request.
        self._pinned = None

    def _pin(self, release_id=None):
        if self._pinned is None:
            rid = (release_id if release_id is not None
                   else self._release_id if self._release_id is not None
                   else active_release_id())
            if rid is None:
                raise ConceptGraphUnavailable(
                    'no active vocab mirror release; refusing to traverse')
            # Uphold the reader invariant: only ever traverse the ACTIVE
            # generation — never a STAGING/partial (fail-open) or SUPERSEDED one.
            if not MirrorRelease.objects.using(_DB).filter(
                    release_id=rid, state=MirrorRelease.ACTIVE).exists():
                raise ConceptGraphUnavailable(
                    f'release {rid} is not the ACTIVE generation; refusing to traverse')
            self._pinned = rid
        elif release_id is not None and release_id != self._pinned:
            raise ValueError(
                f'release_id {release_id} conflicts with the pinned release {self._pinned}')
        return self._pinned

    def _versions(self, release_id):
        return frozenset(
            MirrorVocabulary.objects.using(_DB)
            .filter(release_id=release_id).exclude(vocabulary_version__isnull=True)
            .values_list('vocabulary_version', flat=True)
        )

    # ── relationship BFS (concept_relationship) ──────────────────────────────

    def descendants(self, concept_ids, relationship_ids=None, max_levels=None,
                    vocabulary_ids=None, concept_class_ids=None, release_id=None):
        return self.expand(concept_ids, 'descendants', relationship_ids, max_levels,
                           vocabulary_ids, concept_class_ids, release_id)

    def ancestors(self, concept_ids, relationship_ids=None, max_levels=None,
                  vocabulary_ids=None, concept_class_ids=None, release_id=None):
        return self.expand(concept_ids, 'ancestors', relationship_ids, max_levels,
                           vocabulary_ids, concept_class_ids, release_id)

    def expand(self, concept_ids, direction, relationship_ids=None, max_levels=None,
               vocabulary_ids=None, concept_class_ids=None, release_id=None):
        if direction not in _DIRECTIONS:
            raise ValueError(f'direction must be one of {_DIRECTIONS}, got {direction!r}')
        sources = _positive_ints(concept_ids)
        if not sources:
            return ConceptGraphResult(groups={}, truncated=[], versions=frozenset())
        rid = self._pin(release_id)
        # One BFS per source (per-level query, index-backed). Source counts for
        # regimen→component expansion are small; batch cardinality is worth a look
        # if this is ever driven by a large source set.
        raw = {s: self._bfs(s, direction, relationship_ids, max_levels, rid) for s in sources}
        # Optional vocabulary_id / concept_class_id post-filter on the reached
        # nodes — resolved in ONE query over the union, then intersected per source.
        if vocabulary_ids or concept_class_ids:
            allowed = self._allowed_nodes(raw, vocabulary_ids, concept_class_ids, rid)
            groups = {s: sorted(nodes & allowed) for s, nodes in raw.items()}
        else:
            groups = {s: sorted(nodes) for s, nodes in raw.items()}
        return ConceptGraphResult(groups=groups, truncated=[], versions=self._versions(rid))

    def _allowed_nodes(self, raw, vocabulary_ids, concept_class_ids, release_id):
        all_nodes = set().union(*raw.values()) if raw else set()
        if not all_nodes:
            return set()
        qs = MirrorConcept.objects.using(_DB).filter(
            release_id=release_id, concept_id__in=list(all_nodes))
        if vocabulary_ids:
            qs = qs.filter(vocabulary_id__in=list(vocabulary_ids))
        if concept_class_ids:
            qs = qs.filter(concept_class_id__in=list(concept_class_ids))
        return set(qs.values_list('concept_id', flat=True))

    def _bfs(self, source, direction, relationship_ids, max_levels, release_id):
        # descendants follow concept_id_1 -> concept_id_2; ancestors the reverse.
        from_field, to_field = (
            ('concept_id_1', 'concept_id_2') if direction == 'descendants'
            else ('concept_id_2', 'concept_id_1'))
        reached, visited, frontier, level = set(), {source}, {source}, 0
        while frontier and (max_levels is None or level < max_levels):
            qs = MirrorConceptRelationship.objects.using(_DB).filter(
                release_id=release_id, **{f'{from_field}__in': list(frontier)})
            if relationship_ids:
                qs = qs.filter(relationship_id__in=list(relationship_ids))
            nxt = set(qs.values_list(to_field, flat=True))
            reached |= nxt
            frontier = nxt - visited  # cycle guard: only walk newly-seen nodes
            visited |= frontier
            level += 1
        reached.discard(source)  # a cycle can re-reach the source; never return it
        return reached

    # ── transitive closure (concept_ancestor) ────────────────────────────────

    def closure_descendants(self, concept_id, release_id=None):
        rid = self._pin(release_id)
        return set(
            MirrorConceptAncestor.objects.using(_DB)
            .filter(release_id=rid, ancestor_concept_id=int(concept_id))
            .exclude(descendant_concept_id=int(concept_id))
            .values_list('descendant_concept_id', flat=True))

    def closure_ancestors(self, concept_id, release_id=None):
        rid = self._pin(release_id)
        return set(
            MirrorConceptAncestor.objects.using(_DB)
            .filter(release_id=rid, descendant_concept_id=int(concept_id))
            .exclude(ancestor_concept_id=int(concept_id))
            .values_list('ancestor_concept_id', flat=True))
