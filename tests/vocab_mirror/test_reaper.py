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


def test_keep_two_protects_two_most_recent():
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.SUPERSEDED)   # oldest non-active
    _gen(3, MirrorRelease.SUPERSEDED)
    _gen(4, MirrorRelease.SUPERSEDED)   # newest (release_id tiebreak under updated_at ties)

    reaped = reap_old_generations(keep=2, min_age=timedelta(hours=6), now=_past_window())

    # ACTIVE(1) + the 2 most-recent non-active (4, 3) protected → only 2 reaped.
    assert {r['release_id'] for r in reaped} == {2}


def test_end_to_end_activate_supersede_reap():
    # Exercise the REAL activation → SUPERSEDED path (updated_at bumped on supersede)
    # that the keep/recency ordering actually protects.
    from vocab_mirror.activation import activate_release
    _gen(1, MirrorRelease.READY)
    _gen(2, MirrorRelease.READY)
    activate_release(1)
    activate_release(2)  # supersedes release 1
    assert MirrorRelease.objects.get(release_id=1).state == MirrorRelease.SUPERSEDED

    reaped = reap_old_generations(keep=0, min_age=timedelta(0), now=_past_window())

    assert {r['release_id'] for r in reaped} == {1}
    assert MirrorRelease.objects.get(release_id=2).state == MirrorRelease.ACTIVE
    assert _rows(1) == 0 and _rows(2) == 4


def test_reap_under_lock_runs_when_uncontended():
    from vocab_mirror.reaper import reap_under_lock
    _gen(1, MirrorRelease.ACTIVE)
    _gen(2, MirrorRelease.FAILED)

    result = reap_under_lock(keep=0, min_age=timedelta(0), now=_past_window())

    assert result is not None  # acquired the lock and ran
    assert {r['release_id'] for r in result} == {2}
    assert not MirrorRelease.objects.filter(release_id=2).exists()


def test_reap_best_effort_swallows_reaper_errors(monkeypatch):
    # A reaper failure must never fail the sync.
    import vocab_mirror.reaper as reaper_mod
    from vocab_mirror.sync import _reap_best_effort

    def boom(*a, **k):
        raise RuntimeError('reaper blew up')

    monkeypatch.setattr(reaper_mod, 'reap_old_generations', boom)
    _reap_best_effort()  # must not raise


def test_command_rejects_negative_options():
    from django.core.management import CommandError, call_command
    with pytest.raises(CommandError):
        call_command('reap_vocab_mirror', min_age_hours=-6)  # cutoff in the future
    with pytest.raises(CommandError):
        call_command('reap_vocab_mirror', keep=-1)  # slice semantics, not a count
