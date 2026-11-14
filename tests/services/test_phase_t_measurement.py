"""Phase-T gating measurement (#263).

The regimen-unresolved counter must fire exactly when a regimen is present at
matching under OMOP but the consumer supplied no components — and never otherwise
(components supplied, no regimen, or the flag off). Observation only: the return
value of ``derive_component_and_type_values`` is unchanged by the measurement.
"""
import pytest
from django.test import override_settings

from trials.services.omop.therapy_graph import derive_component_and_type_values
from trials.services.omop.phase_t_metrics import (
    record_regimen_unresolved,
    regimen_unresolved_count,
    reset_regimen_unresolved_count,
)

REGIMEN_CID, COMP_CID = 900260, 900825

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_counter():
    reset_regimen_unresolved_count()
    yield
    reset_regimen_unresolved_count()


# ── the helper itself ────────────────────────────────────────────────────────

def test_record_increments_and_reset_clears():
    assert regimen_unresolved_count() == 0
    record_regimen_unresolved([REGIMEN_CID])
    record_regimen_unresolved([REGIMEN_CID])
    assert regimen_unresolved_count() == 2
    reset_regimen_unresolved_count()
    assert regimen_unresolved_count() == 0


def test_record_tolerates_none():
    record_regimen_unresolved(None)  # must not raise
    assert regimen_unresolved_count() == 1


# ── fired via the derivation chokepoint (only when measure=True) ─────────────

@override_settings(EXACT_OMOP_THERAPY=True)
def test_counts_when_regimen_present_and_no_components(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger='omop.phase_t'):
        result = derive_component_and_type_values([str(REGIMEN_CID)], None, measure=True)
    assert result == (None, None)          # behavior unchanged
    assert regimen_unresolved_count() == 1
    # the durable signal is the log event — assert it actually fired
    assert any('regimen_unresolved' in r.message for r in caplog.records)


@override_settings(EXACT_OMOP_THERAPY=True)
def test_no_count_when_measure_off_matcher_path():
    # The matcher path leaves measure=False → per-trial calls never record, so the
    # signal stays once-per-search (no log flood).
    derive_component_and_type_values([str(REGIMEN_CID)], None)  # measure defaults False
    assert regimen_unresolved_count() == 0


@override_settings(EXACT_OMOP_THERAPY=True)
def test_no_count_when_components_supplied():
    derive_component_and_type_values([str(REGIMEN_CID)], [str(COMP_CID)], measure=True)
    assert regimen_unresolved_count() == 0


@override_settings(EXACT_OMOP_THERAPY=True)
def test_no_count_when_components_known_empty():
    # [] is a known-empty component set (the consumer answered), not unresolved.
    derive_component_and_type_values([str(REGIMEN_CID)], [], measure=True)
    assert regimen_unresolved_count() == 0


@override_settings(EXACT_OMOP_THERAPY=True)
def test_no_count_when_no_regimen():
    # Nothing to resolve → not a Phase-T-relevant miss.
    derive_component_and_type_values([], None, measure=True)
    assert regimen_unresolved_count() == 0


@override_settings(EXACT_OMOP_THERAPY=False)
def test_no_count_when_flag_off():
    # Legacy path never involves consumer components; not a Phase-T signal.
    derive_component_and_type_values([str(REGIMEN_CID)], None, measure=True)
    assert regimen_unresolved_count() == 0
