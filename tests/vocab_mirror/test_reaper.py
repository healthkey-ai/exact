"""Vocab-mirror generation reaper tests (#259).

Policy under test: keep the ACTIVE generation, the ``keep`` most-recent non-active
ones, and anything younger than ``min_age``; drop the rest (mirror rows + the
MirrorRelease row). Time is controlled via the ``now`` argument.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.reaper import reap_old_generations

pytestmark = pytest.mark.django_db

_TABLES = (MirrorVocabulary, MirrorConcept, MirrorConceptRelationship, MirrorConceptAncestor)


def _gen(rid, state):
    rel = MirrorRelease.objects.create(release_id=rid, state=state)
    MirrorVocabulary.objects.create(release_id=rid, vocabulary_id='V', vocabulary_name='V',
                                    vocabulary_concept_id=1)
    MirrorConcept.objects.create(release_id=rid, concept_id=rid, concept_name='c',
                                 domain_id='D', vocabulary_id='V', concept_class_id='X',
                                 concept_code='c')
    MirrorConceptRelationship.objects.create(release_id=rid, concept_id_1=1, concept_id_2=2,
                                             relationship_id='r')
    MirrorConceptAncestor.objects.create(release_id=rid, ancestor_concept_id=1,
                                         descendant_concept_id=2,
                                         min_levels_of_separation=1, max_levels_of_separation=1)
    return rel


def _rows(rid):
    return sum(m.objects.filter(release_id=rid).count() for m in _TABLES)


def _past_window():
    # A `now` far enough ahead that any just-created generation is beyond min_age.
    return timezone.now() + timedelta(days=1)


def test_active_is_never_reaped():
    _gen(1, MirrorRelease.ACTIVE)
    assert reap_old_generations(keep=0, min_age=timedelta(0), now=_past_window()) == []
    assert MirrorRelease.objects.filter(release_id=1).exists()
    assert _rows(1) == 4


def test_reaps_old_superseded_beyond_keep():
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.SUPERSEDED)   # older / lower release_id
    _gen(3, MirrorRelease.SUPERSEDED)   # kept as the most-recent non-active

    reaped = reap_old_generations(keep=1, min_age=timedelta(hours=6), now=_past_window())

    assert {r['release_id'] for r in reaped} == {2}
    assert not MirrorRelease.objects.filter(release_id=2).exists()
    assert _rows(2) == 0
    assert MirrorRelease.objects.filter(release_id=3).exists()  # protected by keep=1
    assert MirrorRelease.objects.filter(release_id=1).exists()  # ACTIVE


def test_retention_window_protects_young_generations():
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.SUPERSEDED)
    # now == creation time, so gen 2 is younger than min_age → protected despite keep=0.
    assert reap_old_generations(keep=0, min_age=timedelta(hours=6), now=timezone.now()) == []
    assert MirrorRelease.objects.filter(release_id=2).exists()
    assert _rows(2) == 4


def test_reaps_stranded_failed_ready_staging():
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.FAILED)
    _gen(3, MirrorRelease.READY)
    _gen(4, MirrorRelease.STAGING)

    reaped = reap_old_generations(keep=0, min_age=timedelta(hours=6), now=_past_window())

    assert {r['release_id'] for r in reaped} == {2, 3, 4}
    assert MirrorRelease.objects.filter(state=MirrorRelease.ACTIVE).count() == 1
    assert all(_rows(rid) == 0 for rid in (2, 3, 4))


def test_dry_run_reports_without_deleting():
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.FAILED)

    reaped = reap_old_generations(keep=0, min_age=timedelta(hours=6),
                                  now=_past_window(), dry_run=True)

    assert {r['release_id'] for r in reaped} == {2}
    assert reaped[0]['rows']['MirrorConcept'] == 1  # reports real counts
    assert MirrorRelease.objects.filter(release_id=2).exists()  # nothing deleted
    assert _rows(2) == 4
