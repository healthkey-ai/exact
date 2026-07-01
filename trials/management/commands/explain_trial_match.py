"""
Management command: explain_trial_match

For a specific patient+trial pair, shows the per-attribute match status from
two sources side-by-side:
  - CTOMOP  — patient data read from the external patient DB (EXACT's view)
  - CB      — patient data read from reference_patients_data.json (reference system's view)

This makes it easy to see EXACTLY which attribute causes:
  - eligible (EXACT) vs potential (CB)  — attribute is known in CTOMOP, unknown in CB
  - eligible (EXACT) vs not_eligible (CB)  — attribute differs between the two sources
  - ranking differences  — score component breakdown per trial

Usage:
    python manage.py explain_trial_match \\
      --person-id 20494 \\
      --trial-id 18141 \\
      --source-db-url "$PATIENT_DATABASE_URL"

    # Optionally pass explicit patient name to match reference data entry:
    python manage.py explain_trial_match \\
      --person-id 20494 \\
      --name "Charlotte Walker" \\
      --trial-id 18141 \\
      --source-db-url "$PATIENT_DATABASE_URL" \\
      --cb-data scripts/reference_patients_data.json
"""
import json
import os

from django.core.management.base import BaseCommand

from trials.management.commands.compare_trials import _psql_query_one
from trials.management.commands.search_trials_for_patients import _build_patient_info
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher


def _get_attr_statuses(trial, pi):
    """Return {attr: status} for all mapping attrs relevant to the trial's disease."""
    matcher = UserToTrialAttrMatcher(trial, pi)
    out = {}
    for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items():
        if 'disease' in meta and (
            matcher.disease_code is None or matcher.disease_code not in meta['disease']
        ):
            continue
        out[attr] = matcher.attr_match_status(attr)
    return out


def _get_patient_value(pi, attr):
    """Return the patient's value for an attr, falling back gracefully."""
    from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
    try:
        v = PatientInfoAttributes(pi).get_value(attr)
        if v is None or v == '' or v == [] or v == {}:
            return None
        return v
    except Exception:
        return getattr(pi, attr, None)


def _get_trial_requirement(trial, attr, meta):
    """Return the trial's requirement for this attr (the value it filters on)."""
    trial_attr = meta.get('attr')
    if not trial_attr:
        return '(computed)'
    val = getattr(trial, trial_attr, None)
    # For min/max pairs, show both
    if val is None and 'min_attr' in str(meta):
        return '(min/max range)'
    return val


class Command(BaseCommand):
    help = 'Show per-attribute match status (CTOMOP vs CB) for a patient+trial pair.'

    def add_arguments(self, parser):
        parser.add_argument('--person-id', type=int, required=True)
        parser.add_argument('--trial-id', type=int, required=True)
        parser.add_argument(
            '--name',
            default='',
            help='Patient full name — used to look up CB data entry.',
        )
        parser.add_argument(
            '--source-db-url',
            default=os.environ.get('PATIENT_DATABASE_URL', ''),
        )
        parser.add_argument(
            '--cb-data',
            default='scripts/reference_patients_data.json',
            help='Path to reference patients data JSON (default: scripts/reference_patients_data.json)',
        )

    def handle(self, *args, **options):
        from trials.models import Trial
        from trials.services.patient_info.resolve import _build_in_memory

        person_id = options['person_id']
        trial_id = options['trial_id']
        db_url = options['source_db_url']
        cb_data_path = options['cb_data']

        # ── Fetch trial ──────────────────────────────────────────────────
        try:
            trial = Trial.objects.get(id=trial_id)
        except Trial.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Trial {trial_id} not found in trials DB.'))
            return

        # ── Fetch CTOMOP patient row ──────────────────────────────────────
        row = _psql_query_one(db_url, f'''
            SELECT pi.*, p.given_name, p.family_name,
                   p.gender_source_value, p.gender_concept_id
            FROM patient_info pi
            JOIN person p ON pi.person_id = p.person_id
            WHERE pi.person_id = {person_id}
            LIMIT 1
        ''')
        if row is None:
            self.stderr.write(self.style.ERROR(f'person_id={person_id} not found in patient DB.'))
            return

        ctomop_name = f"{row.get('given_name', '')} {row.get('family_name', '')}".strip()
        patient_name = options['name'] or ctomop_name

        ctomop_pi = _build_patient_info(dict(row))

        self.stdout.write(
            f'\n=== {patient_name} (person_id={person_id}) → Trial {trial_id} '
            f'[{trial.disease}] ===\n'
        )
        self.stdout.write(
            f'  CTOMOP: zip={row.get("postal_code")}  stage={getattr(ctomop_pi, "stage", None)!r}  '
            f'prior_therapy={getattr(ctomop_pi, "prior_therapy", None)!r}'
        )

        # ── Fetch CB PatientInfo ──────────────────────────────────────────
        cb_pi = None
        if os.path.exists(cb_data_path):
            with open(cb_data_path) as f:
                cb_entries = json.load(f)
            cb_entry = next(
                (e for e in cb_entries if e['name'].lower() == patient_name.lower()),
                None,
            )
            if cb_entry:
                cb_patient_info = cb_entry.get('patient_info') or {}
                # _build_in_memory accepts camelCase (same format as the CB API)
                try:
                    cb_pi = _build_in_memory(cb_patient_info)
                    self.stdout.write(
                        f'  CB:     zip={cb_patient_info.get("postalCode")}  '
                        f'stage={getattr(cb_pi, "stage", None)!r}  '
                        f'prior_therapy={getattr(cb_pi, "prior_therapy", None)!r}'
                    )
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'  CB PatientInfo build failed: {e}'))
                    cb_pi = None
            else:
                self.stdout.write(self.style.WARNING(
                    f'  No CB entry found for "{patient_name}" in {cb_data_path}'
                ))
        else:
            self.stdout.write(self.style.WARNING(f'  CB data file not found: {cb_data_path}'))

        # ── Compute per-attribute statuses ────────────────────────────────
        ctomop_statuses = _get_attr_statuses(trial, ctomop_pi)
        cb_statuses = _get_attr_statuses(trial, cb_pi) if cb_pi else {}

        ctomop_overall = (
            'not_eligible' if 'not_matched' in ctomop_statuses.values()
            else 'potential' if 'unknown' in ctomop_statuses.values()
            else 'eligible'
        )
        cb_overall = (
            'not_eligible' if 'not_matched' in cb_statuses.values()
            else 'potential' if 'unknown' in cb_statuses.values()
            else 'eligible'
        ) if cb_pi else '—'

        self.stdout.write(f'\n  Overall  CTOMOP: {ctomop_overall}   CB: {cb_overall}\n')

        # ── Print attribute table ─────────────────────────────────────────
        STATUS_COLOR = {
            'matched':     self.style.SUCCESS,
            'unknown':     self.style.WARNING,
            'not_matched': self.style.ERROR,
        }

        # Collect rows
        rows = []
        for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items():
            ctomop_s = ctomop_statuses.get(attr)
            if ctomop_s is None:
                continue  # not applicable for this disease
            cb_s = cb_statuses.get(attr) if cb_pi else None

            ctomop_val = _get_patient_value(ctomop_pi, attr)
            cb_val = _get_patient_value(cb_pi, attr) if cb_pi else None

            rows.append((attr, ctomop_s, cb_s, ctomop_val, cb_val))

        # Group by whether they differ
        differs = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in rows if cs != cbs]
        same_bad = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in rows if cs == cbs and cs != 'matched']
        same_good = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in rows if cs == cbs and cs == 'matched']

        col_a = max((len(a) for a, *_ in rows), default=20) + 2

        def _row_line(attr, ctomop_s, cb_s, ctomop_val, cb_val, highlight=False):
            ctomop_col = STATUS_COLOR.get(ctomop_s or '', lambda x: x)(f'{ctomop_s or "—":12}')
            cb_col = STATUS_COLOR.get(cb_s or '', lambda x: x)(f'{cb_s or "—":12}') if cb_pi else ''
            val_col = f'ctomop={str(ctomop_val)[:30]:<32}'
            cb_val_col = f'cb={str(cb_val)[:30]:<32}' if cb_pi else ''
            diff_marker = ' ◄ DIFFERS' if highlight else ''
            return f'  {attr:<{col_a}} {ctomop_col} {cb_col}  {val_col}{cb_val_col}{diff_marker}'

        if differs:
            self.stdout.write(self.style.MIGRATE_HEADING('── Attributes where CTOMOP ≠ CB ──'))
            for row in differs:
                self.stdout.write(_row_line(*row, highlight=True))

        if same_bad:
            self.stdout.write(self.style.MIGRATE_HEADING('\n── Attributes with issues in BOTH sources ──'))
            for row in same_bad:
                self.stdout.write(_row_line(*row))

        self.stdout.write(self.style.MIGRATE_HEADING(f'\n── Matched in both ({len(same_good)} attributes) ──'))
        for row in same_good:
            self.stdout.write(_row_line(*row))

        # ── Summary of key differences ────────────────────────────────────
        if differs:
            self.stdout.write('\n')
            ctomop_to_potential = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in differs
                                   if cs == 'matched' and cbs == 'unknown']
            ctomop_to_not_eligible = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in differs
                                      if cs == 'matched' and cbs == 'not_matched']
            cb_to_potential = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in differs
                               if cbs == 'matched' and cs == 'unknown']
            cb_to_not_eligible = [(a, cs, cbs, cv, cbv) for a, cs, cbs, cv, cbv in differs
                                  if cbs == 'matched' and cs == 'not_matched']

            if ctomop_to_potential:
                self.stdout.write(self.style.WARNING(
                    f'  CTOMOP has data that CB lacks (causes eligible→potential in CB):'
                ))
                for a, cs, cbs, cv, cbv in ctomop_to_potential:
                    self.stdout.write(f'    {a}: ctomop={cv!r}  cb={cbv!r}')

            if ctomop_to_not_eligible:
                self.stdout.write(self.style.ERROR(
                    f'  CTOMOP match that CB does NOT match (causes eligible→not_eligible in CB):'
                ))
                for a, cs, cbs, cv, cbv in ctomop_to_not_eligible:
                    self.stdout.write(f'    {a}: ctomop={cv!r}  cb={cbv!r}')

            if cb_to_potential:
                self.stdout.write(self.style.WARNING(
                    f'  CB has data that CTOMOP lacks (causes eligible→potential in CTOMOP):'
                ))
                for a, cs, cbs, cv, cbv in cb_to_potential:
                    self.stdout.write(f'    {a}: ctomop={cv!r}  cb={cbv!r}')

            if cb_to_not_eligible:
                self.stdout.write(self.style.ERROR(
                    f'  CB mismatch that CTOMOP does NOT see:'
                ))
                for a, cs, cbs, cv, cbv in cb_to_not_eligible:
                    self.stdout.write(f'    {a}: ctomop={cv!r}  cb={cbv!r}')
