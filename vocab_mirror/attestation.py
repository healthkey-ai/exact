"""Trial-projection release attestation (#265 / ADR 0002 mechanism #4).

CB owns the trial ``omop_*`` projection and its release provenance; when a
release-wide backfill has stamped every trial for release R, CB publishes an
attestation into EXACT's ``default`` DB via :func:`publish_projection_attestation`
(the ``publish_projection_attestation`` command wraps it; a remote HTTP endpoint is
a follow-up). EXACT's mirror activation gate reads it locally — never a cross-DB
read of the CB trials table — so the pointer flip stays atomic on one DB.
"""
from vocab_mirror.models import ProjectionAttestation

# The attestation currently lives on EXACT's ``default`` DB (published via EXACT's mgmt
# command). ADR 0003 ratified **Option D** — CB seals an immutable attestation in the
# CB-owned ``trials`` DB and EXACT reads it via the trials alias. That relocation (moving
# this model + all three helpers off ``default`` to a routed read model, and CB owning the
# write) is a SEPARATE, module-wide change tracked as the Option-D relocation issue; the
# checksum-verify below is orthogonal and consistent with the current on-``default`` module.
# When the relocation lands, ``_DB`` moves for all three helpers together.
_DB = 'default'


def publish_projection_attestation(release_id, run_id='', trial_count=None,
                                   checksum=''):
    """Upsert the attestation that CB's trial ``omop_*`` projection is ready for
    ``release_id``. Idempotent (one row per release; re-publish updates it)."""
    obj, _ = ProjectionAttestation.objects.using(_DB).update_or_create(
        release_id=int(release_id),
        defaults=dict(run_id=run_id or '', trial_count=trial_count,
                      checksum=checksum or ''),
    )
    return obj


def projection_attested(release_id):
    """True when CB has published a projection attestation for ``release_id``."""
    return ProjectionAttestation.objects.using(_DB).filter(
        release_id=int(release_id)).exists()


def get_projection_attestation(release_id):
    """The ``ProjectionAttestation`` row for ``release_id`` (with its ``checksum`` /
    ``trial_count``), or ``None`` if CB has not published one. Used by the activation gate
    to VERIFY the checksum, not just its existence (ADR 0002 §Gate 1 gap #1)."""
    return ProjectionAttestation.objects.using(_DB).filter(
        release_id=int(release_id)).first()
