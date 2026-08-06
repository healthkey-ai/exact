"""Trial-projection release attestation (#265 / ADR 0002 mechanism #4).

CB owns the trial ``omop_*`` projection and its release provenance; when a
release-wide backfill has stamped every trial for release R, CB publishes an
attestation into EXACT's ``default`` DB via :func:`publish_projection_attestation`
(the ``publish_projection_attestation`` command wraps it; a remote HTTP endpoint is
a follow-up). EXACT's mirror activation gate reads it locally — never a cross-DB
read of the CB trials table — so the pointer flip stays atomic on one DB.
"""
from vocab_mirror.models import ProjectionAttestation

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
