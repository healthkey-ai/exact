#!/usr/bin/env bash
# Initialize the trials database from a gzipped SQL backup when empty.
#
# Behavior:
#   - Skip if TRIALS_DATABASE_URL is not set (single-DB deployments).
#   - Skip if TRIALS_DATABASE_INIT_FROM_BACKUP is not truthy (opt-in only,
#     because this step DROPs the public schema before restoring).
#   - Skip if trials_trial already has rows.
#   - Otherwise download TRIALS_DATABASE_BACKUP_URL (default: public GCS bucket)
#     and restore it into TRIALS_DATABASE_URL.
set -euo pipefail

TRIALS_DATABASE_URL="${TRIALS_DATABASE_URL:-}"
TRIALS_DATABASE_BACKUP_URL="${TRIALS_DATABASE_BACKUP_URL:-https://storage.googleapis.com/cancerbot-public/exact/trials/latest.sql.gz}"
TRIALS_DATABASE_INIT_FROM_BACKUP="${TRIALS_DATABASE_INIT_FROM_BACKUP:-}"

log() { echo "[init_trials_db] $*"; }

if [ -z "$TRIALS_DATABASE_URL" ]; then
    log "TRIALS_DATABASE_URL not set — skipping."
    exit 0
fi

case "${TRIALS_DATABASE_INIT_FROM_BACKUP,,}" in
    1|true|yes|on) ;;
    *)
        log "TRIALS_DATABASE_INIT_FROM_BACKUP not enabled — skipping."
        log "Set TRIALS_DATABASE_INIT_FROM_BACKUP=1 to auto-restore the trials DB from a snapshot."
        exit 0
        ;;
esac

log "Checking trials database status..."

# Probe in three steps, so that a FAILED probe can never be mistaken for "the
# database is empty". The previous single-shot form
#     row_count="$(psql ... 2>/dev/null || true)"
# collapsed every failure mode — unreachable host, wrong credentials, missing
# SELECT grant, renamed table — into an empty string. That
# empty string failed the numeric test below and fell through to
# `DROP SCHEMA public CASCADE`, destroying a database that may well have been
# fully populated. Only a probe that succeeds AND genuinely reports "no table"
# or "zero rows" may authorise the restore.
if ! probe_err="$(psql "$TRIALS_DATABASE_URL" -tAXc 'SELECT 1' 2>&1 >/dev/null)"; then
    log "ERROR: cannot query TRIALS_DATABASE_URL — refusing to restore."
    log "       A failed probe is not evidence that the database is empty."
    if [ -n "$probe_err" ]; then log "       psql: ${probe_err}"; fi
    exit 1
fi

table_ref="$(psql "$TRIALS_DATABASE_URL" -tAXc "SELECT to_regclass('public.trials_trial')")" || {
    log "ERROR: could not probe for trials_trial — refusing to restore."
    exit 1
}
table_ref="${table_ref//[[:space:]]/}"

if [ -z "$table_ref" ]; then
    # Absent from public, but visible through search_path elsewhere? Then this
    # database is not the fresh one it looks like, and dropping public would be
    # destroying a schema we never inspected.
    if [ -n "$(psql "$TRIALS_DATABASE_URL" -tAXc "SELECT to_regclass('trials_trial')" 2>/dev/null | tr -d '[:space:]')" ]; then
        log "ERROR: trials_trial resolves outside schema public — refusing to restore."
        exit 1
    fi
fi

if [ -n "$table_ref" ]; then
    row_count="$(psql "$TRIALS_DATABASE_URL" -tAXc 'SELECT COUNT(*) FROM public.trials_trial')" || {
        log "ERROR: trials_trial exists but could not be counted (permissions?) — refusing to restore."
        exit 1
    }
    row_count="${row_count//[[:space:]]/}"
    if ! [[ "$row_count" =~ ^[0-9]+$ ]]; then
        log "ERROR: unexpected row-count output '${row_count}' — refusing to restore."
        exit 1
    fi
    if [ "$row_count" -gt 0 ]; then
        log "trials_trial already populated (${row_count} rows) — skipping restore."
        exit 0
    fi
    log "trials_trial exists and is empty — restoring from backup."
else
    log "trials_trial does not exist — restoring from backup."
fi
log "Backup source: $TRIALS_DATABASE_BACKUP_URL"

backup_file="$(mktemp --suffix=.sql.gz)"
trap 'rm -f "$backup_file"' EXIT

python3 - "$TRIALS_DATABASE_BACKUP_URL" "$backup_file" <<'PY'
import shutil
import sys
import urllib.request

url, dest = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
    shutil.copyfileobj(resp, out, length=1024 * 1024)
PY

log "Backup downloaded ($(du -h "$backup_file" | cut -f1)). Recreating public schema..."
psql "$TRIALS_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'

log "Restoring backup into trials database..."
gunzip -c "$backup_file" | psql "$TRIALS_DATABASE_URL" -v ON_ERROR_STOP=1 -q

log "Done."
