"""Locked write of a trial's OMOP demographics columns (epic #4447).

Mirror of therapy_sync: single place that does the read-compute-write under a
per-trial row lock, so every writer (the batch backfill) serializes on the same
trial.id and a stale snapshot can't overwrite a newer one.

Ported from CancerBot (CB epic #4447). EXACT has no live post_save sync task —
in production CB owns the trials table and ships the populated omop_* columns;
EXACT only runs this from the backfill command (local single-DB mode). The lock
is scoped to the DB the trials model routes to (split-DB read-only trials DB),
unlike CB's bare ``atomic()`` which targets ``default``.
"""
from django.db import router, transaction

from trials.models import Trial
from trials.services.omop.demographics import build_omop_demographics


def sync_trial_omop_demographics(trial_id):
    """Recompute + persist a trial's omop demographics columns under a row lock.

    Returns ``(values, unmapped, changed)`` or ``(None, None, False)`` if the trial
    is gone. Writes via QuerySet.update() (no save-signal side effects) and only
    when values change.
    """
    _db = router.db_for_write(Trial) or 'default'
    with transaction.atomic(using=_db):
        trial = Trial.objects.select_for_update().filter(id=trial_id).first()
        if trial is None:
            return None, None, False

        values, unmapped = build_omop_demographics(trial)
        changed = {col: val for col, val in values.items() if getattr(trial, col) != val}
        if changed:
            Trial.objects.filter(id=trial_id).update(**changed)
        return values, unmapped, bool(changed)
