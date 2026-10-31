"""Locked write of a trial's OMOP language-skill column (CB #5350).

Mirror of demographics_sync: one read-compute-write under a per-trial row lock,
used by the batch backfill.

Ported from CancerBot. EXACT has no live post_save sync task: in production CB
owns the trials table and ships the populated omop_* columns, so EXACT only runs
this from the backfill command (local single-DB mode). The lock is scoped to the
DB the trials model routes to (split-DB read-only trials DB), unlike CB's bare
``atomic()``, which targets ``default``.
"""
from django.db import router, transaction

from trials.models import Trial
from trials.services.omop.languages import build_omop_languages_skills


def sync_trial_omop_languages(trial_id, concept_ids=None):
    """Recompute + persist a trial's omop language-skill column under a row lock.

    Returns ``(values, unmapped, changed)`` or ``(None, None, False)`` if the trial
    is gone. Writes via QuerySet.update() (no save-signal side effects) and only
    when the value changes. ``concept_ids`` (from ``load_concept_ids()``) lets a
    batch caller read the vocab once.
    """
    _db = router.db_for_write(Trial) or 'default'
    with transaction.atomic(using=_db):
        trial = Trial.objects.select_for_update().filter(id=trial_id).first()
        if trial is None:
            return None, None, False

        values, unmapped = build_omop_languages_skills(trial, concept_ids)
        changed = {col: val for col, val in values.items() if getattr(trial, col) != val}
        if changed:
            Trial.objects.filter(id=trial_id).update(**changed)
        return values, unmapped, bool(changed)
