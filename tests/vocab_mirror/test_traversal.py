"""Local concept-graph traversal tests (#251 / ADR 0002).

Traversal reads only the ACTIVE generation, pinned once per instance. Covers:
fail-closed (no active release; a non-active release id refused), relationship
BFS (levels, filter, cycle guard, direction, diamond dedup), node filtering, the
concept_ancestor closure, and single-active-generation isolation.
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


def _activate(release_id=1):
    """Seed the gate minimum in all four tables (isolated concept_ids that don't
    collide with test graph data) + activate the release, so ACTIVE-only
    traversal can run against it. Tests then add their own edges to it."""
    MirrorVocabulary.objects.create(release_id=release_id, vocabulary_id='V',
                                    vocabulary_name='V', vocabulary_concept_id=1)
    MirrorConcept.objects.create(release_id=release_id, concept_id=900001, concept_name='g',
                                 domain_id='D', vocabulary_id='V', concept_class_id='C',
                                 concept_code='g')
    _rel(release_id, 900001, 900002, relationship_id='seed')
    _anc(release_id, 900001, 900002)
    MirrorRelease.objects.create(release_id=release_id, state=MirrorRelease.READY)
    activate_release(release_id)


class TestFailClosed:
    def test_no_active_release_raises(self):
        _rel(1, 10, 100)  # data exists, but no release is ACTIVE
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().descendants([10], relationship_ids=[REL])

    def test_pinning_a_non_active_release_is_refused(self):
        _rel(1, 10, 100)  # release 1 has rows but was never activated
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().descendants([10], relationship_ids=[REL], release_id=1)

    def test_empty_sources_is_empty_without_a_release(self):
        assert LocalConceptGraph().descendants([], relationship_ids=[REL]).groups == {}

    def test_closure_fails_closed_with_no_active_release(self):
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().closure_descendants(10)


class TestRelationshipBFS:
    def test_descendants_one_level(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 10, 101)
        result = LocalConceptGraph().descendants([10], relationship_ids=[REL])
        assert result.groups == {10: [100, 101]}
        assert result.truncated == []

    def test_descendants_multi_level_bfs(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 100, 1000)
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL]).groups == {10: [100, 1000]}

    def test_max_levels_bounds_the_walk(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 100, 1000)
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL],
                                               max_levels=1).groups == {10: [100]}

    def test_ancestors_walk_the_reverse_edge(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 20, 100)
        assert LocalConceptGraph().ancestors([100], relationship_ids=[REL]).groups == {100: [10, 20]}

    def test_relationship_filter(self):
        _activate(1)
        _rel(1, 10, 100, REL)
        _rel(1, 10, 200, 'Other rel')
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL]).groups == {10: [100]}
        assert LocalConceptGraph().descendants([10]).groups == {10: [100, 200]}  # no filter

    def test_cycle_guard_terminates_and_excludes_source(self):
        _activate(1)
        _rel(1, 10, 20)
        _rel(1, 20, 10)  # cycle back to the source
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL]).groups == {10: [20]}

    def test_diamond_is_deduplicated(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 10, 200)
        _rel(1, 100, 300)
        _rel(1, 200, 300)  # both paths reach 300
        assert LocalConceptGraph().descendants([10], relationship_ids=[REL]).groups == {10: [100, 200, 300]}

    def test_multi_source_gives_per_source_groups(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 20, 200)
        assert LocalConceptGraph().descendants([10, 20], relationship_ids=[REL]).groups == {
            10: [100], 20: [200]}


class TestNodeFilter:
    def test_vocabulary_and_class_filter(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(1, 10, 200)
        MirrorConcept.objects.create(release_id=1, concept_id=100, concept_name='a',
                                     domain_id='Drug', vocabulary_id='RxNorm',
                                     concept_class_id='Ingredient', concept_code='c')
        MirrorConcept.objects.create(release_id=1, concept_id=200, concept_name='b',
                                     domain_id='Drug', vocabulary_id='HemOnc',
                                     concept_class_id='Regimen', concept_code='d')
        assert LocalConceptGraph().descendants([10], vocabulary_ids=['RxNorm']).groups == {10: [100]}
        assert LocalConceptGraph().descendants([10], concept_class_ids=['Regimen']).groups == {10: [200]}
        # a filter that matches nothing -> empty group (not unfiltered)
        assert LocalConceptGraph().descendants([10], vocabulary_ids=['SNOMED']).groups == {10: []}


class TestActiveGenerationIsolation:
    def test_traversal_only_sees_the_active_generation(self):
        _activate(1)
        _rel(1, 10, 100)
        _rel(2, 10, 999)  # a different, non-active release
        assert LocalConceptGraph().descendants([10]).groups == {10: [100]}
        with pytest.raises(ConceptGraphUnavailable):
            LocalConceptGraph().descendants([10], release_id=2)  # non-active refused

    def test_pinned_instance_rejects_a_conflicting_per_call_release(self):
        _activate(1)
        _rel(1, 10, 100)
        g = LocalConceptGraph()  # pins the active release (1) on first use
        assert g.descendants([10]).groups == {10: [100]}
        with pytest.raises(ValueError):
            g.descendants([10], release_id=2)  # conflicts with the pinned release


class TestClosure:
    def test_closure_descendants_and_ancestors(self):
        _activate(1)
        _anc(1, 10, 100)
        _anc(1, 10, 1000)
        g = LocalConceptGraph()
        assert g.closure_descendants(10) == {100, 1000}
        assert LocalConceptGraph().closure_ancestors(100) == {10}
