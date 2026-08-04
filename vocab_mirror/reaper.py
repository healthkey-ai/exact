"""Retention / GC for old vocab-mirror generations (#259; ADR 0002).

Activation supersedes-and-**retains** the previous generation (its rows stay so
in-flight readers finish), and the sync only deletes rows for the release it
(re)stages. So SUPERSEDED generations — plus any stranded READY / FAILED / STAGING
ones left when promop's ``latest`` jumps to a newer release id — accumulate
forever, at ~18M ``concept_relationship`` rows each. This reaper prunes them.

Policy (all of these are KEPT; everything else is dropped):
- the current **ACTIVE** generation — always;
- the ``keep`` most-recently-touched non-active generations — so a reader still
  finishing on the just-superseded generation is not pulled out from under;
- any generation younger than ``min_age`` — the retention window.

Dropping a generation deletes its rows in all four mirror tables **and** its
``MirrorRelease`` row. Runs under the sync singleton advisory lock (via
``reap_under_lock``) so it never races a sync/activation writer.
"""
import logging
from datetime import timedelta

from django.db import connections
from django.utils import timezone

from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)

logger = logging.getLogger(__name__)

_DB = 'default'
# The four generation-tagged data tables (not MirrorRelease, which is the pointer).
_REAP_MODELS = (
    MirrorVocabulary, MirrorConcept, MirrorConceptRelationship, MirrorConceptAncestor,
)
# Reuse the #250 sync singleton lock so a reap never races a sync/activation.
_SYNC_ADVISORY_LOCK_KEY = 8234502250

DEFAULT_KEEP = 1
DEFAULT_MIN_AGE = timedelta(hours=6)


def _recency(rel):
    return rel.updated_at or rel.created_at


def reap_old_generations(keep=DEFAULT_KEEP, min_age=DEFAULT_MIN_AGE, now=None,
                         dry_run=False):
    """Drop stale non-ACTIVE generations. See the module docstring for the policy.

    Returns a list of ``{'release_id', 'state', 'rows'}`` for the generations
    reaped (or that *would* be reaped when ``dry_run``). ``rows`` maps each mirror
    model name to its deleted row count (0s under ``dry_run``).
    """
    now = now or timezone.now()
    cutoff = now - min_age

    active_ids = set(
        MirrorRelease.objects.using(_DB)
        .filter(state=MirrorRelease.ACTIVE).values_list('release_id', flat=True))
    # Most-recently-touched first, so `keep` protects the freshest non-active ones.
    # NOTE: `keep` protects the most-recently-*touched* non-active generations,
    # which is not strictly the previously-active one — a later STAGING→FAILED
    # restage bumps a different generation's updated_at and can steal the slot.
    # Reader safety therefore rests on the time-based `min_age` retention window,
    # not on `keep`; `keep` is a belt-and-braces buffer for the common no-churn
    # case (on supersession activation bumps the old-active row's updated_at, so it
    # is the freshest non-active row and keep>=1 covers it).
    non_active = list(
        MirrorRelease.objects.using(_DB).exclude(state=MirrorRelease.ACTIVE)
        .order_by('-updated_at', '-release_id'))
    keep_ids = set(active_ids) | {r.release_id for r in non_active[:keep]}

    reaped = []
    for rel in non_active:
        if rel.release_id in keep_ids:
            continue
        ts = _recency(rel)
        if ts is not None and ts > cutoff:
            continue  # inside the retention window
        rows = {}
        if dry_run:
            for model in _REAP_MODELS:
                rows[model.__name__] = (
                    model.objects.using(_DB).filter(release_id=rel.release_id).count())
        else:
            # Deliberately NOT one big transaction: a generation's
            # concept_relationship alone can be ~18M rows, and wrapping all four
            # tables in a single txn holds a long-running transaction (WAL bloat,
            # blocks vacuum, holds the advisory lock longer). Safety instead comes
            # from (a) the retention window — `min_age` guarantees no live reader is
            # still on a reaped generation, so a partial delete is never observed —
            # and (b) deleting the data tables first and the `MirrorRelease` pointer
            # LAST, so an interrupted reap leaves recoverable data+pointer (never
            # orphan data) that the next run re-selects and finishes idempotently.
            for model in _REAP_MODELS:
                deleted, _ = (
                    model.objects.using(_DB).filter(release_id=rel.release_id).delete())
                rows[model.__name__] = deleted
            rel.delete(using=_DB)
        reaped.append({'release_id': rel.release_id, 'state': rel.state, 'rows': rows})
        logger.info('vocab reaper: %s generation %s (%s)',
                    'would reap' if dry_run else 'reaped', rel.release_id, rel.state)
    return reaped


def reap_under_lock(**kwargs):
    """Run :func:`reap_old_generations` holding the sync singleton advisory lock.

    Returns ``None`` (skipped) if another sync/reap holds the lock, else the reap
    result. Use this for the standalone command; the sync job, already holding the
    lock, calls :func:`reap_old_generations` directly.
    """
    with connections[_DB].cursor() as cur:
        cur.execute('SELECT pg_try_advisory_lock(%s)', [_SYNC_ADVISORY_LOCK_KEY])
        if not cur.fetchone()[0]:
            logger.info('vocab reaper: another sync/reap holds the lock; skipping')
            return None
        try:
            return reap_old_generations(**kwargs)
        finally:
            cur.execute('SELECT pg_advisory_unlock(%s)', [_SYNC_ADVISORY_LOCK_KEY])
