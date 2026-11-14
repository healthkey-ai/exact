"""Release-consistency validator for OMOP concept_ids (#286).

Narrow read-port over the mirror so the therapy matcher can check that the
concept_ids a patient carries are PRESENT and VALID in the pinned vocab release,
without spreading `MirrorConcept` ORM access through therapy-graph code.

Per the #286 design (per-concept staleness, not per-release equality): only the
stability of the SPECIFIC concept_ids a patient carries matters for concept_id
overlap. A concept that is absent or invalidated (`invalid_reason` set) in the
active release is stale for that patient.

The caller MUST resolve and pass the pinned release_id (typically
`release_context.active_pinned_release()` — the per-request pin, NOT a live read).
Passing `release_id=None` raises `ConceptGraphUnavailable` so the caller fails
closed — never a silent fallback to the live active release (which could straddle
an activation mid-request, or run unpinned for non-HTTP callers).
"""
from vocab_mirror.models import MirrorConcept
from vocab_mirror.traversal import ConceptGraphUnavailable, _positive_ints, _DB


def validate_concept_ids(concept_ids, release_id):
    """Return the set (of ints) of ``concept_ids`` that are present and valid
    (``invalid_reason IS NULL``) in the mirror at ``release_id``.

    ``release_id`` is required — ``None`` raises ``ConceptGraphUnavailable`` so the
    caller fails closed. Non-digit / non-positive inputs are ignored. An empty or
    all-invalid input returns an empty set.
    """
    if release_id is None:
        raise ConceptGraphUnavailable(
            'no active vocab mirror release; refusing to validate concept ids')
    ids = _positive_ints(concept_ids)
    if not ids:
        return set()
    return set(
        MirrorConcept.objects.using(_DB)
        .filter(release_id=release_id, concept_id__in=ids, invalid_reason__isnull=True)
        .values_list('concept_id', flat=True)
    )
