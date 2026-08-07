"""Canonical trial-projection checksum (ADR 0002 §Gate 1 gap #1 / ADR 0003 Option D).

The frozen CB↔EXACT contract EXACT recomputes at activation to verify CB's published
attestation checksum. Must be deterministic and order/duplicate-independent (the matcher
treats the type lists as sets), and change when a projected value changes.
"""
import pytest

from trials.models import Trial
from trials.services.omop.projection_checksum import (
    _normalize,
    compute_trial_projection_checksum,
)
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def _trial(code, required=None, excluded=None):
    return TrialFactory(code=code, omop_therapy_types_required=required or [],
                        omop_therapy_types_excluded=excluded or [])


def test_normalize_sorts_dedups_stringifies():
    assert _normalize([3, 1, 2, 2]) == ['1', '2', '3']
    assert _normalize(None) == []
    assert _normalize([]) == []
    assert _normalize(['35807403', 35807295]) == ['35807295', '35807403']


def test_empty_universe_is_stable_and_zero_count():
    d1, n1 = compute_trial_projection_checksum()
    d2, n2 = compute_trial_projection_checksum()
    assert d1 == d2 and n1 == n2 == 0


def test_count_and_determinism():
    _trial('T1', [1])
    _trial('T2', [2, 3], [9])
    d1, n = compute_trial_projection_checksum()
    d2, _ = compute_trial_projection_checksum()
    assert n == 2 and d1 == d2 and len(d1) == 64  # sha256 hex


def test_order_and_duplicate_independent():
    t = _trial('T1', [3, 1, 2])
    d_a, _ = compute_trial_projection_checksum()
    Trial.objects.filter(pk=t.pk).update(omop_therapy_types_required=[2, 2, 1, 3])  # reorder + dup
    d_b, _ = compute_trial_projection_checksum()
    assert d_a == d_b  # set semantics → same checksum


def test_value_change_changes_checksum():
    t = _trial('T1', [1])
    d_a, _ = compute_trial_projection_checksum()
    Trial.objects.filter(pk=t.pk).update(omop_therapy_types_required=[1, 99])
    d_b, _ = compute_trial_projection_checksum()
    assert d_a != d_b


def test_excluded_participates():
    t = _trial('T1', [1], [])
    d_a, _ = compute_trial_projection_checksum()
    Trial.objects.filter(pk=t.pk).update(omop_therapy_types_excluded=[7])
    d_b, _ = compute_trial_projection_checksum()
    assert d_a != d_b


def test_identity_is_code_not_pk():
    # Two universes with the same (code, values) content but created in a different order
    # (→ different pks) must hash identically — identity is the CB-portable `code`.
    _trial('B', [2])
    _trial('A', [1])
    d1, _ = compute_trial_projection_checksum()
    Trial.objects.all().delete()
    _trial('A', [1])
    _trial('B', [2])
    d2, _ = compute_trial_projection_checksum()
    assert d1 == d2
