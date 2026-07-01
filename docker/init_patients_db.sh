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
row_count="$(psql "$PATIENT_DATABASE_URL" -tAXc \
    'SELECT COUNT(*) FROM patient_info' 2>/dev/null || true)"
row_count="${row_count//[[:space:]]/}"

if [[ "$row_count" =~ ^[0-9]+$ ]] && [ "$row_count" -gt 0 ]; then
    log "patient_info already populated (${row_count} rows) — skipping restore."
    exit 0
fi

log "patient_info is empty or missing — restoring from backup."
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
