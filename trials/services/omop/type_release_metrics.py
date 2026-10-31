"""Gate 2 (#286) shadow observation — patient class-id release staleness.

Pure observation for the OMOP-types cutover (mirrors ``phase_t_metrics``, #263):
**no behaviour change**. Records, once per search, how often a patient arrives
carrying drug-class concept_ids that are stale — absent or invalidated in the
pinned vocab-mirror release — capturing the two #286 shadow signals:

- **dropped-stale ids**: how many of the patient's class ids were dropped from
  required matching (fail-closed for those ids);
- **exclusion-block active**: whether the conservative excluded-type block is in
  effect for the search (any unvalidated id → every excluded-type trial is
  conservatively ``not_matched``; see ``type_release_gate`` / L2 blast-radius).

Convention (there is no metrics library in this codebase — see
``phase_t_metrics``): the durable signal is the structured, greppable **log
event**; the process-local counters are for tests / introspection only.

Granularity: recorded **once per search** — the search queryset calls the
validator with ``measure=True``; the per-trial matcher and the detail display
leave it off, so one stale-patient search emits one log line, not one per trial
scored (which would flood logs precisely when the signal matters most).
"""
import logging

logger = logging.getLogger('omop.type_release')

# Process-local; tests/introspection only. The durable signal is the log event.
_stale_search_count = 0    # searches whose patient carried >=1 stale class id
_dropped_ids_total = 0     # total stale class ids dropped across those searches


def record_type_staleness(dropped_count, exclusion_block_active):
    """Record one search whose patient carried stale class ids.

    ``dropped_count``: how many of the patient's class concept_ids were absent /
    invalid in the pinned release (dropped from required matching). A value ``<= 0``
    is a no-op — a healthy search (mirror covers every id) records nothing, so the
    log stays quiet until staleness actually occurs.

    ``exclusion_block_active``: True if the search also has the conservative
    excluded-type block in effect (the patient carries an unvalidated id), so every
    excluded-type trial in the search is conservatively ``not_matched``.
    """
    global _stale_search_count, _dropped_ids_total
    if dropped_count <= 0:
        return
    _stale_search_count += 1
    _dropped_ids_total += dropped_count
    logger.info(
        'type_release_staleness: patient carried %d stale class id(s) dropped from '
        'required matching (#286 shadow); exclusion_block_active=%s',
        dropped_count, bool(exclusion_block_active),
    )


def stale_search_count():
    """Searches with >=1 stale patient class id recorded since the last reset (tests)."""
    return _stale_search_count


def dropped_ids_total():
    """Total stale class ids dropped across recorded searches since reset (tests)."""
    return _dropped_ids_total


def reset():
    """Reset the process-local counters (tests only)."""
    global _stale_search_count, _dropped_ids_total
    _stale_search_count = 0
    _dropped_ids_total = 0
