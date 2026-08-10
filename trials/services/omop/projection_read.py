"""EXACT read side of the CB-owned projection registry/snapshot (Design S, gap #2).

Thin accessors over the ``ProjectionRelease`` / ``ProjectionSnapshot`` read-models, which
the router sends to the CB-owned trials DB (``trials`` alias) when configured, else the
local ``default`` (single-DB / tests). This is the S4a foundation; the FULL read contract
(recompute the checksum vs ``manifest_checksum``, vocab equality on the patient release) and
the matcher-seam repoint land in S4b (#350). Read-only — EXACT never writes these (CB owns
and DB-guards them).
"""
from django.db.models import F

from trials.models import ProjectionRelease


def trusted_release_for(vocab_release_id):
    """The published, non-disabled ``ProjectionRelease`` bound to ``vocab_release_id``,
    or ``None``.

    This is the registry-state half of EXACT's read contract (``published ∧ ¬disabled ∧
    vocab equality``). The checksum-match half (recompute vs ``manifest_checksum``) is
    layered on by the activation gate / caller in S4b. If (unexpectedly) more than one is
    published for the same release, returns the newest by ``published_at``.
    """
    if not vocab_release_id:
        return None
    return (ProjectionRelease.objects
            .filter(vocab_release_id=str(vocab_release_id), published=True, disabled=False)
            .order_by(F('published_at').desc(nulls_last=True), '-projection_id')
            .first())
