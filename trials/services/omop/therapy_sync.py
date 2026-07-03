"""Locked write of a trial's OMOP therapy columns (#4457).

Single place that performs the read-compute-write under a per-trial row lock, so
every writer (here, the batch backfill command) serializes on the same trial.id
and a stale snapshot can never overwrite a newer one. Keeps the conversion logic
itself pure (see therapy_concept_mapper.build_omop_columns).

Ported from CancerBot (CB epic #4447). EXACT has no live post_save sync task —
in production CB owns the trials table and ships the populated omop_* columns;
EXACT only runs this from the backfill command (local single-DB mode).
"""
from django.db import router, transaction

from trials.models import Trial
from trials.services.omop.therapy_concept_mapper import build_omop_columns


def sync_trial_omop_columns(trial_id):
    """Recompute and persist a trial's omop_* therapy columns under a row lock.

    Returns ``(values, unmapped, changed)`` — the computed column values, the
    dropped (unknown/unmapped) legacy codes, and whether anything was written.
    Returns ``(None, None, False)`` if the trial no longer exists. Writes via
    QuerySet.update() (no signal re-fire) and only when values change.
    """
    _db = router.db_for_write(Trial) or 'default'
    with transaction.atomic(using=_db):
        trial = Trial.objects.select_for_update().filter(id=trial_id).first()
        if trial is None:
            return None, None, False

        values, unmapped = build_omop_columns(trial)
        changed = {col: val for col, val in values.items() if getattr(trial, col) != val}
        if changed:
            Trial.objects.filter(id=trial_id).update(**changed)
        return values, unmapped, bool(changed)
