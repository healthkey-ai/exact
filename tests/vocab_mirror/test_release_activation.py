"""Release contract + atomic activation tests (#249 / ADR 0002).

Covers the control-plane guarantees: fail-closed reads, an atomic single-ACTIVE
swap that supersedes (but retains) the previous generation, the DB-enforced
"one active" invariant, the release-match gate, and idempotency.
"""
import pytest
from django.db import IntegrityError

from vocab_mirror import activation
from vocab_mirror.activation import (
    ReleaseMatchFailed,
    ReleaseNotReady,
    activate_release,
    active_release_id,
    register_release_match_check,
)
from vocab_mirror.models import MirrorConcept, MirrorRelease

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _restore_release_checks():
    """The release-match registry is module-global; snapshot/restore it so a
    test that registers a check can't leak into others."""
    snapshot = list(activation._release_match_checks)
    yield
    activation._release_match_checks[:] = snapshot


def _ready_release(rid, with_data=True):
    rel = MirrorRelease.objects.create(release_id=rid, state=MirrorRelease.READY)
    if with_data:
        MirrorConcept.objects.create(
            release_id=rid, concept_id=1, concept_name='x', domain_id='Drug',
            vocabulary_id='RxNorm', concept_class_id='Ingredient', concept_code='c')
    return rel


class TestFailClosed:
    def test_active_release_id_is_none_when_nothing_active(self):
        _ready_release(1)  # READY but not activated
        assert active_release_id() is None


class TestActivation:
    def test_activate_ready_release(self):
        _ready_release(1)
        activate_release(1)
        assert active_release_id() == 1
        assert MirrorRelease.objects.get(release_id=1).state == MirrorRelease.ACTIVE

    def test_swap_supersedes_previous_keeps_single_active_and_retains_rows(self):
        _ready_release(1)
        _ready_release(2)
        activate_release(1)
        activate_release(2)
        assert active_release_id() == 2
        assert MirrorRelease.objects.get(release_id=1).state == MirrorRelease.SUPERSEDED
        assert MirrorRelease.objects.filter(state=MirrorRelease.ACTIVE).count() == 1
        # retention: the superseded generation's data rows survive the swap
        assert MirrorConcept.objects.filter(release_id=1).exists()

    def test_activation_is_idempotent(self):
        _ready_release(1)
        activate_release(1)
        activate_release(1)
        assert active_release_id() == 1
        assert MirrorRelease.objects.filter(state=MirrorRelease.ACTIVE).count() == 1

    def test_not_ready_raises_and_leaves_active_unchanged(self):
        _ready_release(1)
        activate_release(1)
        MirrorRelease.objects.create(release_id=2, state=MirrorRelease.STAGING)
        with pytest.raises(ReleaseNotReady):
            activate_release(2)
        assert active_release_id() == 1

    def test_missing_release_raises(self):
        with pytest.raises(ReleaseNotReady):
            activate_release(999)


class TestReleaseMatchGate:
    def test_empty_mirror_generation_is_rejected(self):
        _ready_release(1, with_data=False)  # READY, but zero concept rows
        with pytest.raises(ReleaseMatchFailed):
            activate_release(1)
        assert active_release_id() is None

    def test_registered_check_failure_blocks_and_preserves_active(self):
        _ready_release(1)
        activate_release(1)
        _ready_release(2)

        @register_release_match_check
        def _reject(release_id):
            raise ReleaseMatchFailed('artifact disagrees')

        with pytest.raises(ReleaseMatchFailed):
            activate_release(2)
        assert active_release_id() == 1  # unchanged; previous generation preserved


class TestSingleActiveInvariant:
    def test_db_rejects_two_active_releases(self):
        MirrorRelease.objects.create(release_id=1, state=MirrorRelease.ACTIVE)
        with pytest.raises(IntegrityError):
            MirrorRelease.objects.create(release_id=2, state=MirrorRelease.ACTIVE)

    def test_promote_integrity_error_becomes_typed_concurrent_activation(self, monkeypatch):
        """A concurrent activation that wins the one-active slot makes the
        loser's promote hit the partial-unique. That IntegrityError must surface
        as a typed ConcurrentActivation (inside the MirrorReleaseError contract),
        not a raw DB error, and roll back cleanly."""
        _ready_release(1)

        def _boom(self, *a, **k):
            raise IntegrityError(
                'duplicate key value violates unique constraint '
                '"uq_one_active_mirror_release"')

        monkeypatch.setattr(MirrorRelease, 'save', _boom)
        with pytest.raises(activation.ConcurrentActivation):
            activate_release(1)
        assert active_release_id() is None  # rolled back; nothing activated
