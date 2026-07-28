"""Sync the local OMOP vocabulary mirror from promop (promop#334, #250 / ADR 0002).

Flow (one run of :func:`sync_vocab_mirror`):

1. Hold a **Postgres advisory lock** (singleton writer — this is the sync
   singleton the activation control plane assumes, #249). If another sync holds
   it, exit cleanly (``locked``) — never two writers.
2. Conditional-poll ``vocab-releases/latest`` with the last **successfully
   processed** ETag. ``304`` → ``unchanged``, done. A failed release never
   advances that ETag (it is only read off READY/ACTIVE rows), so a broken load
   is retried next run.
3. For the new release id, load each table's NDJSON snapshot into a **fresh
   staging generation** (delete-by-release-then-insert — idempotent; the mirror
   tables carry no uniqueness, so a re-run must not append duplicates, #248).
4. Verify completeness per table: the ``{"__done": true, "rows": N}`` sentinel
   must be present (else the stream was truncated) and the loaded count must
   match both the sentinel and the manifest ``row_counts``. Any mismatch →
   mark the release FAILED, drop its rows, and raise (fail closed).
5. On success mark the release READY and (unless ``activate=False``) activate it
   via the #249 control plane.

Enforced completeness is the ``__done`` sentinel count plus the manifest
``row_counts`` cross-check. Two follow-ups (recorded, not blocking):
- **Checksum verification** of the manifest ``checksums`` — needs promop's exact
  per-table row canonicalization pinned before it can enforce without false
  mismatches; until then the sentinel + row-count gate rejects truncated/partial
  loads and the checksums are recorded for provenance only.
- **COPY for the large tables** — ``concept_relationship`` (~18M rows) currently
  loads via batched ``bulk_create`` into a table with live indexes; a
  ``psycopg.copy`` path (optionally dropping/rebuilding indexes per generation)
  will be much faster.
"""
import logging
from dataclasses import dataclass, field
from datetime import date

from django.db import connections
from django.utils import timezone

from vocab_mirror.activation import activate_release
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.promop_vocab_client import PromopVocabClient, VocabSyncError

logger = logging.getLogger(__name__)

_DB = 'default'  # the mirror lives on `default` (db_router / ADR §Placement)
# Fixed 64-bit key for the sync singleton advisory lock.
_SYNC_ADVISORY_LOCK_KEY = 8234502250
_BATCH_SIZE = 5000

# promop#334 table slug -> mirror model. EXACT syncs exactly the tables it needs.
TABLE_MODELS = {
    'vocabulary': MirrorVocabulary,
    'concept': MirrorConcept,
    'concept_relationship': MirrorConceptRelationship,
    'concept_ancestor': MirrorConceptAncestor,
}
DEFAULT_TABLES = ['vocabulary', 'concept', 'concept_relationship', 'concept_ancestor']


@dataclass
class SyncOutcome:
    status: str  # 'unchanged' | 'already_synced' | 'synced' | 'locked'
    release_id: int | None = None
    counts: dict = field(default_factory=dict)


def _row_projector(model):
    """Build a fast row → model-kwargs projector for a mirror model.

    Keeps only the model's own columns (promop may send extra columns; unknown
    keys are dropped) and coerces ISO date strings to ``date`` for DateFields.
    """
    fields = []
    for f in model._meta.concrete_fields:
        if f.name in ('id', 'release_id'):
            continue
        fields.append((f.name, f.get_internal_type() == 'DateField'))

    def project(row):
        out = {}
        for name, is_date in fields:
            v = row.get(name)
            if is_date:
                v = date.fromisoformat(v) if isinstance(v, str) and v else None
            out[name] = v
        return out

    return project


def _last_processed_etag():
    """ETag of the currently ACTIVE release (the one readers actually pin).

    Deliberately only ACTIVE — not READY: if a load succeeds (→ READY) but
    activation then fails (a ConcurrentActivation, a transient gate failure, a DB
    blip), the release must NOT 304-skip forever. Because its ETag is not counted
    here, the next run re-polls, finds the READY generation, and recovers it by
    (re)activating without re-downloading (see ``_do_sync``). FAILED/STAGING
    releases likewise never advance the pointer.
    """
    return (
        MirrorRelease.objects.using(_DB)
        .filter(state=MirrorRelease.ACTIVE)
        .exclude(etag__isnull=True)
        .values_list('etag', flat=True)
        .first()
    )


def _delete_release_rows(release_id):
    for model in TABLE_MODELS.values():
        model.objects.using(_DB).filter(release_id=release_id).delete()


def _load_table(client, release_id, table, expected_count):
    """Stream + bulk-load one table into the (fresh) staging generation.

    Returns the loaded row count. Raises ``VocabSyncError`` on a truncated
    stream (no sentinel) or a count that disagrees with the sentinel / manifest.
    """
    model = TABLE_MODELS[table]
    project = _row_projector(model)
    model.objects.using(_DB).filter(release_id=release_id).delete()  # idempotent restage

    count = 0
    batch = []
    sentinel = None
    gen = client.stream_snapshot(release_id, table)
    try:
        for row in gen:
            if row.get('__done'):
                sentinel = row.get('rows')
                break
            batch.append(model(release_id=release_id, **project(row)))
            if len(batch) >= _BATCH_SIZE:
                model.objects.using(_DB).bulk_create(batch)
                count += len(batch)
                batch = []
        else:
            # Stream ended without the sentinel line -> truncated download.
            raise VocabSyncError(f'{table}: stream ended without a __done sentinel (truncated)')
    finally:
        # Close the generator (and its underlying streamed response) even when we
        # break early at the sentinel or raise mid-stream.
        close = getattr(gen, 'close', None)
        if close is not None:
            close()

    if batch:
        model.objects.using(_DB).bulk_create(batch)
        count += len(batch)

    # The sentinel is the always-present per-#334 completeness signal; require an
    # integer count (a `{"__done": true}` with no `rows` is malformed, not a pass).
    if not isinstance(sentinel, int) or isinstance(sentinel, bool):
        raise VocabSyncError(f'{table}: __done sentinel is missing an integer rows count')
    if count != sentinel:
        raise VocabSyncError(f'{table}: loaded {count} rows != sentinel {sentinel}')
    if expected_count is not None and count != expected_count:
        raise VocabSyncError(f'{table}: loaded {count} rows != manifest row_count {expected_count}')
    return count


def _do_sync(client, tables, activate):
    # Only a full-table load may be activated — activating a subset would put an
    # incomplete generation live (empty relationship/ancestor tables → traversal
    # silently returns nothing). Subset loads (e.g. `--tables concept`) stage but
    # never activate; the populated-generation gate is the backstop.
    is_full_sync = set(tables) == set(DEFAULT_TABLES)
    may_activate = activate and is_full_sync

    latest = client.get_latest_release(if_none_match=_last_processed_etag())
    if latest.not_modified:
        logger.info('vocab sync: no new release (304)')
        return SyncOutcome(status='unchanged')

    manifest = latest.manifest or {}
    try:
        release_id = int(manifest['id'])
    except (KeyError, TypeError, ValueError) as exc:
        raise VocabSyncError(
            f'vocab-releases manifest has no valid integer id: {manifest!r}') from exc
    row_counts = manifest.get('row_counts') or {}

    existing = MirrorRelease.objects.using(_DB).filter(release_id=release_id).first()
    if existing:
        if existing.state == MirrorRelease.ACTIVE:
            logger.info('vocab sync: release %s already active', release_id)
            return SyncOutcome(status='already_synced', release_id=release_id)
        if existing.state == MirrorRelease.READY:
            # Already loaded + verified. If a prior run's activation was stranded,
            # recover by (re)activating without re-downloading; else leave READY.
            if may_activate:
                activate_release(release_id)
                logger.info('vocab sync: recovered + activated READY release %s', release_id)
                return SyncOutcome(status='activated', release_id=release_id)
            return SyncOutcome(status='already_synced', release_id=release_id)
        # STAGING / FAILED — fall through to restage below.

    # (Re)stage: fresh generation, STAGING.
    _delete_release_rows(release_id)
    rel, _ = MirrorRelease.objects.using(_DB).update_or_create(
        release_id=release_id,
        defaults=dict(
            etag=latest.etag, state=MirrorRelease.STAGING, row_counts=row_counts,
            checksums=manifest.get('checksums') or {}, manifest=manifest,
            loaded_at=None, activated_at=None,
        ),
    )

    counts = {}
    try:
        for table in tables:
            counts[table] = _load_table(client, release_id, table, row_counts.get(table))
    except Exception as exc:
        rel.state = MirrorRelease.FAILED
        rel.save(using=_DB, update_fields=['state', 'updated_at'])
        _delete_release_rows(release_id)
        logger.error('vocab sync: release %s FAILED, rolled back: %s', release_id, exc)
        raise

    rel.state = MirrorRelease.READY
    rel.loaded_at = timezone.now()
    rel.save(using=_DB, update_fields=['state', 'loaded_at', 'updated_at'])
    logger.info('vocab sync: release %s READY (%s)', release_id, counts)

    if may_activate:
        activate_release(release_id)
    elif activate and not is_full_sync:
        logger.warning(
            'vocab sync: subset load %s left release %s READY, not activated '
            '(an incomplete generation must not go live)', tables, release_id)
    return SyncOutcome(status='synced', release_id=release_id, counts=counts)


def sync_vocab_mirror(client=None, tables=None, activate=True):
    """Run one sync under the singleton advisory lock. See module docstring."""
    with connections[_DB].cursor() as cur:
        cur.execute('SELECT pg_try_advisory_lock(%s)', [_SYNC_ADVISORY_LOCK_KEY])
        acquired = cur.fetchone()[0]
        if not acquired:
            logger.info('vocab sync: another sync holds the advisory lock; skipping')
            return SyncOutcome(status='locked')
        try:
            return _do_sync(client or PromopVocabClient(), tables or DEFAULT_TABLES, activate)
        finally:
            cur.execute('SELECT pg_advisory_unlock(%s)', [_SYNC_ADVISORY_LOCK_KEY])
