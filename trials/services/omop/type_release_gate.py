"""#286 Gate 2 — per-concept release-consistency for OMOP drug-class TYPE ids.

Validates the patient's OWN drug-class concept_ids against the pinned vocab-mirror
release (present + ``invalid_reason IS NULL``, via :mod:`vocab_mirror.validate`) and
reports them ASYMMETRICALLY to the two therapy-matching seams (matcher + queryset):

- **required**: a stale/absent class id is DROPPED → fail-closed for that id (a
  required-type trial whose only patient match was stale resolves to not_matched).
- **excluded**: a stale/unvalidated class id is NEVER dropped — dropping it would
  turn a real exclusion hit into a match (fail-OPEN). If the patient carries ANY
  unvalidated class id, the caller must conservatively reject a trial that excludes
  types (see ``has_unvalidated``).

This lives here, NOT in ``derive_component_and_type_values``: derive must keep
returning the RAW class ids so the excluded side can still see the unvalidated ones
(filtering inside derive would lose them and re-open the exclusion). See #286 design.

Release resolution + memo mirror the component-lookup gate
(:class:`component_category_lookup.component_lookup_request_cache`): the pinned
release comes from :func:`active_pinned_release` (the per-request pin, or the live
active release for non-HTTP callers), and validation is memoized per-request via
:class:`type_validation_request_cache` — one mirror query per (release, class-id set)
per request, never a process-global cache (a mirror rebuild between requests is
always picked up; outside a request the memo is unset and every call reads directly).

Fail-closed: when no release is pinned/active (``active_pinned_release()`` is None),
every id is treated as unvalidated — required drops to empty (→ not_matched for a
required-type trial) and any excluded constraint is conservatively rejected. Never a
silent fallback to the unvalidated raw ids.
"""
import contextvars
import logging

from vocab_mirror.release_context import active_pinned_release
from vocab_mirror.validate import validate_concept_ids

logger = logging.getLogger(__name__)

# Request-scoped memo (see module docstring). None outside a request → no caching.
_request_memo: contextvars.ContextVar = contextvars.ContextVar(
    'type_validation_request_memo', default=None)


def _normalized(type_ids):
    """Stable, hashable key for a patient class-id set (order-independent)."""
    return tuple(sorted((str(x).strip() for x in type_ids), key=str))


def resolve_type_validation(type_ids):
    """Validate the patient's raw class ``type_ids`` at the pinned release.

    Returns ``(validated, has_unvalidated)`` where ``validated`` is the set (of
    ``str``) of ids present + valid in the mirror, and ``has_unvalidated`` is True
    when ANY input id is absent/invalid — including the no-release fail-closed case.

    ``type_ids`` None/``[]`` → ``(set(), False)``: nothing to validate here; the
    existing #285 unknown/empty fail-closed handles those patient sets upstream.
    """
    if not type_ids:
        return set(), False
    key = _normalized(type_ids)
    release_id = active_pinned_release()
    memo = _request_memo.get()
    if memo is None:
        validated = _validate(key, release_id)
    else:
        cache_key = (release_id, key)
        if cache_key not in memo:
            memo[cache_key] = _validate(key, release_id)
        validated = memo[cache_key]
    has_unvalidated = any(cid not in validated for cid in key)
    return validated, has_unvalidated


def _validate(normalized_ids, release_id):
    """One mirror query → the set of validated ids (as ``str``).

    No release (``release_id is None``) → fail closed: the empty set, so every id
    counts as unvalidated. Never falls back to the raw ids.
    """
    if release_id is None:
        # Fail closed, but loudly: with OMOP-types on and no active mirror release,
        # EVERY required-type trial drops to not_matched and every excluded-type
        # trial is conservatively rejected — a large, otherwise-invisible drop in a
        # clinical matcher. Memoized upstream, so this fires ~once per request.
        logger.warning(
            'OMOP-types release validation requested with no active vocab-mirror '
            'release; failing closed — all patient class ids treated as unvalidated '
            '(required types dropped, excluded-type trials rejected).')
        return set()
    return {str(c) for c in validate_concept_ids(normalized_ids, release_id)}


class type_validation_request_cache:
    """Enable the request-scoped type-validation memo for a ``with`` block.

    Enter once per request (the trials view does, alongside
    ``component_lookup_request_cache``) so the per-trial matcher dedups the
    patient's class-id validation to one mirror query; the memo resets on exit, so
    nothing persists across requests/workers or straddles a mirror rebuild.
    """

    def __enter__(self):
        self._token = _request_memo.set({})
        return self

    def __exit__(self, *exc):
        _request_memo.reset(self._token)
        return False
