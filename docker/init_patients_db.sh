#!/usr/bin/env bash
# Initialize the patients database from a gzipped SQL backup when empty.
#
# Behavior:
#   - Skip if PATIENT_DATABASE_URL is not set (single-DB deployments).
#   - Skip if PATIENT_DATABASE_INIT_FROM_BACKUP is not truthy (opt-in only,
#     because this step DROPs the public schema before restoring).
#   - Skip if patient_info already has rows.
#   - Otherwise download PATIENT_DATABASE_BACKUP_URL (default: public GCS bucket)
#     and restore it into PATIENT_DATABASE_URL.
set -euo pipefail

PATIENT_DATABASE_URL="${PATIENT_DATABASE_URL:-}"
PATIENT_DATABASE_BACKUP_URL="${PATIENT_DATABASE_BACKUP_URL:-https://storage.googleapis.com/cancerbot-public/exact/patients/latest.sql.gz}"
PATIENT_DATABASE_INIT_FROM_BACKUP="${PATIENT_DATABASE_INIT_FROM_BACKUP:-}"

log() { echo "[init_patients_db] $*"; }

case "${PATIENT_DATABASE_INIT_FROM_BACKUP,,}" in
    1|true|yes|on) ;;
    *)
        log "PATIENT_DATABASE_INIT_FROM_BACKUP not enabled — skipping."
        log "Set PATIENT_DATABASE_INIT_FROM_BACKUP=1 to auto-restore the patients DB from a snapshot."
        exit 0
        ;;
esac

if [ -z "$PATIENT_DATABASE_URL" ]; then
    log "ERROR: PATIENT_DATABASE_INIT_FROM_BACKUP is enabled but PATIENT_DATABASE_URL is not set."
    exit 1
fi

log "Checking patients database status..."

# Probe in three steps, so that a FAILED probe can never be mistaken for "the
# database is empty". The previous single-shot form
#     row_count="$(psql ... 2>/dev/null || true)"
# collapsed every failure mode — unreachable host, wrong credentials, missing
# SELECT grant, renamed table — into an empty string. That
# empty string failed the numeric test below and fell through to
# `DROP SCHEMA public CASCADE`, destroying a database that may well have been
# fully populated. Only a probe that succeeds AND genuinely reports "no table"
# or "zero rows" may authorise the restore.
if ! probe_err="$(psql "$PATIENT_DATABASE_URL" -tAXc 'SELECT 1' 2>&1 >/dev/null)"; then
    log "ERROR: cannot query PATIENT_DATABASE_URL — refusing to restore."
    log "       A failed probe is not evidence that the database is empty."
    if [ -n "$probe_err" ]; then log "       psql: ${probe_err}"; fi
    exit 1
fi

table_ref="$(psql "$PATIENT_DATABASE_URL" -tAXc "SELECT to_regclass('public.patient_info')")" || {
    log "ERROR: could not probe for patient_info — refusing to restore."
    exit 1
}
table_ref="${table_ref//[[:space:]]/}"

if [ -z "$table_ref" ]; then
    # Absent from public, but visible through search_path elsewhere? Then this
    # database is not the fresh one it looks like, and dropping public would be
    # destroying a schema we never inspected.
    if [ -n "$(psql "$PATIENT_DATABASE_URL" -tAXc "SELECT to_regclass('patient_info')" 2>/dev/null | tr -d '[:space:]')" ]; then
        log "ERROR: patient_info resolves outside schema public — refusing to restore."
        exit 1
    fi
fi

if [ -n "$table_ref" ]; then
    row_count="$(psql "$PATIENT_DATABASE_URL" -tAXc 'SELECT COUNT(*) FROM public.patient_info')" || {
        log "ERROR: patient_info exists but could not be counted (permissions?) — refusing to restore."
        exit 1
    }
    row_count="${row_count//[[:space:]]/}"
    if ! [[ "$row_count" =~ ^[0-9]+$ ]]; then
        log "ERROR: unexpected row-count output '${row_count}' — refusing to restore."
        exit 1
    fi
    if [ "$row_count" -gt 0 ]; then
        log "patient_info already populated (${row_count} rows) — skipping restore."
        exit 0
    fi
    log "patient_info exists and is empty — restoring from backup."
else
    log "patient_info does not exist — restoring from backup."
fi
log "Backup source: $PATIENT_DATABASE_BACKUP_URL"

backup_file="$(mktemp --suffix=.sql.gz)"
trap 'rm -f "$backup_file"' EXIT

python3 - "$PATIENT_DATABASE_BACKUP_URL" "$backup_file" <<'PY'
import shutil
import sys
import urllib.request

url, dest = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:
    shutil.copyfileobj(resp, out, length=1024 * 1024)
PY

log "Backup downloaded ($(du -h "$backup_file" | cut -f1)). Recreating public schema..."
psql "$PATIENT_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'

log "Restoring backup into patients database..."
gunzip -c "$backup_file" | psql "$PATIENT_DATABASE_URL" -v ON_ERROR_STOP=1 -q

log "Done."
