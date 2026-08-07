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

**Default OBSERVE-ONLY** (:data:`_PATIENT_RELEASE_GATE_ENFORCE` is False): the gate
records the release-skew shadow metric + logs but NEVER changes a verdict —
:func:`release_gated_class_ids` returns the patient's class ids unchanged.

**Enforcement** is composed into :func:`type_release_gate.resolve_type_validation` (which
all three seams — queryset prefilter, matcher verdict, detail display — already call): when
enforced and the request's aggregate patient release is inconsistent with the pinned mirror,
the WHOLE class set is treated as unvalidated (``(set(), True)``), so every seam fails closed
CONSISTENTLY via the existing Gate-2 machinery. This composes rather than gating the class-id
VALUE, so it does not disturb the two fail-closed chokepoints (the detail-display type map and
the component-only ``eligible_for_therapy_related_things_from_lines`` ``return self``
short-circuit) — the raw class ids keep flowing; only the (validated, unvalidated) result
changes. Do NOT flip the toggle before the trial-side attestation is verified +
immutable/revocable (ADR 0002 §Gate 1 gaps 1-2).

The patient release reaches :func:`gate1_fail_closed` two ways so EVERY entry point is
covered without a per-entry-point set-point: the **matcher verdict / detail-display**
seams pass it EXPLICITLY (they hold the patient_info_attr); the **queryset prefilter**
reads it from the :class:`patient_release_scope` contextvar that
:meth:`filter_by_patient_info` binds for the eligibility build (that method is the one
funnel for the HTTP view (via filtered_trials), the embedded ``ExactMatcher`` backend,
and mgmt commands). Outside a seam (unit-test / non-seam callers) neither is set →
``_UNSET`` → Gate 1 not applied, so Gate-2-only callers are unaffected.

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

# Enforcement toggle (mirrors vocab_mirror.activation._PROJECTION_GATE_ENFORCE). While
# False the gate is OBSERVE-ONLY — release_gated_class_ids records skew but nothing
# changes a verdict. Flip to True ONLY once the trial-side attestation is verified +
# immutable/revocable (ADR 0002 §Gate 1 gaps 1-2). When True, a patient whose aggregate
# release is inconsistent with the pinned mirror has its WHOLE class set rejected —
# composed into resolve_type_validation as (set(), unvalidated) so all three seams
# (queryset prefilter, matcher verdict, detail display) fail closed consistently.
_PATIENT_RELEASE_GATE_ENFORCE = False

# The queryset-path patient release, bound for the duration of a `filter_by_patient_info`
# eligibility build (which EVERY search entry point funnels through — HTTP view via
# filtered_trials, the embedded ExactMatcher backend, mgmt commands, and direct callers)
# and reset on exit so it never leaks across calls on a reused worker.
# The matcher verdict / detail-display seams do NOT use this — they hold the
# patient_info_attr and pass the release EXPLICITLY to resolve_type_validation, so Gate 1
# covers every path without a per-entry-point set-point. _UNSET distinguishes "no release
# context" (unit-test / non-seam caller → Gate 1 not applied) from "release is None"
# (patient carries none → fail-closed when enforced).
_UNSET = object()
_request_patient_release: contextvars.ContextVar = contextvars.ContextVar(
    'request_patient_release', default=_UNSET)


class patient_release_scope:
    """Bind the patient's aggregate release for the enclosed queryset build (entered by
    ``filter_by_patient_info``, the one chokepoint every search entry point funnels
    through). Reset on exit, so a value never leaks to the next call on a reused
    worker thread."""

    def __init__(self, release_id):
        self._release = release_id

    def __enter__(self):
        self._token = _request_patient_release.set(self._release)
        return self

    def __exit__(self, *exc):
        _request_patient_release.reset(self._token)
        return False


def gate1_fail_closed(patient_release_id=_UNSET, active_release_id=_UNSET):
    """True when Gate 1 must fail-close the patient's type matching: enforcement is on and
    the patient's aggregate release is NOT consistent with the pinned mirror.

    The release comes either EXPLICITLY (``patient_release_id`` — the matcher verdict /
    detail-display seams pass it, holding the patient_info_attr) or, when not supplied,
    from the :class:`patient_release_scope` contextvar (the queryset prefilter path).
    ``_UNSET`` in both → no release context (unit-test / non-seam caller) → Gate 1 not
    applied, so the observe-only path and every Gate-2-only caller are byte-identical. A
    release of ``None`` (patient carries none) → fail-closed when enforced.

    ``active_release_id`` is the pinned mirror release read ONCE by the caller
    (resolve_type_validation) and reused for both gates so a concurrent activation can't
    split the two checks across generations."""
    if not _PATIENT_RELEASE_GATE_ENFORCE:
        return False
    rel = patient_release_id if patient_release_id is not _UNSET else _request_patient_release.get()
    if rel is _UNSET:
        return False
    return not resolve_patient_release_ok(rel, active_release_id=active_release_id)


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


def resolve_patient_release_ok(patient_release_id, measure=False, active_release_id=_UNSET):
    """True when the patient's aggregate ``therapy_release_id`` is release-consistent
    with the pinned mirror (strict decimal-string equality), else False (fail-closed).

    ``active_release_id`` lets the caller PIN the release read once and reuse it for both
    Gate 1 and Gate 2 (resolve_type_validation does this) so a concurrent activation can't
    validate the release token against one generation and the class ids against another.
    When ``_UNSET`` it reads :func:`active_pinned_release` itself (the observe-only
    ``release_gated_class_ids`` path).

    ``measure=True`` records the shadow release-skew metric — once per search (the
    queryset passes it; the per-trial matcher / detail display leave it False so the
    signal is emitted once per search, not once per trial). Observation only.
    """
    release_id = active_pinned_release() if active_release_id is _UNSET else active_release_id
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
