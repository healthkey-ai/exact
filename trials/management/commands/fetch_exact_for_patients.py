"""
Management command: fetch_exact_for_patients

Loads all patients from an external patient DB (PROMOP), runs the EXACT
search API for each one (top-N trials), and caches the full responses to
disk so they can be analysed offline without re-running the slow search.

Cache layout
------------
  {cache_dir}/
    {person_id}.json   — one file per patient; contains normalised row,
                         list-endpoint results, and per-trial detail responses

Usage
-----
    python manage.py fetch_exact_for_patients \\
      --source-db-url $PATIENT_DATABASE_URL \\
      --cache-dir ~/.cache/exact/patients \\
      --limit 10

Cache files contain PHI (patient names + full patient_info), so the default
cache dir is OUTSIDE the repo tree and files are written mode 0600. Writing
the cache inside the repo requires the explicit --allow-in-repo-cache flag.

Options
-------
    --source-db-url   PostgreSQL URL for the patient DB
                      (falls back to PATIENT_DATABASE_URL env var)
    --cache-dir       Directory to write cache files (default: ~/.cache/exact/patients)
    --limit           Top-N trials to fetch per patient (default: 10)
    --person-ids      Comma-separated list of person IDs to restrict to
    --refresh         Re-fetch even if a cache file already exists
    --dry-run         Print the first patient's normalised data and exit
"""
import json
import logging
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# Files written here contain PHI (names, full patient_info). Default to a
# secure location outside the repo working tree so it can never be committed.
DEFAULT_CACHE_DIR = os.path.expanduser('~/.cache/exact/patients')
REPO_ROOT = Path(__file__).resolve().parents[3]


# ── helpers reused from sister command ─────────────────────────────────────

def _get_client():
    """Return an authenticated DRF APIClient (in-process, no server needed)."""
    from rest_framework.test import APIClient
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.filter(is_active=True).order_by('id').first()
    if user is None:
        raise RuntimeError('No active user found in the EXACT DB — cannot authenticate the API client.')
    client = APIClient()
    client.defaults['HTTP_HOST'] = 'localhost'
    client.force_authenticate(user=user)
    return client


def _psql_query_rows(db_url, sql):
    """
    Run a SQL query via psql subprocess and return a list of row dicts.
    Uses psql instead of psycopg2 to avoid a double-free crash on macOS/conda
    when a second libpq connection is opened alongside Django's own connection.
    """
    import subprocess
    wrapped = f"SELECT row_to_json(t) FROM ({sql}) t"
    env = {**os.environ, 'PGSSLMODE': 'require'}
    result = subprocess.run(
        ['psql', db_url, '-t', '--no-psqlrc', '-c', wrapped],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f'psql error: {result.stderr.strip()}')
    rows = []
    skipped = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            skipped += 1
            logger.warning('Skipping non-JSON line from psql: %.80s — %s', line, exc)
    if skipped:
        logger.warning('Skipped %d non-JSON line(s) from psql output.', skipped)
    return rows


def _fetch_patients(db_url, person_ids=None, limit=None):
    """Return all patient dicts from PROMOP via psql subprocess."""
    where = ''
    if person_ids:
        ids_sql = ', '.join(str(int(i)) for i in person_ids)
        where = f'WHERE pi.person_id IN ({ids_sql})'

    limit_clause = f'LIMIT {int(limit)}' if limit else ''

    sql = f'''
        SELECT pi.*,
               p.gender_source_value,
               p.gender_concept_id,
               p.given_name || ' ' || p.family_name AS full_name
        FROM patient_info pi
        JOIN person p ON pi.person_id = p.person_id
        {where}
        ORDER BY pi.person_id
        {limit_clause}
    '''
    return _psql_query_rows(db_url, sql)


def _clean_row(row: dict) -> dict:
    """Normalise + clean a PROMOP row into a JSON-serialisable patient_info dict."""
    from trials.management.commands.search_trials_for_patients import (
        _normalize_promop_row, SKIP_COLUMNS, JSON_FIELDS,
    )

    row = _normalize_promop_row(dict(row))

    cleaned = {}
    for col, val in row.items():
        if col in SKIP_COLUMNS:
            continue
        if col == 'full_name':
            continue
        if val is None:
            continue
        if col in JSON_FIELDS and isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                val = [val] if val else []
        if not val and col in JSON_FIELDS:
            continue
        if isinstance(val, date):
            val = val.isoformat()
        if isinstance(val, Decimal):
            val = float(val)
        cleaned[col] = val

    return cleaned


def _api_search(client, patient_info: dict, limit: int) -> dict:
    """Call the EXACT list endpoint; returns raw response dict."""
    import json as _json
    resp = client.generic(
        'GET',
        f'/trials/?page_size={limit}&recruitmentStatus=RECRUITING',
        data=_json.dumps({'patient_info': patient_info}),
        content_type='application/json',
    )
    if resp.status_code != 200:
        raise RuntimeError(f'List endpoint returned HTTP {resp.status_code}: {resp.content[:200]}')
    return resp.json()


def _api_detail(patient_info: dict, trial_id: int) -> dict:
    """
    Call the EXACT detail endpoint for one trial.
    Creates its own APIClient so this function is safe to call from a thread.
    """
    import json as _json
    from django.db import close_old_connections
    close_old_connections()
    client = _get_client()
    resp = client.generic(
        'GET',
        f'/trials/{trial_id}/?view=all_attributes_in_groups',
        data=_json.dumps({'patient_info': patient_info}),
        content_type='application/json',
    )
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}: {resp.content[:120]}')
    return resp.json()


def _fetch_details_parallel(patient_info: dict, trial_ids: list[int],
                             workers: int = 5) -> dict:
    """
    Fetch detail responses for all trial_ids concurrently.
    Returns {str(trial_id): detail_dict}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_api_detail, patient_info, tid): tid
                   for tid in trial_ids}
        for fut in as_completed(futures):
            tid = futures[fut]
            try:
                results[str(tid)] = fut.result()
            except Exception as exc:
                logger.warning('Detail fetch failed for trial %s: %s', tid, exc)
                results[str(tid)] = {'error': str(exc)}
    return results




# ── management command ──────────────────────────────────────────────────────

class Command(BaseCommand):
    help = 'Fetch EXACT trial results for all PROMOP patients and cache to disk.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-db-url',
            default=os.environ.get('PATIENT_DATABASE_URL', ''),
            help='PostgreSQL URL for the patient DB (falls back to PATIENT_DATABASE_URL)',
        )
        parser.add_argument(
            '--cache-dir',
            default=DEFAULT_CACHE_DIR,
            help=f'Directory to write PHI cache files (default: {DEFAULT_CACHE_DIR})',
        )
        parser.add_argument(
            '--allow-in-repo-cache',
            action='store_true',
            help='Explicitly permit writing the PHI cache inside the repo tree '
                 '(unsafe — files may be committed). Off by default.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Top-N trials to fetch per patient (default: 10)',
        )
        parser.add_argument(
            '--person-ids',
            default='',
            help='Comma-separated person IDs to restrict to',
        )
        parser.add_argument(
            '--patient-limit',
            type=int,
            default=None,
            help='Maximum number of patients to process',
        )
        parser.add_argument(
            '--workers',
            type=int,
            default=5,
            help='Parallel threads for detail fetches per patient (default: 5)',
        )
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='Re-fetch even if a cache file already exists',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the first patient normalised data and exit',
        )

    def handle(self, *args, **options):
        db_url = options['source_db_url']
        if not db_url:
            self.stderr.write(self.style.ERROR(
                'No patient DB URL. Use --source-db-url or set PATIENT_DATABASE_URL.'
            ))
            return

        cache_dir = options['cache_dir']
        cache_path = Path(cache_dir).resolve()
        if REPO_ROOT in cache_path.parents or cache_path == REPO_ROOT:
            if not options['allow_in_repo_cache']:
                raise CommandError(
                    f'Refusing to write PHI cache inside the repo tree ({cache_path}). '
                    f'Use an outside-repo --cache-dir (default: {DEFAULT_CACHE_DIR}) '
                    f'or pass --allow-in-repo-cache to override.'
                )
        os.makedirs(cache_dir, exist_ok=True)
        os.chmod(cache_dir, 0o700)

        person_ids = [int(x) for x in options['person_ids'].split(',') if x.strip()]
        limit = options['limit']
        refresh = options['refresh']

        client = _get_client()

        total = skipped = errors = 0

        rows = _fetch_patients(db_url, person_ids=person_ids or None,
                               limit=options.get('patient_limit'))
        self.stdout.write(f'Found {len(rows)} patients in PROMOP')

        for row in rows:
            person_id = row.get('person_id')
            name = row.get('full_name') or f'person_{person_id}'
            cache_file = os.path.join(cache_dir, f'{person_id}.json')

            if os.path.exists(cache_file) and not refresh:
                self.stdout.write(f'  {name} (#{person_id}) ... cached')
                skipped += 1
                continue

            self.stdout.write(f'  {name} (#{person_id}) ...', ending='')

            try:
                # Close stale DB connections so a dropped connection (e.g. after
                # laptop sleep) is transparently re-established on next query.
                from django.db import close_old_connections
                close_old_connections()

                patient_info = _clean_row(row)

                if options['dry_run']:
                    self.stdout.write('\n')
                    self.stdout.write(json.dumps(patient_info, indent=2, default=str))
                    return

                # Step 1 — top-N list (matchingType, attributesToFillIn, scores)
                search_response = _api_search(client, patient_info, limit)
                trial_ids = [t['trialId'] for t in search_response.get('results', [])]

                # Step 2 — full details for each trial, fetched in parallel
                # (each thread creates its own APIClient — thread-safe)
                details = _fetch_details_parallel(patient_info, trial_ids,
                                                  workers=options['workers'])

                cache_data = {
                    'person_id': person_id,
                    'name': name,
                    'patient_info': patient_info,
                    'search': search_response,
                    'details': details,
                }
                trial_count = len(trial_ids)
                # O_NOFOLLOW: refuse to follow a planted symlink at the cache
                # path (would otherwise write PHI / chmod the link target).
                fd = os.open(cache_file,
                             os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump(cache_data, f, indent=2, default=str)
                # O_CREAT's mode is ignored when the file already exists (e.g. a
                # 0644 file from a prior run on --refresh), so enforce 0600 here.
                os.chmod(cache_file, 0o600)

                self.stdout.write(self.style.SUCCESS(
                    f' OK ({trial_count} trials)'
                ))
                total += 1

            except Exception as exc:
                logger.exception('Failed for %s', name)
                self.stdout.write(self.style.ERROR(f' ERROR: {exc}'))
                errors += 1

        self.stdout.write(
            f'\nDone — fetched: {total}, skipped (cached): {skipped}, errors: {errors}'
        )
        self.stdout.write(f'Cache: {os.path.abspath(cache_dir)}/')
