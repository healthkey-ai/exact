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
``row_counts`` cross-check. Each table streams into ``COPY … FROM STDIN`` (#256),
so ``concept_relationship`` (~18M rows in prod RxNorm) loads without materializing
millions of ORM objects. Indexes stay live during the load — dropping the shared
release_id-leading indexes would degrade the concurrently-ACTIVE generation's reads.

Remaining follow-up (blocked, not a blocker): **byte-level checksum verification**
of the manifest ``checksums``. It would catch *silent corruption* (same count,
altered bytes) beyond the sentinel + row-count gate, but promop's ``checksums`` are
currently ``{count, min_ctid, max_ctid}`` — physical ctids are meaningless to a
consumer (EXACT's copy has different ctids), so only ``count`` is verifiable (and
already is). Real verification needs promop to emit a content hash over a pinned
per-table row canonicalization; tracked cross-repo.
"""
import io
import logging
from dataclasses import dataclass, field
from datetime import date

from django.db import connections
from django.utils import timezone

from vocab_mirror.activation import ReleaseMatchFailed, activate_release
from vocab_mirror.models import (
    MirrorConcept,
    MirrorConceptAncestor,
    MirrorConceptRelationship,
    MirrorRelease,
    MirrorVocabulary,
)
from vocab_mirror.promop_vocab_client import (
    PromopVocabClient,
    VocabReleaseSuperseded,
    VocabSyncError,
)

logger = logging.getLogger(__name__)

_DB = 'default'  # the mirror lives on `default` (db_router / ADR §Placement)
# Fixed 64-bit key for the sync singleton advisory lock.
_SYNC_ADVISORY_LOCK_KEY = 8234502250
# How many times one run re-resolves /latest after a mid-sync supersede (409)
# before deferring to the next scheduled run (promop#371/#373).
_SUPERSEDE_MAX_RETRIES = 3

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
    # 'unchanged' (304) | 'already_synced' (release already ACTIVE) |
    # 'synced' (loaded + activated) | 'activated' (recovered a stranded READY) |
    # 'partial' (subset load, left STAGING, not activated) |
    # 'loaded_not_activated' (READY but the release-match gate rejected it) |
    # 'superseded' (releases kept publishing mid-sync; deferred to the next run) |
    # 'locked' (another sync holds the advisory lock)
    status: str
    release_id: int | None = None
    counts: dict = field(default_factory=dict)


def _copy_columns(model):
    """COPY target columns (``release_id`` first, then the model's own columns
    minus the surrogate ``id``) plus a ``(row_key, is_date)`` list for projecting
    each snapshot row's values in that same order. ``field.name == field.column``
    for the mirror models (no ``db_column`` overrides), and the snapshot keys are
    the OMOP column names = the field names, so one name serves both.
    """
    columns = ['release_id']
    fields = []
    for f in model._meta.concrete_fields:
        if f.name in ('id', 'release_id'):
            continue
        columns.append(f.column)
        fields.append((f.name, f.get_internal_type() == 'DateField'))
    return columns, fields


def _copy_escape(v):
    """Escape one value for the COPY TEXT format.

    TEXT (not CSV) so ``None`` → ``\\N`` (SQL NULL) stays distinct from an empty
    string → empty field; backslash / tab / newline / CR are escaped.
    """
    if v is None:
        return r'\N'
    if v is True:
        return 't'
    if v is False:
        return 'f'
    s = v if isinstance(v, str) else str(v)
    return (s.replace('\\', '\\\\').replace('\t', '\\t')
            .replace('\n', '\\n').replace('\r', '\\r'))


class _CopyStream(io.RawIOBase):
    """Readable stream that pulls encoded COPY lines from a row iterator on demand,
    so an ~18M-row snapshot streams into ``COPY … FROM STDIN`` without ever
    materializing in memory (#256)."""

    def __init__(self, line_iter):
        self._iter = line_iter
        self._buf = b''

    def readable(self):
        return True

    def readinto(self, b):
        while not self._buf:
            try:
                self._buf = next(self._iter)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


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


def _prepare_cross_artifacts(release_id):
    """Rebuild + stamp the release-gated derived artifacts for ``release_id`` so the
    release-match gate (``activation.py``) can verify them at activation.

    Runs under the sync advisory lock (single writer). v1: the component→category
    lookup (#262) — rebuilt from the local M2M and stamped for this release in one
    atomic txn. Imported lazily to keep ``vocab_mirror`` free of a load-time
    dependency on ``trials.services``. Any failure propagates and fails the sync
    (the release is not activated), same as a load failure.
    """
    from trials.services.omop.component_category_lookup import sync_component_category_lookup
    result = sync_component_category_lookup(release_id=release_id)
    logger.info(
        'vocab sync: component-category lookup rebuilt + stamped for release %s (%s)',
        release_id,
        {k: result[k] for k in ('total', 'added', 'updated', 'removed')})


def _load_table(client, release_id, table, expected_count):
    """Stream one table's snapshot into the (fresh) staging generation via
    ``COPY … FROM STDIN`` (#256).

    COPY replaces per-batch ORM ``bulk_create`` — for ``concept_relationship``
    (~18M rows in prod RxNorm) it avoids materializing millions of model objects
    and streams straight into Postgres, a large speedup on the periodic sync.
    Indexes are deliberately kept live (dropping the shared release_id-leading
    indexes around a load would degrade the concurrently-ACTIVE generation's
    reads). Returns the loaded row count; raises ``VocabSyncError`` on a truncated
    stream (no sentinel) or a count that disagrees with the sentinel / manifest.
    """
    model = TABLE_MODELS[table]
    columns, fields = _copy_columns(model)
    model.objects.using(_DB).filter(release_id=release_id).delete()  # idempotent restage

    state = {'count': 0, 'sentinel': None, 'saw_done': False}
    gen = client.stream_snapshot(release_id, table)
    rid = _copy_escape(release_id)

    def _lines():
        for row in gen:
            if row.get('__done'):
                state['sentinel'] = row.get('rows')
                state['saw_done'] = True
                return  # stop before the sentinel; COPY sees EOF here
            parts = [rid]
            for name, is_date in fields:
                v = row.get(name)
                if is_date:
                    v = date.fromisoformat(v) if isinstance(v, str) and v else None
                parts.append(_copy_escape(v))
            state['count'] += 1
            yield ('\t'.join(parts) + '\n').encode('utf-8')

    col_sql = ', '.join(f'"{c}"' for c in columns)
    sql = f'COPY "{model._meta.db_table}" ({col_sql}) FROM STDIN'
    conn = connections[_DB]
    conn.ensure_connection()
    try:
        # copy_expert is psycopg2-specific (the pinned driver); a psycopg3 move
        # would use `cursor.copy(...)` instead.
        with conn.connection.cursor() as cur:
            cur.copy_expert(sql, _CopyStream(_lines()))
    except Exception as exc:
        # A COPY parse/DB error (malformed row, constraint) — surface as the sync's
        # own error type so _do_sync fails the release closed (rolls back its rows).
        if isinstance(exc, VocabSyncError):
            raise
        raise VocabSyncError(f'{table}: COPY failed: {exc}') from exc
    finally:
        # Close the generator (and its underlying streamed response) even when we
        # stop early at the sentinel or raise mid-stream.
        close = getattr(gen, 'close', None)
        if close is not None:
            close()

    if not state['saw_done']:
        # Stream ended without the sentinel line -> truncated download.
        raise VocabSyncError(f'{table}: stream ended without a __done sentinel (truncated)')

    sentinel, count = state['sentinel'], state['count']
    # The sentinel is the always-present per-#334 completeness signal; require an
    # integer count (a `{"__done": true}` with no `rows` is malformed, not a pass).
    if not isinstance(sentinel, int) or isinstance(sentinel, bool):
        raise VocabSyncError(f'{table}: __done sentinel is missing an integer rows count')
    if count != sentinel:
        raise VocabSyncError(f'{table}: loaded {count} rows != sentinel {sentinel}')
    if expected_count is not None and count != expected_count:
        raise VocabSyncError(f'{table}: loaded {count} rows != manifest row_count {expected_count}')
    return count


def _reap_best_effort():
    """Prune old generations after a successful activation — best-effort, non-fatal.

    Runs inside the sync advisory lock, so it calls ``reap_old_generations``
    directly (not the self-locking wrapper). A reaper failure must never fail the
    sync, so it is logged and swallowed. Lazy import keeps ``sync`` free of a
    load-time reaper dependency (#259).
    """
    try:
        from vocab_mirror.reaper import reap_old_generations
        reaped = reap_old_generations()
        if reaped:
            logger.info('vocab sync: reaped %d old generation(s): %s',
                        len(reaped), [r['release_id'] for r in reaped])
    except Exception as exc:
        logger.warning('vocab sync: reaper failed (non-fatal): %s', exc)


def _do_sync(client, tables, activate):
    """Run one sync, restarting if the release is superseded mid-stream.

    promop's snapshots are latest-only (promop#371/#373): a newer release
    published between our ``/latest`` poll and a snapshot stream surfaces as a 409
    (``VocabReleaseSuperseded``). That is a re-resolve signal, not a failure — poll
    ``/latest`` again and load the new release, bounded so rapid publishing can't
    spin forever (the next scheduled run picks up where we left off).
    """
    for attempt in range(_SUPERSEDE_MAX_RETRIES):
        try:
            return _do_sync_once(client, tables, activate)
        except VocabReleaseSuperseded as exc:
            logger.info('vocab sync: %s; re-resolving /latest (attempt %d/%d)',
                        exc, attempt + 1, _SUPERSEDE_MAX_RETRIES)
    logger.warning(
        'vocab sync: releases kept superseding after %d attempts; leaving it for '
        'the next run', _SUPERSEDE_MAX_RETRIES)
    return SyncOutcome(status='superseded')


def _do_sync_once(client, tables, activate):
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
                _prepare_cross_artifacts(release_id)
                try:
                    activate_release(release_id)
                except ReleaseMatchFailed as exc:
                    # e.g. a required table is legitimately empty in this release.
                    # Don't crash-loop the job every run — log and exit non-fatal;
                    # the old ACTIVE generation keeps serving (or readers 503).
                    logger.error('vocab sync: READY release %s not activatable: %s',
                                 release_id, exc)
                    return SyncOutcome(status='loaded_not_activated', release_id=release_id)
                logger.info('vocab sync: recovered + activated READY release %s', release_id)
                _reap_best_effort()
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
    except VocabReleaseSuperseded as exc:
        # A newer release published mid-sync (promop snapshots are latest-only).
        # This release is stale, not corrupt — discard its staging (rows + the
        # MirrorRelease row) and let the caller re-resolve /latest and restart.
        _delete_release_rows(release_id)
        rel.delete(using=_DB)
        logger.info('vocab sync: release %s superseded mid-sync, discarded: %s',
                    release_id, exc)
        raise
    except Exception as exc:
        rel.state = MirrorRelease.FAILED
        rel.save(using=_DB, update_fields=['state', 'updated_at'])
        _delete_release_rows(release_id)
        logger.error('vocab sync: release %s FAILED, rolled back: %s', release_id, exc)
        raise

    if not is_full_sync:
        # A subset load is an incomplete generation — leave it STAGING (never
        # READY), so a later full sync RESTAGES it rather than finding a READY
        # release it can neither activate (the gate needs all tables) nor
        # restage. READY means "complete + activatable".
        logger.warning(
            'vocab sync: subset load %s left release %s STAGING (partial; a full '
            'sync will restage it)', tables, release_id)
        return SyncOutcome(status='partial', release_id=release_id, counts=counts)

    rel.state = MirrorRelease.READY
    rel.loaded_at = timezone.now()
    rel.save(using=_DB, update_fields=['state', 'loaded_at', 'updated_at'])
    logger.info('vocab sync: release %s READY (%s)', release_id, counts)

    if may_activate:
        _prepare_cross_artifacts(release_id)
        try:
            activate_release(release_id)
        except ReleaseMatchFailed as exc:
            logger.error('vocab sync: release %s loaded but not activatable: %s',
                         release_id, exc)
            return SyncOutcome(status='loaded_not_activated', release_id=release_id,
                               counts=counts)
        _reap_best_effort()
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
