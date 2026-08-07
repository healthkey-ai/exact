"""#286 Gate 1 — patient↔mirror release-consistency for OMOP drug-class TYPE matching.

Gate 2 (:mod:`type_release_gate`) validates each of the patient's class concept_ids
against the pinned vocab-mirror release. Gate 1 is coarser and orthogonal: the
patient's WHOLE aggregate class set (``therapy_type_ids``) must have been derived
against the SAME vocabulary generation the mirror is pinned to — else a stale
generation can silently change the concept_id OVERLAP even when every concept is
individually valid (a changed drug→class ``Is a`` expansion). See EXACT ADR 0002
§"Gate 1" (the decided architecture) + issue #286.

The patient carries ONE aggregate release token ``therapy_release_id`` (promop
VocabularyRelease pk as a decimal string; promop#394, unanimous-of-lines else null).
The check is a strict string compare against ``str(active_pinned_release())`` — never
``int(...)`` (which would coerce ``True``→1 / ``1.9``→1 and can raise on an over-long
digit string). Anything that is not a canonical decimal string equal to the active
release is **not release-consistent**.

This slice is **OBSERVE-ONLY**: it records the release-skew shadow metric + logs but
NEVER changes a verdict — :func:`release_gated_class_ids` returns the patient's class
ids unchanged. ENFORCEMENT (fail-closing type matching on a release mismatch) is a
deliberately separate later slice: it must be wired holistically across every seam's
fail-closed chokepoints (the detail-display type map, and the component-only
``eligible_for_therapy_related_things_from_lines`` short-circuit that returns ``self``
when both component and class ids are empty) so the queryset and matcher agree — and it
requires the trial-side attestation gaps closed first (ADR 0002 §Gate 1 gaps 1-2). Not
bolted on here.

Release resolution + memo mirror :mod:`type_release_gate`: the pinned release comes from
:func:`active_pinned_release` and the OK decision is memoized per-request via
:class:`patient_release_check_request_cache` (one decision per (release, patient-release)
per request; nothing process-global that could straddle a mirror rebuild).
"""
import contextvars
import logging

from vocab_mirror.release_context import active_pinned_release

logger = logging.getLogger(__name__)

# Defensive bound: the patient release is a small VocabularyRelease pk. A string
# longer than this is malformed → fail-closed (and never reaches any int()).
_MAX_RELEASE_LEN = 32

# Request-scoped memo (see module docstring). None outside a request → no caching.
_request_memo: contextvars.ContextVar = contextvars.ContextVar(
    'patient_release_check_memo', default=None)


def _compute(patient_release_id, active_release_id):
    """The uncached OK decision + observe-only log. True only when the patient's
    release is a canonical decimal string equal to the active mirror release."""
    if active_release_id is None:
        logger.warning(
            'OMOP-types patient-release check with no active vocab-mirror release; '
            'not release-consistent (fail-closed when enforced).')
        return False
    ok = (
        isinstance(patient_release_id, str)
        and patient_release_id.isascii()  # exclude non-ASCII digits (a promop pk is ASCII)
        and patient_release_id.isdigit()
        and 0 < len(patient_release_id) <= _MAX_RELEASE_LEN
        and patient_release_id == str(active_release_id)
    )
    if not ok:
        logger.warning(
            'OMOP-types patient-release mismatch: patient therapy_release_id=%r vs '
            'active mirror release=%r; overlap not release-consistent (fail-closed '
            'when enforced).', patient_release_id, active_release_id)
    return ok


def resolve_patient_release_ok(patient_release_id, measure=False):
    """True when the patient's aggregate ``therapy_release_id`` is release-consistent
    with the pinned mirror (strict decimal-string equality), else False (fail-closed).

    ``measure=True`` records the shadow release-skew metric — once per search (the
    queryset passes it; the per-trial matcher / detail display leave it False so the
    signal is emitted once per search, not once per trial). Observation only.
    """
    release_id = active_pinned_release()
    memo = _request_memo.get()
    if memo is None:
        ok = _compute(patient_release_id, release_id)
    else:
        cache_key = (release_id, patient_release_id)
        if cache_key not in memo:
            memo[cache_key] = _compute(patient_release_id, release_id)
        ok = memo[cache_key]
    if measure:
        from trials.services.omop.patient_release_metrics import record_patient_release_skew
        record_patient_release_skew(ok)
    return ok


def release_gated_class_ids(patient_info_attr, enabled, measure=False):
    """The patient's class ids for type matching, with the #286 Gate 1 release skew
    OBSERVED (this slice never changes them — enforcement is a later slice).

    Drop-in for ``get_user_therapy_type_ids() if enabled else None`` at every seam
    that consumes the patient's class ids (queryset prefilter, matcher verdict,
    detail display):

    - ``enabled`` False → ``None`` (legacy path untouched; byte-identical).
    - class ids absent (``None``) OR known-empty (``[]``) → ``None`` (nothing to
      observe; ``[]`` and ``None`` are treated identically by the downstream type
      filter, so this is verdict-neutral — and it keeps an empty patient set out of
      the skew metric, mirroring the ``type_release_gate`` empty early-return).
    - a non-empty set → the class ids **unchanged**, after recording (``measure=True``,
      the once-per-search queryset call) whether the patient's aggregate release is
      consistent with the pinned mirror. The return value is identical whether or not
      the release matches — no verdict change (see module docstring on enforcement).
    """
    if not enabled:
        return None
    class_ids = patient_info_attr.get_user_therapy_type_ids()
    if not class_ids:  # None (absent) or [] (known-empty) — nothing to observe
        return None
    resolve_patient_release_ok(
        patient_info_attr.get_user_therapy_release_id(), measure=measure)
    return class_ids


class patient_release_check_request_cache:
    """Enable the request-scoped patient-release-check memo for a ``with`` block.

    Enter once per request (the trials view does, alongside
    ``type_validation_request_cache``) so the three seams share one decision; the
    memo resets on exit, so nothing persists across requests/workers or straddles a
    mirror rebuild.
    """

    def __enter__(self):
        self._token = _request_memo.set({})
        return self

    def __exit__(self, *exc):
        _request_memo.reset(self._token)
        return False
