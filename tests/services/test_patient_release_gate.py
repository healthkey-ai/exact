"""Unit tests for the #286 Gate 1 patient-release gate (ADR 0002 §Gate 1).

Covers the strict fail-closed resolver, the observe-only release_gated_class_ids
(returns the class ids unchanged — this slice only measures skew), the per-request
memo, and the shadow metric. Enforcement (fail-closing on a mismatch) is a later slice.
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


# ── input contract: the field survives _build_in_memory ────────────────────────

def test_therapy_release_id_survives_build_in_memory():
    from trials.services.patient_info.resolve import _build_in_memory
    pi = _build_in_memory({'therapy_release_id': '7'})
    assert pi.therapy_release_id == '7'  # not filtered out, not coerced to Decimal
