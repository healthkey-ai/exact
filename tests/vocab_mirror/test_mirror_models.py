"""Schema tests for the OMOP vocabulary mirror (#248 / ADR 0002).

Covers the two properties the schema exists to provide:
- **generation coexistence** — the same concept_id lives in multiple releases,
  uniquely keyed by (release_id, concept_id), so a swap + retention window never
  collides;
- **release-scoped traversal** — descendants/ancestors/relationships resolve
  within one release, never across generations.
Plus the placement invariant: the mirror routes to ``default`` (a writable DB we
own), never to the read-only ``trials`` alias.
"""
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection

from exact.db_router import TrialsDatabaseRouter
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorVocabulary,
)

pytestmark = pytest.mark.django_db


def _concept(**kw):
    base = dict(
        release_id=1, concept_id=100, concept_name='Bortezomib', domain_id='Drug',
        vocabulary_id='RxNorm', concept_class_id='Ingredient', concept_code='c1',
    )
    base.update(kw)
    return MirrorConcept.objects.create(**base)


class TestGenerationCoexistence:
    def test_same_concept_id_coexists_across_releases(self):
        _concept(release_id=1, concept_id=100, concept_name='old')
        _concept(release_id=2, concept_id=100, concept_name='new')
        assert MirrorConcept.objects.get(release_id=1, concept_id=100).concept_name == 'old'
        assert MirrorConcept.objects.get(release_id=2, concept_id=100).concept_name == 'new'

    def test_concept_unique_within_a_release(self):
        _concept(release_id=1, concept_id=100)
        with pytest.raises(IntegrityError):
            _concept(release_id=1, concept_id=100, concept_name='dup')

    def test_vocabulary_unique_within_a_release(self):
        MirrorVocabulary.objects.create(release_id=1, vocabulary_id='RxNorm',
                                        vocabulary_name='RxNorm', vocabulary_concept_id=1)
        # same vocab, next release — allowed
        MirrorVocabulary.objects.create(release_id=2, vocabulary_id='RxNorm',
                                        vocabulary_name='RxNorm', vocabulary_concept_id=1)
        with pytest.raises(IntegrityError):
            MirrorVocabulary.objects.create(release_id=1, vocabulary_id='RxNorm',
                                            vocabulary_name='dup', vocabulary_concept_id=1)


class TestReleaseScopedTraversal:
    def test_descendants_are_release_scoped(self):
        # ancestor 10 → {11,12} in R1, {99} in R2
        for rel, desc in [(1, 11), (1, 12), (2, 99)]:
            MirrorConceptAncestor.objects.create(
                release_id=rel, ancestor_concept_id=10, descendant_concept_id=desc,
                min_levels_of_separation=1, max_levels_of_separation=1)
        got_r1 = set(MirrorConceptAncestor.objects
                     .filter(release_id=1, ancestor_concept_id=10)
                     .values_list('descendant_concept_id', flat=True))
        got_r2 = set(MirrorConceptAncestor.objects
                     .filter(release_id=2, ancestor_concept_id=10)
                     .values_list('descendant_concept_id', flat=True))
        assert got_r1 == {11, 12}
        assert got_r2 == {99}

    def test_relationship_forward_and_reverse_release_scoped(self):
        MirrorConceptRelationship.objects.create(
            release_id=1, concept_id_1=10, concept_id_2=20, relationship_id='Subsumes')
        MirrorConceptRelationship.objects.create(
            release_id=1, concept_id_1=10, concept_id_2=21, relationship_id='Subsumes')
        MirrorConceptRelationship.objects.create(
            release_id=2, concept_id_1=10, concept_id_2=99, relationship_id='Subsumes')
        fwd_r1 = set(MirrorConceptRelationship.objects
                     .filter(release_id=1, concept_id_1=10, relationship_id='Subsumes')
                     .values_list('concept_id_2', flat=True))
        assert fwd_r1 == {20, 21}
        rev = set(MirrorConceptRelationship.objects
                  .filter(release_id=1, concept_id_2=20, relationship_id='Subsumes')
                  .values_list('concept_id_1', flat=True))
        assert rev == {10}


class TestTraversalIndexesExist:
    """A release-scoped query returns correct results even with no index, so
    assert the traversal indexes exist at the DB level (with the expected
    leading ``release_id`` column order) — otherwise a graph traversal over
    ~18M rows silently degrades to a full scan (#248 review)."""

    EXPECTED = {
        MirrorConcept: {
            'uq_mconcept_rel_cid': ['release_id', 'concept_id'],
            'ix_mconcept_rel_vocab': ['release_id', 'vocabulary_id'],
        },
        MirrorConceptRelationship: {
            'ix_mcr_rel_c1_rid': ['release_id', 'concept_id_1', 'relationship_id'],
            'ix_mcr_rel_c2_rid': ['release_id', 'concept_id_2', 'relationship_id'],
        },
        MirrorConceptAncestor: {
            'ix_mca_rel_anc': ['release_id', 'ancestor_concept_id'],
            'ix_mca_rel_desc': ['release_id', 'descendant_concept_id'],
        },
        MirrorVocabulary: {
            'uq_mvocab_rel_vocab': ['release_id', 'vocabulary_id'],
        },
    }

    def test_named_indexes_present_with_expected_columns(self):
        with connection.cursor() as cur:
            for model, expected in self.EXPECTED.items():
                table = model._meta.db_table
                constraints = connection.introspection.get_constraints(cur, table)
                for name, cols in expected.items():
                    assert name in constraints, f'{table}: missing index {name}'
                    assert constraints[name]['columns'] == cols, (
                        f'{table}.{name}: {constraints[name]["columns"]} != {cols}')


class TestPlacement:
    """The mirror must live on a writable DB we own, not the read-only trials alias."""

    def test_mirror_models_do_not_route_to_trials_alias(self):
        router = TrialsDatabaseRouter()
        # Even when a separate trials DB IS configured, mirror models must not be
        # routed to it (None → Django falls back to 'default').
        with patch('exact.db_router._trials_db_configured', return_value=True):
            for model in (MirrorConcept, MirrorConceptRelationship,
                          MirrorConceptAncestor, MirrorVocabulary):
                assert router.db_for_read(model) is None
                assert router.db_for_write(model) is None

    def test_mirror_migrates_on_default_only(self):
        router = TrialsDatabaseRouter()
        with patch('exact.db_router._trials_db_configured', return_value=True):
            assert router.allow_migrate('default', 'vocab_mirror') is True
            assert router.allow_migrate('trials', 'vocab_mirror') is False
