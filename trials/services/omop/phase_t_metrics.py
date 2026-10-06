"""Phase-T gating measurement (#263).

Phase-T — wiring live regimen→component graph expansion onto the eligibility path —
is a **measured** decision, not an automatic one (ADR 0002; epic #246). The gating
metric is: how often does a regimen arrive at matching **without** consumer-supplied
components? Under Phase P (#239) the consumer (promop) pre-expands and sends
``patient_component_ids``; when it doesn't, EXACT currently treats the component /
type criteria as *unknown* (it does NOT expand the regimen locally). If that
"regimen-unresolved" case is rare, Phase-T may not be worth the added coupling and
fail-closed 503s; if it is common, it justifies the flip.

This module is **pure observation** — no behavior change, no eligibility impact. It
emits a structured, greppable log event plus a process-local counter for tests.

Granularity: the signal is recorded **once per search** — the search queryset passes
``measure=True`` to the shared derivation, while the per-trial matcher leaves it off —
so one regimen-unresolved patient search emits one log line, not one per trial scored
(which would flood logs precisely when the metric matters most). It counts identifiers
**as they arrive** (not resolvable-only), a slight over-count that is fine for a
"did a regimen arrive without components" proxy.
"""
import logging

logger = logging.getLogger('omop.phase_t')

# Process-local; for tests/introspection only (not a durable metric — the durable
# signal is the log event below, counted in the log aggregator).
_regimen_unresolved_count = 0


def record_regimen_unresolved(regimen_identifiers):
    """Record one regimen-unresolved occurrence: a regimen was present at matching
    but no components were supplied by the consumer (Phase-T gating metric)."""
    global _regimen_unresolved_count
    _regimen_unresolved_count += 1
    logger.info(
        'regimen_unresolved: regimen present but no consumer-supplied components '
        '(Phase-T gating metric); regimen_count=%d',
        len(regimen_identifiers or []),
    )


def regimen_unresolved_count():
    """Occurrences recorded in this process since the last reset (tests only)."""
    return _regimen_unresolved_count


def reset_regimen_unresolved_count():
    """Reset the process-local counter (tests only)."""
    global _regimen_unresolved_count
    _regimen_unresolved_count = 0
