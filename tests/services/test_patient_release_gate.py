"""Unit tests for the #286 Gate 1 patient-release gate (ADR 0002 §Gate 1).

Covers the strict fail-closed resolver, the observe-only release_gated_class_ids
(returns the class ids unchanged — measures skew), the per-request memo, the shadow
metric, and the enforcement decision gate1_fail_closed (composed into
resolve_type_validation; observe-only by default via _PATIENT_RELEASE_GATE_ENFORCE).
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.omop import patient_release_gate as prg
from trials.services.omop import patient_release_metrics as prm

pytestmark = pytest.mark.django_db


def _pin(monkeypatch, active):
    """Force active_pinned_release() to return `active` (int or None)."""
    monkeypatch.setattr(prg, 'active_pinned_release', lambda: active)


def _attr(**kw):
    return PatientInfoAttributes(PatientInfo(**kw))


# ── resolve_patient_release_ok — strict, fail-closed ────────────────────────────

def test_match_is_ok(monkeypatch):
    _pin(monkeypatch, 7)
    assert prg.resolve_patient_release_ok('7') is True


def test_mismatch_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    assert prg.resolve_patient_release_ok('8') is False


def test_no_active_release_fails_closed(monkeypatch):
    _pin(monkeypatch, None)
    assert prg.resolve_patient_release_ok('7') is False


def test_none_patient_release_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    assert prg.resolve_patient_release_ok(None) is False


def test_non_string_patient_release_fails_closed(monkeypatch):
    _pin(monkeypatch, 1)
    # int(True)==1 would fail open; the string compare rejects a bool/int/float.
    assert prg.resolve_patient_release_ok(True) is False
    assert prg.resolve_patient_release_ok(1) is False
    assert prg.resolve_patient_release_ok(1.0) is False


def test_non_digit_patient_release_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    assert prg.resolve_patient_release_ok('7a') is False


def test_over_length_patient_release_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    assert prg.resolve_patient_release_ok('1' * 33) is False


# ── release_gated_class_ids — observe-only vs enforced ──────────────────────────

def test_disabled_returns_none(monkeypatch):
    _pin(monkeypatch, 7)
    a = _attr(therapy_type_ids=[35807295], therapy_release_id='7')
    assert prg.release_gated_class_ids(a, enabled=False) is None


def test_absent_class_ids_returns_none(monkeypatch):
    _pin(monkeypatch, 7)
    a = _attr(therapy_release_id='7')  # no therapy_type_ids
    assert prg.release_gated_class_ids(a, enabled=True) is None


def test_empty_class_ids_returns_none_and_records_no_skew(monkeypatch):
    # Known-empty ([]) is verdict-neutral (== None downstream) AND must NOT record a
    # skew measurement — an empty patient set is not release-relevant (parity with the
    # type_release_gate empty early-return).
    _pin(monkeypatch, 7)
    prm.reset()
    a = _attr(therapy_type_ids=[], therapy_release_id='8')  # stale release, but empty set
    assert prg.release_gated_class_ids(a, enabled=True, measure=True) is None
    assert prm.total_search_count() == 0  # no measurement recorded


def test_consistent_release_returns_class_ids(monkeypatch):
    _pin(monkeypatch, 7)
    a = _attr(therapy_type_ids=[35807295, 35807403], therapy_release_id='7')
    assert prg.release_gated_class_ids(a, enabled=True) == ['35807295', '35807403']


def test_mismatch_returns_class_ids_unchanged(monkeypatch):
    # OBSERVE-ONLY: a stale patient release NEVER changes the class ids (this slice
    # only measures skew; enforcement is a later slice).
    _pin(monkeypatch, 7)
    a = _attr(therapy_type_ids=[35807295], therapy_release_id='8')
    assert prg.release_gated_class_ids(a, enabled=True) == ['35807295']


def test_null_release_returns_class_ids_unchanged(monkeypatch):
    # OBSERVE-ONLY: patient with types but no release still gets its class ids.
    _pin(monkeypatch, 7)
    a = _attr(therapy_type_ids=[35807295])  # no therapy_release_id
    assert prg.release_gated_class_ids(a, enabled=True) == ['35807295']


# ── per-request memo ────────────────────────────────────────────────────────────

def test_memo_dedups_within_request(monkeypatch):
    _pin(monkeypatch, 7)
    calls = {'n': 0}
    real_compute = prg._compute

    def _counting(patient_release_id, active_release_id):
        calls['n'] += 1
        return real_compute(patient_release_id, active_release_id)
    monkeypatch.setattr(prg, '_compute', _counting)
    with prg.patient_release_check_request_cache():
        assert prg.resolve_patient_release_ok('7') is True
        assert prg.resolve_patient_release_ok('7') is True
    # The memoized work (_compute) runs once for the repeated (release, patient) key.
    assert calls['n'] == 1


def test_no_memo_outside_request_recomputes(monkeypatch):
    _pin(monkeypatch, 7)
    calls = {'n': 0}
    real_compute = prg._compute

    def _counting(patient_release_id, active_release_id):
        calls['n'] += 1
        return real_compute(patient_release_id, active_release_id)
    monkeypatch.setattr(prg, '_compute', _counting)
    # No request-cache context → no memo → each call recomputes.
    prg.resolve_patient_release_ok('7')
    prg.resolve_patient_release_ok('7')
    assert calls['n'] == 2


# ── shadow metric ───────────────────────────────────────────────────────────────

def test_measure_records_skew_on_mismatch(monkeypatch):
    _pin(monkeypatch, 7)
    prm.reset()
    a = _attr(therapy_type_ids=[35807295], therapy_release_id='8')
    prg.release_gated_class_ids(a, enabled=True, measure=True)
    assert prm.skew_search_count() == 1
    assert prm.total_search_count() == 1


def test_measure_no_skew_on_match(monkeypatch):
    _pin(monkeypatch, 7)
    prm.reset()
    a = _attr(therapy_type_ids=[35807295], therapy_release_id='7')
    prg.release_gated_class_ids(a, enabled=True, measure=True)
    assert prm.skew_search_count() == 0
    assert prm.total_search_count() == 1


def test_no_measure_records_nothing(monkeypatch):
    _pin(monkeypatch, 7)
    prm.reset()
    a = _attr(therapy_type_ids=[35807295], therapy_release_id='8')
    prg.release_gated_class_ids(a, enabled=True, measure=False)
    assert prm.total_search_count() == 0


# ── gate1_fail_closed — the enforcement decision (composed into resolve_type_validation) ──

def _enforce(monkeypatch, on=True):
    monkeypatch.setattr(prg, '_PATIENT_RELEASE_GATE_ENFORCE', on)


def test_gate1_toggle_off_never_fails_closed(monkeypatch):
    # Observe-only default: even a mismatched release must not fail closed.
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, False)
    assert prg.gate1_fail_closed('8') is False           # explicit mismatch, toggle off
    with prg.patient_release_scope('8'):
        assert prg.gate1_fail_closed() is False           # contextvar mismatch, toggle off


def test_gate1_no_context_not_applied(monkeypatch):
    # No explicit release AND no scope → _UNSET → Gate 1 not applied (Gate-2-only callers).
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    assert prg.gate1_fail_closed() is False


# --- explicit release (matcher verdict / detail-display seams) ---

def test_gate1_explicit_consistent_passes(monkeypatch):
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    assert prg.gate1_fail_closed('7') is False


def test_gate1_explicit_mismatch_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    assert prg.gate1_fail_closed('8') is True


def test_gate1_explicit_null_release_fails_closed(monkeypatch):
    # A patient carrying no release → unknown → fail-closed when enforced.
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    assert prg.gate1_fail_closed(None) is True


# --- scope contextvar (queryset prefilter path) ---

def test_gate1_scope_consistent_passes(monkeypatch):
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    with prg.patient_release_scope('7'):
        assert prg.gate1_fail_closed() is False


def test_gate1_scope_mismatch_fails_closed(monkeypatch):
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    with prg.patient_release_scope('8'):
        assert prg.gate1_fail_closed() is True


def test_gate1_explicit_overrides_scope(monkeypatch):
    # An explicit release wins over the scope contextvar (the seams always pass theirs).
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    with prg.patient_release_scope('8'):                  # scope stale
        assert prg.gate1_fail_closed('7') is False         # explicit consistent → passes


def test_gate1_scope_resets_after_context(monkeypatch):
    # The scope contextvar must not leak past its `with` (no cross-call leak on a worker).
    _pin(monkeypatch, 7)
    _enforce(monkeypatch, True)
    with prg.patient_release_scope('8'):
        assert prg.gate1_fail_closed() is True
    assert prg.gate1_fail_closed() is False  # back to _UNSET → not applied


def test_pin_once_gate1_and_gate2_share_one_release_read(monkeypatch):
    # resolve_type_validation must read active_pinned_release ONCE and reuse it for both
    # Gate 1 (release token) and Gate 2 (per-concept validity) — a concurrent activation
    # between two reads could otherwise split the checks across generations.
    from trials.services.omop import type_release_gate as trg
    calls = {'n': 0}

    def _counting():
        calls['n'] += 1
        return 1
    monkeypatch.setattr(trg, 'active_pinned_release', _counting)
    _enforce(monkeypatch, True)
    with prg.patient_release_scope('1'):          # consistent release → Gate 1 passes, Gate 2 runs
        trg.resolve_type_validation(['35807295'])
    assert calls['n'] == 1


# ── input contract: the field survives _build_in_memory ────────────────────────

def test_therapy_release_id_survives_build_in_memory():
    from trials.services.patient_info.resolve import _build_in_memory
    pi = _build_in_memory({'therapy_release_id': '7'})
    assert pi.therapy_release_id == '7'  # not filtered out, not coerced to Decimal
