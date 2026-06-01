"""
Management command: search_trials_for_patients

Reads PatientInfo records from an external patient database and matches them
against the trials database using the EXACT matching engine in-process — no
web server required.

Usage
-----
    python manage.py search_trials_for_patients \\
      --source-db-url postgresql://user:pass@host:5432/patients

    Falls back to PATIENT_DATABASE_URL env var if --source-db-url is not given.

Common options
--------------
    --person-ids 1,2,3      # optional: filter to specific person IDs
    --patient-limit 100     # max patients to process (default: all)
    --batch-size 100        # rows per DB fetch
    --limit 50              # max trials to return per patient
    --benefit-weight 25.0
    --patient-burden-weight 25.0
    --risk-weight 25.0
    --distance-penalty-weight 25.0
    --output results.json   # write full JSON output to file
    --format json|csv
    --dry-run               # print first patient's parsed data and exit
"""
import csv
import json
import logging
import os
import shutil
import subprocess

from django.core.management.base import BaseCommand, CommandError
from trials.services.patient_info.ctomop_adapter import (
    JSON_FIELDS,
    OUTCOME_MAP as _OUTCOME_MAP,
    REFRACTORY_MAP as _REFRACTORY_MAP,
    SKIP_COLUMNS,
    build_patient_info_from_ctomop_row as _build_patient_info,
    normalize_ctomop_row as _normalize_ctomop_row,
    resolve_code as _resolve_code,
    resolve_code_csv as _resolve_code_csv,
    resolve_therapy_code as _resolve_therapy_code,
)
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher



logger = logging.getLogger(__name__)




class Command(BaseCommand):
    help = (
        'Read PatientInfo records from an external patient database and match '
        'them against trials using the EXACT matching engine (in-process).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--source-db-url',
            type=str,
            default=os.environ.get('PATIENT_DATABASE_URL', ''),
            help='PostgreSQL connection URL for the patient database. '
                 'Falls back to PATIENT_DATABASE_URL env var.',
        )

        # --- Filtering ---
        parser.add_argument(
            '--person-ids',
            type=str,
            default='',
            help='Comma-separated person IDs to process (default: all)',
        )
        parser.add_argument(
            '--patient-limit',
            type=int,
            default=None,
            help='Maximum number of patients to process (default: all). '
                 'Applied before --person-ids filtering.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Rows per DB batch fetch (default: 100)',
        )

        # --- Trial search ---
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Max trials to return per patient (default: 50)',
        )

        # --- Goodness score weights ---
        parser.add_argument(
            '--benefit-weight',
            type=float,
            default=25.0,
            help='Goodness score benefit component weight (default: 25.0)',
        )
        parser.add_argument(
            '--patient-burden-weight',
            type=float,
            default=25.0,
            help='Goodness score patient burden component weight (default: 25.0)',
        )
        parser.add_argument(
            '--risk-weight',
            type=float,
            default=25.0,
            help='Goodness score risk component weight (default: 25.0)',
        )
        parser.add_argument(
            '--distance-penalty-weight',
            type=float,
            default=25.0,
            help='Goodness score distance penalty component weight (default: 25.0)',
        )

        # --- Output ---
        parser.add_argument(
            '--output',
            type=str,
            default='',
            help='Write full results to this file path (default: stdout summary only)',
        )
        parser.add_argument(
            '--format',
            dest='output_format',
            choices=['json', 'csv', 'ground_truth'],
            default='json',
            help='Output format for --output file. '
                 '"ground_truth" writes one row per trial in ground truth CSV format '
                 '(CTOMOP Patient ID, Trial, Eligible/Potential, Suitability Score). '
                 'Default: json',
        )
        parser.add_argument(
            '--ground-truth-csv',
            type=str,
            default='',
            help='Also write results in ground truth CSV format to this path '
                 '(CTOMOP Patient ID, Trial, Eligible/Potential, Suitability Score). '
                 'Can be combined with --output / --format.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print the parsed data for the first patient and exit',
        )

        # --- Study preferences / search filters ---
        parser.add_argument(
            '--search-title',
            type=str,
            default='',
            help='Filter trials by title keyword',
        )
        parser.add_argument(
            '--recruitment-status',
            type=str,
            default='',
            help='Filter by recruitment status (e.g. RECRUITING)',
        )
        parser.add_argument(
            '--sponsor',
            type=str,
            default='',
            help='Filter trials by sponsor name',
        )
        parser.add_argument(
            '--register',
            type=str,
            default='',
            help='Filter by trial register (e.g. ClinicalTrials.gov)',
        )
        parser.add_argument(
            '--trial-type',
            type=str,
            default='',
            help='Filter by trial type',
        )
        parser.add_argument(
            '--study-type',
            type=str,
            default='',
            help='Filter by study type',
        )
        parser.add_argument(
            '--study-id',
            type=str,
            default='',
            help='Filter by specific study ID (e.g. NCT number)',
        )
        parser.add_argument(
            '--validated-only',
            action='store_true',
            help='Return only manually validated trials',
        )
        parser.add_argument(
            '--distance',
            type=float,
            default=None,
            help='Maximum distance from patient location to trial site',
        )
        parser.add_argument(
            '--distance-units',
            type=str,
            default='km',
            choices=['km', 'miles'],
            help='Distance units: km or miles (default: km)',
        )
        parser.add_argument(
            '--country',
            type=str,
            default='',
            help='Filter trials to a specific country code (e.g. US, DE)',
        )
        parser.add_argument(
            '--region',
            type=str,
            default='',
            help='Filter trials to a specific region/state',
        )
        parser.add_argument(
            '--postal-code',
            type=str,
            default='',
            help='Filter trials near a postal code',
        )
        parser.add_argument(
            '--last-update',
            type=str,
            default='',
            help='Filter trials updated after this date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--first-enrolment',
            type=str,
            default='',
            help='Filter trials with first enrolment after this date (YYYY-MM-DD)',
        )

    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        person_ids = []
        if options['person_ids']:
            person_ids = [int(x.strip()) for x in options['person_ids'].split(',') if x.strip()]

        rows = self._fetch_via_db(options, person_ids)

        if rows is None:
            # Exit non-zero so a wrapping shell pipeline (trials4patients.sh)
            # halts instead of proceeding to a missing-output FileNotFoundError.
            raise CommandError('Patient DB fetch failed — see error above.')

        all_results = []
        processed = 0
        errors = 0

        for row in rows:
            person_id = row.get('person_id')
            disease = row.get('disease') or ''

            if not disease:
                self.stdout.write(f'  Skipping person_id={person_id} (no disease set)')
                continue

            if options['dry_run']:
                self.stdout.write(
                    f'person_id={person_id}, disease={disease}\n'
                    f'Row keys: {list(row.keys())}'
                )
                return

            try:
                result = self._search_trials_direct(
                    row=dict(row),
                    person_id=person_id,
                    disease=disease,
                    limit=options['limit'],
                    benefit_weight=options['benefit_weight'],
                    patient_burden_weight=options['patient_burden_weight'],
                    risk_weight=options['risk_weight'],
                    distance_penalty_weight=options['distance_penalty_weight'],
                    search_title=options['search_title'],
                    recruitment_status=options['recruitment_status'],
                    sponsor=options['sponsor'],
                    register=options['register'],
                    trial_type=options['trial_type'],
                    study_type=options['study_type'],
                    study_id=options['study_id'],
                    validated_only=options['validated_only'],
                    distance=options['distance'],
                    distance_units=options['distance_units'],
                    country=options['country'],
                    region=options['region'],
                    postal_code=options['postal_code'],
                    last_update=options['last_update'],
                    first_enrolment=options['first_enrolment'],
                )
                all_results.append(result)
                self._print_patient_summary(result)
                processed += 1

                if processed % 10 == 0:
                    self.stdout.write(f'  Processed {processed} patients...')

            except Exception:
                errors += 1
                logger.exception('Error searching trials for person_id=%s', person_id)
                self.stderr.write(self.style.ERROR(f'  Error for person_id={person_id}'))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Patients processed: {processed}, Errors: {errors}'
        ))

        # A run where every patient errored produced no usable results. Fail
        # non-zero rather than writing an empty output file and exiting 0,
        # which would let a wrapping pipeline mistake total failure for success.
        if processed == 0 and errors > 0:
            raise CommandError(
                f'All {errors} patient(s) failed — no results produced.'
            )

        if options['output']:
            self._write_output(all_results, options['output'], options['output_format'])
            self.stdout.write(self.style.SUCCESS(f'Results written to: {options["output"]}'))

        if options['ground_truth_csv']:
            self._write_output(all_results, options['ground_truth_csv'], 'ground_truth')
            self.stdout.write(self.style.SUCCESS(f'Ground truth CSV written to: {options["ground_truth_csv"]}'))

    # ------------------------------------------------------------------
    # Source: DB
    # ------------------------------------------------------------------

    def _fetch_via_db(self, options, person_ids):
        """Fetch rows from the patient_info table via psql subprocess.

        Uses psql instead of a direct psycopg2 connection to avoid a
        double-free crash on macOS/conda when two libpq connections are open
        simultaneously (Django's trials DB connection + a second connection).
        """
        source_db_url = options['source_db_url']
        if not source_db_url:
            self.stderr.write(self.style.ERROR(
                'No source DB URL. Use --source-db-url or set PATIENT_DATABASE_URL.'
            ))
            return None

        if not shutil.which('psql'):
            self.stderr.write(self.style.ERROR(
                'psql not found in PATH — required for patient DB queries.'
            ))
            return None

        # Build query — person_ids and patient_limit are integers, safe to inline
        query = '''
            SELECT pi.*, p.gender_source_value, p.gender_concept_id
            FROM patient_info pi
            JOIN person p ON pi.person_id = p.person_id
        '''
        if person_ids:
            ids_str = ', '.join(str(int(pid)) for pid in person_ids)
            query += f' WHERE p.person_id IN ({ids_str})'
        query += ' ORDER BY p.person_id'
        if options.get('patient_limit'):
            query += f' LIMIT {int(options["patient_limit"])}'

        wrapped = f'SELECT row_to_json(t) FROM ({query}) t'
        env = {**os.environ, 'PGSSLMODE': 'require'}

        try:
            result = subprocess.run(
                ['psql', source_db_url, '-t', '--no-psqlrc', '-c', wrapped],
                capture_output=True,
                text=True,
                env=env,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'psql failed: {e}'))
            return None

        if result.returncode != 0:
            self.stderr.write(self.style.ERROR(f'psql error: {result.stderr.strip()}'))
            return None

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
            self.stderr.write(self.style.WARNING(
                f'  Skipped {skipped} non-JSON line(s) from psql output.'
            ))

        return rows

    # ------------------------------------------------------------------
    # Direct trial search (in-process, no HTTP)
    # ------------------------------------------------------------------

    def _search_trials_direct(self, row: dict, person_id, disease, limit: int,
                               benefit_weight=25.0, patient_burden_weight=25.0,
                               risk_weight=25.0, distance_penalty_weight=25.0,
                               search_title='', recruitment_status='', sponsor='',
                               register='', trial_type='', study_type='', study_id='',
                               validated_only=False, distance=None, distance_units='km',
                               country='', region='', postal_code='',
                               last_update='', first_enrolment=''):
        """Match trials against a patient row using the EXACT ORM directly.

        Pipeline:
          1. _build_patient_info() → normalize_patient_info()
          2. Trial.objects.filtered_trials() — eligibility queryset
          3. with_goodness_score_optimized() — scoring
          4. UserToTrialAttrMatcher — eligible/potential classification
        """
        from trials.models import Trial
        from trials.services.study_preferences import StudyPreferences

        pi = _build_patient_info(row)

        study_prefs = StudyPreferences(
            search_title=search_title or None,
            sponsor=sponsor or None,
            register=register or None,
            study_id=study_id or None,
            trial_type=trial_type or None,
            study_type=study_type or None,
            recruitment_status=recruitment_status or None,
            country=country or None,
            region=region or None,
            postal_code=postal_code or None,
            distance=distance,
            distance_units=distance_units,
            validated_only=validated_only,
            last_update=last_update or None,
            first_enrolment=first_enrolment or None,
        )

        queryset = Trial.objects.all()
        queryset, _ = queryset.filtered_trials(
            search_options={},
            study_info=study_prefs,
            patient_info=pi,
            add_traces=False,
        )
        queryset = queryset.with_goodness_score_optimized(
            benefit_weight=benefit_weight,
            patient_burden_weight=patient_burden_weight,
            risk_weight=risk_weight,
            distance_penalty_weight=distance_penalty_weight,
            geo_point=pi.geo_point if pi else None,
            recruitment_status=study_prefs.recruitment_status,
        )

        total = queryset.count()
        trials_page = list(queryset[:limit])

        eligible = []
        potential = []
        scores = []
        goodness_scores = []
        trials_out = []

        for trial in trials_page:
            matcher = UserToTrialAttrMatcher(trial=trial, patient_info=pi)
            match_type = matcher.trial_match_status()
            if match_type == 'not_eligible':
                continue
            match_score = matcher.trial_match_score()
            goodness_score = getattr(trial, 'goodness_score', None)
            if match_score is not None:
                scores.append(match_score)
            if goodness_score is not None:
                goodness_scores.append(float(goodness_score))
            trial_data = {
                'studyId': trial.study_id,
                'briefTitle': trial.brief_title,
                'officialTitle': trial.official_title,
                'matchingType': match_type,
                'matchScore': match_score,
                'goodnessScore': goodness_score,
                'recruitmentStatus': trial.recruitment_status,
                'phase': trial.phases,
                'studyType': trial.study_type,
                'sponsor': trial.sponsor_name,
                'link': trial.link,
                'disease': trial.disease,
                'register': trial.register,
            }
            trials_out.append(trial_data)
            if match_type == 'eligible':
                eligible.append(trial_data)
            else:
                potential.append(trial_data)

        return {
            'person_id': person_id,
            'disease': disease,
            'total_trials': total,
            'returned_trials': len(trials_page),
            'eligible_count': len(eligible),
            'potential_count': len(potential),
            'best_match_score': max(scores) if scores else None,
            'best_goodness_score': max(goodness_scores) if goodness_scores else None,
            'eligible_trials': eligible,
            'potential_trials': potential,
            'trials': trials_out,
        }

    def _print_patient_summary(self, result):
        pid = result['person_id']
        disease = result['disease'] or '(unknown)'
        total = result['total_trials']
        eligible = result['eligible_count']
        potential = result['potential_count']
        match_score = result['best_match_score']
        goodness_score = result.get('best_goodness_score')
        match_str = f'{match_score}%' if match_score is not None else 'n/a'
        goodness_str = f'{goodness_score:.1f}' if goodness_score is not None else 'n/a'

        self.stdout.write(
            f'  person_id={pid} [{disease}] '
            f'→ {total} total | {eligible} eligible | {potential} potential '
            f'| best match: {match_str} | best goodness: {goodness_str}'
        )

    def _write_output(self, results, path, fmt):
        if fmt == 'json':
            with open(path, 'w') as f:
                json.dump(results, f, indent=2, default=str)

        elif fmt == 'csv':
            if not results:
                return
            fieldnames = [
                'person_id', 'disease',
                'total_trials', 'eligible_count', 'potential_count',
                'best_match_score', 'best_goodness_score', 'top_trial_ids',
            ]
            with open(path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in results:
                    top_ids = ','.join(
                        t.get('studyId', '') for t in r.get('trials', [])[:5]
                    )
                    writer.writerow({
                        'person_id': r['person_id'],
                        'disease': r['disease'],
                        'total_trials': r['total_trials'],
                        'eligible_count': r['eligible_count'],
                        'potential_count': r['potential_count'],
                        'best_match_score': r['best_match_score'],
                        'best_goodness_score': r.get('best_goodness_score'),
                        'top_trial_ids': top_ids,
                    })

        elif fmt == 'ground_truth':
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'CTOMOP Patient ID', 'Trial',
                    'Eligible/Potential', 'Suitability Score',
                ])
                for r in results:
                    for t in r.get('trials', []):
                        trial_id = t.get('studyId') or ''
                        writer.writerow([
                            r['person_id'],
                            trial_id,
                            t.get('matchingType', ''),
                            int(t['goodnessScore']) if t.get('goodnessScore') is not None else '',
                        ])
