"""
Management command: compare_trials

Reads an input JSON file with patient names and their reference top-5 trial
IDs, looks each patient up in the external patient database, runs EXACT
matching (direct ORM, no web server), and compares the top-5 results.

The patient DB is queried via psql subprocess to avoid a double-free crash
that occurs on macOS/conda when a second psycopg2 connection is opened while
Django's own psycopg2 connection is already active.

Outputs
-------
  {output}.json   Full per-patient comparison with overlap analysis
  {output}.txt    One line per patient: name<TAB>exact_id1, exact_id2, ...

Usage
-----
    python manage.py compare_trials \\
      --input scripts/compare_input.json \\
      --output /tmp/compare_results \\
      --source-db-url postgresql://user:pass@host:5432/patients

Falls back to PATIENT_DATABASE_URL env var if --source-db-url is not given.
"""
import json
import logging
import os
import shutil
import subprocess

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_ANSI_RE = None


class _TeeWriter:
    """Wraps Django's OutputWrapper and mirrors plain-text output to a file."""

    def __init__(self, wrapped, log_file):
        self._wrapped = wrapped
        self._log_file = log_file

    def write(self, msg='', style_func=None, ending=None):
        self._wrapped.write(msg, style_func=style_func, ending=ending)
        global _ANSI_RE
        if _ANSI_RE is None:
            import re
            _ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
        clean = _ANSI_RE.sub('', str(msg))
        end = ending if ending is not None else '\n'
        self._log_file.write(clean + end)
        self._log_file.flush()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


# ------------------------------------------------------------------
# Patient DB helpers — all queries run via psql subprocess
# ------------------------------------------------------------------

def _psql_query_rows(db_url, sql):
    """
    Run a SQL query via psql and return a list of row dicts.
    Uses row_to_json so every row comes back as a JSON object.
    PGSSLMODE=require is set explicitly so the subprocess doesn't inherit
    a disabled SSL mode from the parent Django/conda environment.
    """
    wrapped = f"SELECT row_to_json(t) FROM ({sql}) t"
    env = {**os.environ, 'PGSSLMODE': 'require'}
    result = subprocess.run(
        ['psql', db_url, '-t', '--no-psqlrc', '-c', wrapped],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f'psql error: {result.stderr.strip()}')
    rows = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _psql_query_one(db_url, sql):
    rows = _psql_query_rows(db_url, sql)
    return rows[0] if rows else None


def _q(value):
    """Escape a string value for safe inline SQL substitution."""
    return value.replace("'", "''")


def _find_lookup_strategy(db_url, verbosity=1):
    """
    Introspect the patient DB to find the best column(s) for patient lookup.
    Returns a strategy dict, or None if nothing usable is found.
    """
    rows = _psql_query_rows(db_url, """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('patient_info', 'person')
          AND (column_name ILIKE '%name%' OR column_name = 'email' OR column_name = 'postal_code')
        ORDER BY table_name, column_name
    """)

    if verbosity >= 2:
        print(f'Discovered columns: {rows}')

    col_map = {}
    for row in rows:
        col_map.setdefault(row['table_name'], set()).add(row['column_name'])

    pi_cols = col_map.get('patient_info', set())
    p_cols = col_map.get('person', set())

    # Remember whether the DB has an email column so _lookup_patient can try it
    # as a per-patient fallback even when it's mostly NULL.
    has_email_col = 'email' in pi_cols
    has_postal_col = 'postal_code' in pi_cols

    if 'email' in pi_cols:
        # Only use email as the *primary* strategy if the column is actually populated.
        email_count = _psql_query_one(db_url,
            "SELECT COUNT(*) AS n FROM patient_info WHERE email IS NOT NULL AND email != ''")
        if email_count and (email_count.get('n') or 0) > 0:
            return {
                'description': 'patient_info.email + person full name',
                'key': 'email',
                'has_email_col': True,
                'has_postal_col': has_postal_col,
                'sql': lambda val, name=None, postal=None: f"""
                    SELECT pi.*
                    FROM patient_info pi
                    JOIN person p ON pi.person_id = p.person_id
                    WHERE pi.email = '{_q(val)}'
                    {"AND p.given_name || ' ' || p.family_name = '" + _q(name) + "'" if name else ''}
                    ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
                """,
            }

    if 'patient_name' in pi_cols:
        return {
            'description': 'patient_info.patient_name',
            'key': 'name',
            'has_email_col': has_email_col,
            'has_postal_col': has_postal_col,
            'sql': lambda val, name=None, postal=None: f"""
                SELECT pi.*
                FROM patient_info pi
                WHERE pi.patient_name = '{_q(val)}'
                {"AND pi.postal_code = '" + _q(postal) + "'" if postal else ''}
                ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
            """,
        }

    if 'given_name' in p_cols and 'family_name' in p_cols:
        return {
            'description': 'person.given_name || space || person.family_name',
            'key': 'name',
            'has_email_col': has_email_col,
            'has_postal_col': has_postal_col,
            'sql': lambda val, name=None, postal=None: f"""
                SELECT pi.*
                FROM patient_info pi
                JOIN person p ON pi.person_id = p.person_id
                WHERE p.given_name || ' ' || p.family_name = '{_q(val)}'
                {"AND pi.postal_code = '" + _q(postal) + "'" if postal else ''}
                ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
            """,
        }

    if 'first_name' in p_cols and 'last_name' in p_cols:
        return {
            'description': 'person.first_name || space || person.last_name',
            'key': 'name',
            'has_email_col': has_email_col,
            'has_postal_col': has_postal_col,
            'sql': lambda val, name=None, postal=None: f"""
                SELECT pi.*
                FROM patient_info pi
                JOIN person p ON pi.person_id = p.person_id
                WHERE p.first_name || ' ' || p.last_name = '{_q(val)}'
                {"AND pi.postal_code = '" + _q(postal) + "'" if postal else ''}
                ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
            """,
        }

    if 'name' in p_cols:
        return {
            'description': 'person.name',
            'key': 'name',
            'has_email_col': has_email_col,
            'has_postal_col': has_postal_col,
            'sql': lambda val, name=None, postal=None: f"""
                SELECT pi.*
                FROM patient_info pi
                JOIN person p ON pi.person_id = p.person_id
                WHERE p.name = '{_q(val)}'
                {"AND pi.postal_code = '" + _q(postal) + "'" if postal else ''}
                ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
            """,
        }

    print(f'Discovered columns: {rows}')
    print('None matched a known strategy (patient_name, given_name+family_name, ...).')
    return None


def _lookup_patient(db_url, patient, strategy):
    """Return (person_id, row_dict) for the given patient dict, or (None, None).

    Lookup priority:
    0. person_id — if the patient dict has a 'person_id', query directly by that.
    1. Email — if the patient dict has an 'email' and the DB has a pi.email column,
       try an exact email match first (disambiguates duplicate names).
    2. Primary strategy (name / postal_code) — use the strategy's key with an
       optional postal_code filter derived from the patient's 'zipcode' field.
    3. Name-only fallback — retry without postal_code if the filtered query
       returned nothing (guards against zipcode mismatches in the DB).
    """
    # 0. Direct person_id lookup — highest priority, bypasses all ambiguity.
    explicit_pid = patient.get('person_id')
    if explicit_pid:
        row = _psql_query_one(db_url, f"""
            SELECT pi.*
            FROM patient_info pi
            WHERE pi.person_id = {int(explicit_pid)}
            ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
        """)
        if row is not None:
            return row.get('person_id'), row

    # 1. Try email first if available on both sides.
    email = patient.get('email')
    if email and strategy.get('has_email_col'):
        row = _psql_query_one(db_url, f"""
            SELECT pi.*
            FROM patient_info pi
            JOIN person p ON pi.person_id = p.person_id
            WHERE pi.email = '{_q(email)}'
            ORDER BY pi.updated_at DESC NULLS LAST LIMIT 1
        """)
        if row is not None:
            return row.get('person_id'), row

    # 2. Primary strategy lookup, with postal_code filter when available.
    lookup_value = patient.get(strategy['key'])
    if not lookup_value:
        return None, None

    postal = patient.get('zipcode') if strategy.get('has_postal_col') else None
    row = _psql_query_one(db_url, strategy['sql'](lookup_value, patient.get('name'), postal))

    # 3. If postal filter produced no match, retry without it (postal may differ).
    if row is None and postal:
        row = _psql_query_one(db_url, strategy['sql'](lookup_value, patient.get('name'), None))

    if row is None:
        return None, None
    return row.get('person_id'), row


# ------------------------------------------------------------------
# Management command
# ------------------------------------------------------------------

REFERENCE_BASE = 'https://app.cancerbot.org'


def _study_preferences_from_cb(si):
    """Map a reference study_info dict to a StudyPreferences instance."""
    from trials.services.study_preferences import StudyPreferences

    def _s(key):
        v = si.get(key)
        return v if v else None

    def _f(key):
        v = si.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return StudyPreferences(
        search_title=_s('searchTitle'),
        search_disease=_s('searchDisease'),
        search_treatment=_s('searchTreatment'),
        sponsor=_s('sponsor'),
        register=_s('register'),
        study_id=_s('studyId'),
        trial_type=_s('trialType'),
        study_type=_s('studyType'),
        recruitment_status=_s('recruitmentStatus'),
        country=_s('country'),
        region=_s('region'),
        # postalCode intentionally omitted — geo_point is set from the input file's zipcode
        distance=_f('distance'),
        distance_units=si.get('distanceUnits') or 'miles',
        validated_only=bool(si.get('validatedOnly')),
        phase=_s('phase'),
        last_update=_s('lastUpdate'),
        first_enrolment=_s('firstEnrolment'),
    )


def _cb_fetch_trial(token, trial_id):
    """Fetch a single trial from the reference system with patient context (scores, distance, match type)."""
    import requests
    try:
        resp = requests.get(
            f'{REFERENCE_BASE}/api/v1/trials/{trial_id}/',
            headers={'Authorization': f'Token {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        t = resp.json()
        return {
            'score': t.get('goodnessScore'),
            'burden': t.get('patientBurdenScore'),
            'distance': t.get('distance'),
            'distanceUnits': t.get('distanceUnits'),
            'matchingType': t.get('matchingType'),
            'distancePenalty': t.get('distancePenalty'),  # 0-20 scale; used for score breakdown
        }
    except Exception:
        return None


def _cb_fetch_wider_ranking(token, target_ids=None, page_size=50):
    """
    Fetch CB's ranked trial list for this patient, paginating until all
    target_ids are found or results are exhausted.
    Returns {trial_id: {'rank': int, 'matchingType': str}}.
    """
    import requests
    found = {}
    remaining = set(target_ids) if target_ids else set()
    page = 1
    offset = 0
    while True:
        try:
            resp = requests.get(
                f'{REFERENCE_BASE}/api/v1/trials/search/',
                headers={'Authorization': f'Token {token}'},
                params={'page_size': page_size, 'page': page, 'type': 'eligible_and_potential'},
                timeout=15,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get('results', [])
            if not results:
                break
            for i, t in enumerate(results):
                tid = t.get('trialId')
                if tid is not None:
                    found[tid] = {'rank': offset + i + 1, 'matchingType': t.get('matchingType')}
                    remaining.discard(tid)
            if not remaining or not data.get('next'):
                break
            page += 1
            offset += len(results)
        except Exception:
            break
    return found


def _cb_fetch_user_weights(token):
    """
    Fetch the patient's goodness-score weights from CB's user-settings endpoint.
    Returns a dict with keys benefit_weight, patient_burden_weight, risk_weight,
    distance_penalty_weight (all floats, defaulting to 25.0 if missing).
    """
    import requests
    defaults = {
        'benefit_weight': 25.0,
        'patient_burden_weight': 25.0,
        'risk_weight': 25.0,
        'distance_penalty_weight': 25.0,
    }
    try:
        resp = requests.get(
            f'{REFERENCE_BASE}/api/v1/users/my-details/',
            headers={'Authorization': f'Token {token}'},
            timeout=10,
        )
        if resp.status_code != 200:
            return defaults
        settings = resp.json().get('settings') or {}
        return {
            'benefit_weight': float(settings.get('benefitWeight') or 25.0),
            'patient_burden_weight': float(settings.get('patientBurdenWeight') or 25.0),
            'risk_weight': float(settings.get('riskWeight') or 25.0),
            'distance_penalty_weight': float(settings.get('distancePenaltyWeight') or 25.0),
        }
    except Exception:
        return defaults


def _cb_fetch_trial_explain(token, trial_id):
    """
    Call CB's trial detail endpoint and extract per-attribute match status.
    Returns a dict {title: match_status} for all attributes CB evaluated.

    CB's matchingType=potential is determined by attributesToFillIn (top-level list of
    attribute names the patient hasn't filled in).  trialEligibilityAttributes carries
    per-attribute matchingType but only for attributes that were actually evaluated — it
    does NOT surface the reason for potential status directly.  We combine both:
      1. trialEligibilityAttributes — for not_matched / not_eligible entries
      2. attributesToFillIn — for "patient hasn't filled this in" (potential reason)
    """
    import requests
    try:
        resp = requests.get(
            f'{REFERENCE_BASE}/api/v1/trials/{trial_id}/',
            headers={'Authorization': f'Token {token}'},
            timeout=15,
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        attrs = {}
        # 1. trialEligibilityAttributes — not_matched / not_eligible entries
        details = data.get('details') or {}
        items = details.get('trialEligibilityAttributes') or []
        for item in items:
            title = item.get('label') or item.get('name') or '?'
            status = item.get('matchingType')
            if status and status != 'matched':
                attrs[title] = status
        # 2. attributesToFillIn — attributes CB is waiting on (reason for potential)
        for attr in (data.get('attributesToFillIn') or []):
            title = (attr.get('userAttributeTitle')
                     or attr.get('trialAttributeName')
                     or attr.get('label') or attr.get('name') or str(attr))
            if title not in attrs:
                attrs[title] = 'unknown (not filled in CB)'
        return attrs
    except Exception:
        return {}


class Command(BaseCommand):
    help = 'Compare EXACT top-5 trial results against reference top-5 for a list of patients.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--input',
            type=str,
            default='scripts/compare_input.json',
            help='Input JSON file (default: scripts/compare_input.json)',
        )
        parser.add_argument(
            '--output',
            type=str,
            default='/tmp/compare_results',
            help='Output file prefix — writes {prefix}.json and {prefix}.txt '
                 '(default: /tmp/compare_results)',
        )
        parser.add_argument(
            '--source-db-url',
            type=str,
            default=os.environ.get('PATIENT_DATABASE_URL', ''),
            help='Patient database URL (falls back to PATIENT_DATABASE_URL env var)',
        )
        parser.add_argument(
            '--top-n',
            type=int,
            default=5,
            help='Number of top trials to compare (default: 5)',
        )
        parser.add_argument(
            '--explain',
            action='store_true',
            default=False,
            help='For rows where type(E) != type(CB), print per-attribute breakdown '
                 'from both EXACT (TrialMatchExplainer) and CB (detail API).',
        )

    def handle(self, *args, **options):
        log_path = f'{options["output"]}.log'
        self._log_fh = open(log_path, 'w')
        self.stdout = _TeeWriter(self.stdout, self._log_fh)

        if not shutil.which('psql'):
            self.stderr.write(self.style.ERROR('psql not found in PATH — required for patient DB queries'))
            return

        input_path = options['input']
        try:
            with open(input_path) as f:
                patients = json.load(f)
        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f'Input file not found: {input_path}'))
            return

        source_db_url = options['source_db_url']
        if not source_db_url:
            self.stderr.write(self.style.ERROR(
                'No patient DB URL. Use --source-db-url or set PATIENT_DATABASE_URL.'
            ))
            return

        strategy = _find_lookup_strategy(source_db_url, verbosity=options['verbosity'])
        if strategy is None:
            self.stderr.write(self.style.ERROR(
                'Could not find lookup columns. Re-run with --verbosity 2 to see discovered columns.'
            ))
            return
        self.stdout.write(f'Patient lookup: {strategy["description"]}')

        # Load reference patient data if available
        cb_data_path = os.path.join(os.path.dirname(input_path), 'reference_patients_data.json')
        cb_scores_by_name = {}      # name -> {trial_id: {'score', 'rank', 'burden', 'distance', 'distanceUnits'}}
        cb_geo_by_name = {}         # name -> {'country': x, 'postalCode': y}
        cb_token_by_name = {}       # name -> auth token
        cb_study_info_by_name = {}  # name -> raw study_info dict from CB API
        if os.path.exists(cb_data_path):
            with open(cb_data_path) as f:
                for entry in json.load(f):
                    if entry.get('token'):
                        cb_token_by_name[entry['name']] = entry['token']
                    if entry.get('study_info'):
                        cb_study_info_by_name[entry['name']] = entry['study_info']
                    cb_scores_by_name[entry['name']] = {
                        t['id']: {
                            'score': t.get('goodnessScore'),
                            'rank': i + 1,
                            'burden': t.get('patientBurdenScore'),
                            'distance': t.get('distance'),
                            'distanceUnits': t.get('distanceUnits'),
                            'matchingType': t.get('matchingType'),
                            'attributesToFillIn': t.get('attributesToFillIn') or [],
                        }
                        for i, t in enumerate(entry.get('top_trials', []))
                        if t.get('id') is not None
                    }
                    pi = entry.get('patient_info') or {}
                    cb_geo_by_name[entry['name']] = {
                        'country': pi.get('country'),
                        'postalCode': pi.get('postalCode'),
                    }

        top_n = options['top_n']

        from trials.models import Trial
        all_ref_ids = {i for p in patients for i in p.get('cancerbot_trial_ids', [])}
        existing_ids = set(Trial.objects.filter(id__in=all_ref_ids).values_list('id', flat=True))
        missing_ids = all_ref_ids - existing_ids
        if missing_ids:
            self.stdout.write(self.style.WARNING(
                f'Ignoring {len(missing_ids)} reference trial IDs not found in trials DB: '
                f'{sorted(missing_ids)}'
            ))

        results = []

        for patient in patients:
            name = patient['name']
            reference_ids = [i for i in patient.get('cancerbot_trial_ids', []) if i not in missing_ids]
            cb_geo = cb_geo_by_name.get(name, {})
            # JSON zipcode takes priority over reference_patients_data.json geo;
            # cb_geo is used as a last-resort fallback when the JSON has no zipcode.
            json_zip     = patient.get('zipcode')
            json_country = patient.get('country_code')
            use_zip     = json_zip     or cb_geo.get('postalCode')
            use_country = json_country or cb_geo.get('country') or 'US'
            self.stdout.write(f'  {name} ...', ending='')

            person_id, row = _lookup_patient(source_db_url, patient, strategy)
            if row is None:
                self.stdout.write(self.style.WARNING(' NOT FOUND'))
                results.append({
                    'name': name,
                    'disease': patient.get('disease'),
                    'stage': patient.get('stage'),
                    'error': 'patient not found in DB',
                    'reference_top5': reference_ids,
                    'exact_top5': [],
                })
                continue

            # Warn when JSON zip differs from CTOMOP postal_code so the override is visible.
            db_zip = row.get('postal_code')
            if use_zip and db_zip and str(use_zip).strip() != str(db_zip).strip():
                self.stdout.write(
                    self.style.WARNING(
                        f'\n    [zip override] JSON={use_zip}/{use_country}  DB={db_zip}  '
                        f'— using JSON zip for distance scoring'
                    ),
                    ending='',
                )

            n = len(reference_ids)
            cb_scores = dict(cb_scores_by_name.get(name, {}))
            cb_token = cb_token_by_name.get(name)
            raw_study_info = cb_study_info_by_name.get(name)
            study_prefs = _study_preferences_from_cb(raw_study_info) if raw_study_info else None
            cb_weights = _cb_fetch_user_weights(cb_token) if cb_token else None
            if cb_weights and any(v != 25.0 for v in cb_weights.values()):
                self.stdout.write(
                    f'  weights (from CB): benefit={cb_weights["benefit_weight"]} '
                    f'burden={cb_weights["patient_burden_weight"]} '
                    f'risk={cb_weights["risk_weight"]} '
                    f'distance={cb_weights["distance_penalty_weight"]}'
                )
            try:
                exact_ids, exact_scores, probe, components, match_types, trial_objects, exact_pi = self._run_matching(
                    row, n,
                    zipcode=use_zip,
                    country_code=use_country,
                    watch_ids=reference_ids,
                    study_prefs=study_prefs,
                    weights=cb_weights,
                )
            except Exception as e:
                logger.exception('Matching failed for %s', name)
                self.stdout.write(self.style.ERROR(f' ERROR: {e}'))
                results.append({
                    'name': name,
                    'person_id': person_id,
                    'disease': patient.get('disease'),
                    'stage': patient.get('stage'),
                    'error': str(e),
                    'reference_top5': reference_ids,
                    'exact_top5': [],
                })
                continue

            reference_set = set(reference_ids)
            exact_set = set(exact_ids)
            overlap = [i for i in exact_ids if i in reference_set]
            exact_only = [i for i in exact_ids if i not in reference_set]
            reference_only = [i for i in reference_ids if i not in exact_set]

            self.stdout.write(
                f' overlap {len(overlap)}/{n} | '
                f'exact_only={exact_only} | reference_only={reference_only}'
            )
            for rank, tid in enumerate(exact_ids, 1):
                self.stdout.write(f'    {rank}. https://app.exact.org/t/{tid}')
            zip_label = f'{use_country} / {use_zip}' if use_zip else '—'
            self.stdout.write(f'    zip: {zip_label}')

            # Score comparison table for all trials (overlap + disputed)
            exact_rank = {tid: rank for rank, tid in enumerate(exact_ids, 1)}
            all_disputed = sorted(set(exact_ids) | set(reference_ids))
            if all_disputed:
                # Back-fill missing scores for all table rows via per-trial detail
                # endpoint (covers CB-only trials with null stored score, and any
                # trial not yet in cb_scores).
                if cb_token:
                    for tid in all_disputed:
                        if cb_scores.get(tid, {}).get('score') is None:
                            fetched = _cb_fetch_trial(cb_token, tid)
                            if fetched:
                                if tid not in cb_scores:
                                    cb_scores[tid] = fetched
                                else:
                                    for k, v in fetched.items():
                                        if cb_scores[tid].get(k) is None:
                                            cb_scores[tid][k] = v

                # Fetch a wider CB ranking for any trial still missing a rank
                # (covers EXACT-only trials and overlap trials whose rank wasn't
                # stored in reference_patients_data.json).
                missing_rank = [
                    tid for tid in all_disputed
                    if cb_scores.get(tid, {}).get('rank') is None
                    and cb_scores.get(tid, {}).get('matchingType') != 'not_eligible'
                ]
                if missing_rank and cb_token:
                    wider = _cb_fetch_wider_ranking(cb_token, target_ids=missing_rank)
                    for tid, data in wider.items():
                        if tid not in cb_scores:
                            cb_scores[tid] = data
                        else:
                            cb_scores[tid].setdefault('rank', data.get('rank'))
                            cb_scores[tid].setdefault('matchingType', data.get('matchingType'))

                rows_t = []
                for tid in all_disputed:
                    in_exact = tid in exact_set
                    in_cb = tid in reference_set
                    tag = ('overlap' if in_exact and in_cb
                           else 'EXACT only' if in_exact
                           else 'CB only')
                    e_score = exact_scores.get(tid) or probe.get(tid, {}).get('score')
                    cb_entry = cb_scores.get(tid, {})
                    c_score = cb_entry.get('score')
                    c_rank = cb_entry.get('rank')
                    p = probe.get(tid, {})
                    if tid in exact_rank:
                        e_rank_str = f'rank #{exact_rank[tid]}'
                    elif p.get('status') == 'ranked_out':
                        e_rank_str = f'rank #{p["rank"]}'
                    elif p.get('status') == 'ineligible':
                        reasons = p.get('reasons') or []
                        e_rank_str = 'ineligible' + (f' ({", ".join(reasons[:3])})' if reasons else '')
                    else:
                        e_rank_str = '—'
                    if c_rank is not None:
                        c_rank_str = f'rank #{c_rank}'
                    elif cb_entry.get('matchingType') == 'not_eligible':
                        c_rank_str = 'not_eligible'
                    else:
                        c_rank_str = '—'
                    comp = components.get(tid, {})
                    dist_m = comp.get('distance_m')
                    e_dist_str = f'{dist_m / 1609.34:.1f}mi' if dist_m is not None else '—'
                    cb_dist = cb_entry.get('distance')
                    cb_dist_units = cb_entry.get('distanceUnits', 'miles')
                    c_dist_str = f'{cb_dist}{cb_dist_units[0]}' if cb_dist is not None else '—'
                    e_match = match_types.get(tid)
                    if e_match is None:
                        p_status = p.get('status')
                        if p_status == 'ineligible':
                            e_match = 'not_eligible'
                        else:
                            e_match = '—'
                    c_match = cb_entry.get('matchingType') or '—'
                    rows_t.append((str(tid), tag,
                                   str(e_score) if e_score is not None else '—',
                                   str(c_score) if c_score is not None else '—',
                                   e_rank_str, c_rank_str,
                                   e_match, c_match))
                headers = ['Trial', 'Side', 'EXACT score', 'CB score', 'EXACT rank', 'CB rank',
                           'type(E)', 'type(CB)']
                col_w = [max(len(h), max(len(r[i]) for r in rows_t))
                         for i, h in enumerate(headers)]
                sep = '+-' + '-+-'.join('-' * w for w in col_w) + '-+'
                hdr = '| ' + ' | '.join(h.ljust(col_w[i]) for i, h in enumerate(headers)) + ' |'
                self.stdout.write('    ' + sep)
                self.stdout.write('    ' + hdr)
                self.stdout.write('    ' + sep)
                for r in rows_t:
                    self.stdout.write('    | ' + ' | '.join(
                        v.ljust(col_w[i]) for i, v in enumerate(r)) + ' |')

                self.stdout.write('    ' + sep)

                # Score component breakdown for trials where E-score and CB-score differ
                score_diff_rows = [
                    r for r in rows_t
                    if r[2] != '—' and r[3] != '—'
                    and abs(float(r[2]) - float(r[3])) > 1
                ]
                if score_diff_rows:
                    self.stdout.write('\n    ── score component breakdown (where |E-CB| > 1) ──')
                    for r in score_diff_rows:
                        tid_str = r[0]
                        tid = int(tid_str)
                        e_sc = float(r[2])
                        c_sc = float(r[3])
                        comp = components.get(tid, {})
                        e_benefit = comp.get('benefit')
                        e_burden  = comp.get('burden')
                        e_risk    = comp.get('risk')
                        dist_m    = comp.get('distance_m')
                        cb_entry  = cb_scores.get(tid, {})
                        cb_burden = cb_entry.get('burden')
                        cb_dist   = cb_entry.get('distance')
                        cb_dist_u = cb_entry.get('distanceUnits', 'miles')

                        _200mi_m = 200 * 1609.34

                        # EXACT contribution terms (0-25 each, weights all 25, sum=100)
                        e_benefit_c = (e_benefit / 20 * 25) if e_benefit is not None else None
                        e_burden_c  = ((1 - e_burden / 20) * 25) if e_burden is not None else None
                        e_risk_c    = ((1 - e_risk / 20) * 25) if e_risk is not None else None
                        if dist_m is not None:
                            e_dist_c = (1 - min(dist_m, _200mi_m) / _200mi_m) * 25
                        else:
                            e_dist_c = None

                        # CB contribution terms — burden is a stored field returned by API;
                        # distance is rounded but good enough for approximation
                        cb_burden_c = ((1 - cb_burden / 20) * 25) if cb_burden is not None else None
                        if cb_dist is not None:
                            cb_dist_m = cb_dist * (1000 if cb_dist_u == 'km' else 1609.34)
                            cb_dist_c = (1 - min(cb_dist_m, _200mi_m) / _200mi_m) * 25
                        else:
                            cb_dist_c = None

                        # Back-calc: CB benefit+risk contribution from known goodnessScore
                        if cb_burden_c is not None and cb_dist_c is not None:
                            cb_br_c = (c_sc - 0.5) - cb_burden_c - cb_dist_c
                        else:
                            cb_br_c = None
                        e_br_c = (e_benefit_c + e_risk_c) if (e_benefit_c is not None and e_risk_c is not None) else None

                        def _fmt(val, fmt='.1f'):
                            return format(val, fmt) if val is not None else '?'

                        self.stdout.write(
                            f'    trial {tid}: E={e_sc} CB={c_sc} Δ={e_sc - c_sc:+.0f}'
                        )
                        e_dist_str = f'{dist_m / 1609.34:.0f}mi' if dist_m is not None else '?'
                        self.stdout.write(
                            f'      EXACT   benefit={e_benefit}({_fmt(e_benefit_c)})  '
                            f'burden={e_burden}({_fmt(e_burden_c)})  '
                            f'risk={e_risk}({_fmt(e_risk_c)})  '
                            f'dist={e_dist_str}({_fmt(e_dist_c)})'
                        )
                        cb_dist_str = f'{cb_dist}{cb_dist_u[0]}' if cb_dist is not None else '?'
                        self.stdout.write(
                            f'      CB      burden={cb_burden}({_fmt(cb_burden_c)})  '
                            f'dist={cb_dist_str}({_fmt(cb_dist_c)})  '
                            f'benefit+risk(back-calc)={_fmt(cb_br_c)}  '
                            f'vs EXACT benefit+risk={_fmt(e_br_c)}'
                        )

                # Per-attribute explain for rows where type(E) != type(CB)
                if options.get('explain'):
                    from trials.services.trial_match_explainer import TrialMatchExplainer
                    for r in rows_t:
                        tid_str, tag, _, _, _, _, e_m, c_m = r
                        if e_m == c_m or e_m == '—' or c_m == '—':
                            continue
                        tid = int(tid_str)
                        self.stdout.write(f'\n    ── explain trial {tid} (type(E)={e_m}, type(CB)={c_m}) ──')

                        # EXACT side
                        trial_obj = trial_objects.get(tid)
                        if trial_obj is None:
                            try:
                                from trials.models import Trial as _Trial
                                trial_obj = _Trial.objects.get(id=tid)
                            except Exception:
                                trial_obj = None
                        if trial_obj and exact_pi:
                            reasons = TrialMatchExplainer(trial_obj, exact_pi).explain()
                            bad = [x for x in reasons if x['status'] != 'matched']
                            if bad:
                                self.stdout.write('    EXACT not-matched/unknown:')
                                for x in bad:
                                    self.stdout.write(
                                        f'      [{x["status"]:11}] {x["attr"]:45} '
                                        f'patient={str(x["patientValue"])[:30]}  '
                                        f'trial={str(x["trialRequirement"])[:30]}'
                                    )

                        # CB side
                        if cb_token:
                            # Prefer cached attributesToFillIn from the search results
                            # (detail endpoint doesn't return this field).
                            cached_score = cb_scores.get(tid, {})
                            cached_attrs_to_fill = cached_score.get('attributesToFillIn') or []
                            if cached_attrs_to_fill:
                                self.stdout.write('    CB potential — patient needs to fill in:')
                                for attr in cached_attrs_to_fill:
                                    title = (attr.get('userAttributeTitle')
                                             or attr.get('trialAttributeName')
                                             or str(attr))
                                    self.stdout.write(f'      {title}')
                            else:
                                # Fall back to live detail endpoint (won't show attributesToFillIn
                                # but may show not_matched eligibility attributes)
                                cb_attrs = _cb_fetch_trial_explain(cb_token, tid)
                                if cb_attrs:
                                    self.stdout.write('    CB not-matched/unknown:')
                                    for title, status in cb_attrs.items():
                                        self.stdout.write(f'      [{status:11}] {title}')
                                else:
                                    self.stdout.write(
                                        '    CB: no explain data available '
                                        '(re-run fetch_reference_patients.py to capture attributesToFillIn)'
                                    )

            results.append({
                'name': name,
                'person_id': person_id,
                'disease': patient.get('disease'),
                'stage': patient.get('stage'),
                'reference_top5': reference_ids,
                'exact_top5': exact_ids,
                'overlap_count': len(overlap),
                'overlap_total': n,
                'overlap_ids': overlap,
                'exact_only': exact_only,
                'reference_only': reference_only,
                'reference_only_probe': {str(tid): probe.get(tid, {}) for tid in reference_only},
            })

        # Summary
        matched = [r for r in results if 'overlap_count' in r]
        if matched:
            avg_overlap = sum(r['overlap_count'] for r in matched) / len(matched)
            avg_total = sum(r['overlap_total'] for r in matched) / len(matched)
            avg_scaled = (avg_overlap / avg_total) * top_n if avg_total else 0
            perfect = sum(1 for r in matched if r['overlap_count'] == r['overlap_total'])
            self.stdout.write(
                f'\nSummary: {len(matched)} patients | '
                f'avg overlap {avg_scaled:.1f}/{top_n} | '
                f'perfect matches {perfect}/{len(matched)}'
            )

        prefix = options['output']
        json_path = f'{prefix}.json'
        txt_path = f'{prefix}.txt'

        with open(json_path, 'w') as f:
            json.dump(results, f, indent=2)

        with open(txt_path, 'w') as f:
            for r in results:
                ids_str = ', '.join(str(i) for i in r.get('exact_top5', []))
                f.write(f"{r['name']}\t{ids_str}\n")

        self.stdout.write(self.style.SUCCESS(f'\nJSON: {json_path}'))
        self.stdout.write(self.style.SUCCESS(f'TXT:  {txt_path}'))
        self.stdout.write(self.style.SUCCESS(f'LOG:  {log_path}'))
        self._log_fh.close()

    def _run_matching(self, row, top_n, zipcode=None, country_code='US', watch_ids=None, study_prefs=None, weights=None):
        """
        Build a PatientInfo from the DB row, run filtered_trials + goodness
        scoring, and return the top-N trial IDs ordered by goodness_score desc.

        Also probes watch_ids (typically reference_only trials) to report whether
        each was filtered out (ineligible) or passed eligibility but ranked below top-N.

        Returns (top_ids, probe_results) where probe_results is a dict:
            {trial_id: {'status': 'ineligible' | 'ranked', 'rank': int, 'score': float}}
        """
        from trials.management.commands.search_trials_for_patients import _build_patient_info
        from trials.models import Trial
        from trials.services.study_preferences import StudyPreferences

        pi = _build_patient_info(row)

        if zipcode:
            import pgeocode
            from django.contrib.gis.geos import Point
            nomi = pgeocode.Nominatim(country_code)
            loc = nomi.query_postal_code(zipcode)
            if loc is not None and not (loc.latitude != loc.latitude):  # NaN check
                pi.geo_point = Point(float(loc.longitude), float(loc.latitude), srid=4326)

        study_info = study_prefs or StudyPreferences(recruitment_status='RECRUITING')
        queryset, _ = Trial.objects.all().filtered_trials(
            search_options={},
            study_info=study_info,
            patient_info=pi,
        )
        if pi.geo_point:
            queryset = queryset.with_distance_optimized(pi.geo_point, recruitment_status=study_info.recruitment_status)
        w = weights or {}
        queryset = queryset.with_goodness_score_optimized(
            benefit_weight=w.get('benefit_weight', 25.0),
            patient_burden_weight=w.get('patient_burden_weight', 25.0),
            risk_weight=w.get('risk_weight', 25.0),
            distance_penalty_weight=w.get('distance_penalty_weight', 25.0),
            geo_point=pi.geo_point,
            recruitment_status=study_info.recruitment_status,
        )
        queryset = queryset.order_by('-goodness_score', 'id')

        from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

        # Fetch a larger candidate pool so that after filtering out not_eligible
        # trials (queryset/matcher gap) we still have enough to fill top_n slots.
        candidate_n = (top_n * 4) if top_n else None
        candidate_trials = list(queryset[:candidate_n])
        candidate_ids_ordered = [t.id for t in candidate_trials]

        # Watch IDs that fell outside the candidate window need separate fetch.
        extra_watch_ids = set(watch_ids or []) - set(candidate_ids_ordered)
        extra_watch_trials = (
            list(queryset.filter(id__in=extra_watch_ids)) if extra_watch_ids else []
        )

        components = {}
        match_types = {}
        score_map = {}
        for trial in candidate_trials + extra_watch_trials:
            dist = getattr(trial, 'distance', None)
            components[trial.id] = {
                'benefit': trial.benefit_score,
                'burden': trial.patient_burden_score,
                'risk': trial.risk_score,
                'distance_m': dist.m if dist is not None else None,
            }
            match_types[trial.id] = UserToTrialAttrMatcher(trial=trial, patient_info=pi).trial_match_status()
            score = getattr(trial, 'goodness_score', None)
            score_map[trial.id] = float(score) if score is not None else None

        # Build top_ids: preserve queryset rank order, exclude not_eligible, cap at top_n.
        top_ids = [
            tid for tid in candidate_ids_ordered
            if match_types.get(tid) != 'not_eligible'
        ][:top_n]
        top_scores = {tid: score_map[tid] for tid in top_ids}

        probe = {}
        if watch_ids:
            # Which watch IDs passed filtered_trials?
            eligible_watch = set(
                queryset.filter(id__in=watch_ids).values_list('id', flat=True)
            )
            # For those that passed, find their rank and score
            for trial in queryset.filter(id__in=eligible_watch):
                score = getattr(trial, 'goodness_score', None)
                if score is not None:
                    rank = (queryset.filter(goodness_score__gt=score).count() +
                            queryset.filter(goodness_score=score, id__lt=trial.id).count() + 1)
                else:
                    rank = None
                probe[trial.id] = {
                    'status': 'ranked_out',
                    'rank': rank,
                    'score': float(score) if score is not None else None,
                }
            ineligible_ids = [tid for tid in watch_ids if tid not in eligible_watch]
            # Score ineligible trials using the unfiltered queryset — goodness score
            # is independent of eligibility and is useful for comparison.
            if ineligible_ids:
                ineligible_qs = Trial.objects.filter(id__in=ineligible_ids)
                ineligible_qs = ineligible_qs.with_goodness_score_optimized(
                    benefit_weight=w.get('benefit_weight', 25.0),
                    patient_burden_weight=w.get('patient_burden_weight', 25.0),
                    risk_weight=w.get('risk_weight', 25.0),
                    distance_penalty_weight=w.get('distance_penalty_weight', 25.0),
                    geo_point=pi.geo_point,
                    recruitment_status=study_info.recruitment_status,
                )
                ineligible_scores = {t.id: getattr(t, 'goodness_score', None) for t in ineligible_qs}
            else:
                ineligible_scores = {}
            for tid in ineligible_ids:
                _, patient_traces = Trial.objects.filter(id=tid).filter_by_patient_info(pi, add_traces=True)
                reasons = [
                    f'{t["attr"].replace("patient_info.", "")}={t["val"]}'
                    for t in (patient_traces or [])
                    if t.get('dropped', 0) > 0
                ]
                score = ineligible_scores.get(tid)
                probe[tid] = {
                    'status': 'ineligible',
                    'reasons': reasons,
                    'score': float(score) if score is not None else None,
                }

        trial_objects = {t.id: t for t in candidate_trials + extra_watch_trials}
        return top_ids, top_scores, probe, components, match_types, trial_objects, pi
