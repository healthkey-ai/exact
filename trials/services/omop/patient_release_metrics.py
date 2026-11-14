"""Gate 1 (#286) shadow observation — patient aggregate-release skew.

Pure observation for the OMOP-types cutover (mirrors ``type_release_metrics`` /
``phase_t_metrics``, #263): **no behaviour change**. Records, once per search,
whether the patient's aggregate ``therapy_release_id`` (promop#394) is NOT
release-consistent with the pinned vocab-mirror release — i.e. whether Gate 1
*would* fail-close the patient's type matching once enforcement lands (a later
slice). Measures the release-skew rate before then.

Convention (there is no metrics library in this codebase — see
``type_release_metrics``): the durable signal is the structured, greppable **log
event**; the process-local counters are for tests / introspection only.

Granularity: recorded **once per search** — the search queryset calls the gate with
``measure=True``; the per-trial matcher and the detail display leave it off, so one
release-skewed search emits one log line, not one per trial scored. This mirrors
``type_release_metrics`` exactly: single-trial ``retrieve`` / detail requests that
bypass the search queryset are NOT counted in either shadow metric (they are not
"searches"). Extending both metrics to record once per such request — via a
request-scoped measured-once guard — is a shared follow-up, out of scope for this
observe-only slice.
"""
import logging

logger = logging.getLogger('omop.patient_release')

# Process-local; tests/introspection only. The durable signal is the log event.
_skew_search_count = 0    # searches whose patient release was NOT consistent
_total_search_count = 0   # searches measured (consistent or not)


def record_patient_release_skew(release_consistent):
    """Record one measured search's Gate-1 outcome.

    ``release_consistent``: True when the patient's ``therapy_release_id`` equals the
    active mirror release (Gate 1 would pass). A False records release skew — the
    patient's class overlap is not release-consistent and Gate 1 would fail-close it
    once enforced. Only the False case logs, so the log stays quiet until skew occurs.
    """
    global _skew_search_count, _total_search_count
    _total_search_count += 1
    if release_consistent:
        return
    _skew_search_count += 1
    logger.info(
        'patient_release_skew: patient therapy_release_id not consistent with the '
        'active vocab-mirror release (#286 Gate 1 shadow; would fail-close type '
        'matching when enforced).')


def skew_search_count():
    """Release-skewed searches recorded since the last reset (tests)."""
    return _skew_search_count


def total_search_count():
    """Total measured searches since the last reset (tests)."""
    return _total_search_count


def reset():
    """Reset the process-local counters (tests only)."""
    global _skew_search_count, _total_search_count
    _skew_search_count = 0
    _total_search_count = 0
