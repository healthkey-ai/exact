"""Local concept-graph traversal tests (#251 / ADR 0002).

Verifies release-pinned traversal over the mirror: fail-closed with no active
release, relationship BFS (levels, filter, cycle guard, direction), the
concept_ancestor closure, and that a traversal never crosses release generations.
"""
import pytest

from vocab_mirror.activation import activate_release
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.traversal import ConceptGraphUnavailable, LocalConceptGraph

pytestmark = pytest.mark.django_db

REL = 'Has component'


def _rel(release_id, c1, c2, relationship_id=REL):
    MirrorConceptRelationship.objects.create(
        release_id=release_id, concept_id_1=c1, concept_id_2=c2, relationship_id=relationship_id)


def _anc(release_id, anc, desc):
    MirrorConceptAncestor.objects.create(
        release_id=release_id, ancestor_concept_id=anc, descendant_concept_id=desc,
        min_levels_of_separation=1, max_levels_of_separation=1)


def _seed_full_and_activate(release_id=1):
    """Populate all four tables for a release and activate it (gate needs all)."""
    MirrorVocabulary.objects.create(release_id=release_id, vocabulary_id='HemOnc',
                                    vocabulary_name='HemOnc', vocabulary_version='2026',
                                    vocabulary_concept_id=1)
    MirrorConcept.objects.create(release_id=release_id, concept_id=10, concept_name='VRd',
                                 domain_id='Drug', vocabulary_id='HemOnc',
                                 concept_class_id='Regimen', concept_code='r')
    _rel(release_id, 10, 100)
    _anc(release_id, 10, 100)
    MirrorRelease.objects.create(release_id=release_id, state=MirrorRelease.READY)
    activate_release(release_id)


class TestFailClosed:
    def test_no_active_release_raises(self):
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().descendants([10], relationship_ids=[REL])

    def test_empty_sources_is_empty_without_a_release(self):
        # No sources -> no traversal -> no release needed.
        result = LocalConceptGraph().descendants([], relationship_ids=[REL])
        assert result.groups == {}


class TestRelationshipBFS:
    def test_descendants_one_level(self):
        _rel(1, 10, 100)
        _rel(1, 10, 101)
        result = LocalConceptGraph().descendants([10], relationship_ids=[REL], release_id=1)
        assert result.groups == {10: [100, 101]}
        assert result.truncated == []

    def test_descendants_multi_level_bfs(self):
        _rel(1, 10, 100)
        _rel(1, 100, 1000)
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL],
                                               release_id=1).groups == {10: [100, 1000]}

    def test_max_levels_bounds_the_walk(self):
        _rel(1, 10, 100)
        _rel(1, 100, 1000)
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL], max_levels=1,
                                               release_id=1).groups == {10: [100]}

    def test_ancestors_walk_the_reverse_edge(self):
        _rel(1, 10, 100)
        _rel(1, 20, 100)
        assert LocalConceptGraph().ancestors([100], relationship_ids=[REL],
                                             release_id=1).groups == {100: [10, 20]}

    def test_relationship_filter(self):
        _rel(1, 10, 100, REL)
        _rel(1, 10, 200, 'Other rel')
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL],
                                               release_id=1).groups == {10: [100]}
        # no filter -> all relationships
        assert LocalConceptGraph().descendants([10], release_id=1).groups == {10: [100, 200]}

    def test_cycle_guard_terminates_and_excludes_source(self):
        _rel(1, 10, 20)
        _rel(1, 20, 10)  # cycle back to the source
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL],
                                               release_id=1).groups == {10: [20]}

    def test_diamond_is_deduplicated(self):
        _rel(1, 10, 100)
        _rel(1, 10, 200)
        _rel(1, 100, 300)
        _rel(1, 200, 300)  # diamond: both paths reach 300
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL],
                                               release_id=1).groups == {10: [100, 200, 300]}

    def test_multi_source_gives_per_source_groups(self):
        _rel(1, 10, 100)
        _rel(1, 20, 200)
        assert LocalConceptGraph().descendants([10, 20], relationship_ids=[REL],
                                               release_id=1).groups == {10: [100], 20: [200]}


class TestNodeFilter:
    def test_vocabulary_and_class_filter(self):
        _rel(1, 10, 100)
        _rel(1, 10, 200)
        MirrorConcept.objects.create(release_id=1, concept_id=100, concept_name='a',
                                     domain_id='Drug', vocabulary_id='RxNorm',
                                     concept_class_id='Ingredient', concept_code='c')
        MirrorConcept.objects.create(release_id=1, concept_id=200, concept_name='b',
                                     domain_id='Drug', vocabulary_id='HemOnc',
                                     concept_class_id='Regimen', concept_code='d')
        g = LocalConceptGraph()
        assert g.descendants([10], vocabulary_ids=['RxNorm'], release_id=1).groups == {10: [100]}
        assert g.descendants([10], concept_class_ids=['Regimen'], release_id=1).groups == {10: [200]}
        # a filter that matches nothing -> empty group (not unfiltered)
        assert g.descendants([10], vocabulary_ids=['SNOMED'], release_id=1).groups == {10: []}


class TestPinPrecedence:
    def test_explicit_release_overrides_instance_release(self):
        _rel(1, 10, 100)
        _rel(2, 10, 999)
        g = LocalConceptGraph(release_id=1)
        assert g.descendants([10], release_id=1).groups == {10: [100]}
        assert g.descendants([10], release_id=2).groups == {10: [999]}  # explicit wins

    def test_closure_fails_closed_with_no_active_release(self):
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().closure_descendants(10)


class TestReleasePinning:
    def test_traversal_does_not_cross_releases(self):
        _rel(1, 10, 100)
        _rel(2, 10, 999)
        assert LocalConceptGraph().descendants([10], release_id=1).groups == {10: [100]}
        assert LocalConceptGraph().descendants([10], release_id=2).groups == {10: [999]}

    def test_resolves_active_release_when_unpinned(self):
        _seed_full_and_activate(1)
        _rel(1, 10, 101)  # extra edge in the active release
        # no release_id -> uses active release 1
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL]).groups == {10: [100, 101]}


class TestClosure:
    def test_closure_descendants_and_ancestors(self):
        _anc(1, 10, 100)
        _anc(1, 10, 1000)
        g = LocalConceptGraph()
        assert g.closure_descendants(10, release_id=1) == {100, 1000}
        assert g.closure_ancestors(100, release_id=1) == {10}
