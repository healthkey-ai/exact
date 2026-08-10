"""S4a (#350) — EXACT read-models for CB's projection registry/snapshot (Design S, gap #2).

The read side EXACT will use instead of live Trial columns. CB owns/writes the tables
(cancerbot #4695/#4696); EXACT reads via the trials alias (here, the local default DB in
tests). Covers the models + the ``trusted_release_for`` registry-state accessor.
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from trials.models import ProjectionRelease, ProjectionSnapshot
from trials.services.omop.projection_read import trusted_release_for

pytestmark = pytest.mark.django_db


def _release(pid, vocab='7', published=True, disabled=False, checksum='abc', at=None):
    return ProjectionRelease.objects.create(
        projection_id=pid, vocab_release_id=vocab, published=published,
        disabled=disabled, manifest_checksum=checksum,
        published_at=at or (timezone.now() if published else None))


class TestReadModels:
    def test_release_and_snapshot_roundtrip(self):
        rel = _release('7:abc', vocab='7', checksum='abc')
        ProjectionSnapshot.objects.create(
            projection=rel, trial_code='NCT-A',
            omop_therapy_types_required=['35803229', '35803227'],
            omop_therapy_types_excluded=['35803228'])
        got = ProjectionRelease.objects.get(pk='7:abc')
        assert got.vocab_release_id == '7' and got.manifest_checksum == 'abc'
        snap = got.snapshots.get()
        assert snap.trial_code == 'NCT-A'
        assert snap.omop_therapy_types_required == ['35803229', '35803227']

    def test_snapshot_unique_projection_trial(self):
        from django.db import IntegrityError, transaction
        rel = _release('P1')
        ProjectionSnapshot.objects.create(projection=rel, trial_code='NCT-A')
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ProjectionSnapshot.objects.create(projection=rel, trial_code='NCT-A')


class TestTrustedReleaseFor:
    def test_returns_published_non_disabled(self):
        _release('7:abc', vocab='7')
        got = trusted_release_for('7')
        assert got is not None and got.projection_id == '7:abc'

    def test_excludes_disabled(self):
        _release('7:x', vocab='7', disabled=True)
        assert trusted_release_for('7') is None

    def test_excludes_unpublished(self):
        _release('7:wip', vocab='7', published=False)
        assert trusted_release_for('7') is None

    def test_excludes_wrong_release(self):
        _release('9:abc', vocab='9')
        assert trusted_release_for('7') is None

    def test_none_for_empty_release(self):
        _release('7:abc', vocab='7')
        assert trusted_release_for('') is None
        assert trusted_release_for(None) is None

    def test_accepts_non_str_release_id(self):
        _release('7:abc', vocab='7')
        assert trusted_release_for(7) is not None  # coerced to '7'

    def test_newest_when_multiple_published(self):
        old = timezone.now()
        _release('7:old', vocab='7', at=old)
        _release('7:new', vocab='7', at=old + timedelta(days=1))
        assert trusted_release_for('7').projection_id == '7:new'

    def test_null_published_at_sorts_last(self):
        """A malformed published-but-null-published_at row must not out-rank a real one."""
        _release('7:good', vocab='7', at=timezone.now())
        # force published_at NULL on a second published row
        ProjectionRelease.objects.create(
            projection_id='7:nullpub', vocab_release_id='7', published=True,
            published_at=None)
        assert trusted_release_for('7').projection_id == '7:good'
