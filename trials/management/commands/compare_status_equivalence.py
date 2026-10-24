"""Measure SQL-classifier vs matcher-verdict divergence across patients (#4832).

Runs the `status_equivalence` comparator over a set of patients against the trial
corpus, aggregates the divergence rate, and breaks it down by direction
(``sql->matcher``) and by deciding attribute — so the diverging attribute
*classes* are visible on real data before any reconcile is attempted.

Patients come from the source patient DB (like `search_trials_for_patients` /
`probe_eligibility`), or from inline JSON for a quick synthetic run.

Usage:
    python manage.py compare_status_equivalence --person-ids 20300,20298
    python manage.py compare_status_equivalence --patient-limit 25
    python manage.py compare_status_equivalence --synthetic '{"disease":"multiple myeloma","patient_age":65}'
    # scope the corpus and show per-trial rows:
    python manage.py compare_status_equivalence --patient-limit 10 --disease "multiple myeloma" --show 20
"""
import json
import os

from django.core.management.base import BaseCommand, CommandError

from trials.models import Trial
from trials.services.matching import status_equivalence as se


def _base_qs(disease=None):
    """The pre-patient trial corpus, optionally disease-scoped (as the app does)."""
    qs = Trial.objects.all()
    if disease:
        qs = qs.filter(disease__icontains=disease.lower())
    return qs


class Command(BaseCommand):
    help = 'Measure SQL-classifier vs matcher-verdict divergence across patients (#4832).'

    def add_arguments(self, parser):
        parser.add_argument('--person-ids', default='',
                            help='Comma-separated person_ids from the source patient DB.')
        parser.add_argument('--patient-limit', type=int, default=10,
                            help='Max patients to pull when --person-ids is not given.')
        parser.add_argument('--synthetic', default='',
                            help='Inline patient JSON; runs one synthetic patient (skips the source DB).')
        parser.add_argument('--disease', default='',
                            help='Scope the trial corpus to this disease (icontains).')
        parser.add_argument('--show', type=int, default=0,
                            help='Print up to N individual divergence rows.')
        parser.add_argument('--source-db-url', default=os.environ.get('PATIENT_DATABASE_URL', ''))

    def handle(self, *args, **options):
        patients = list(self._load_patients(options))
        if not patients:
            raise CommandError('No patients to compare. Pass --synthetic or --person-ids / --patient-limit.')

        summary = se.ComparisonSummary()
        shown = 0
        for label, pi in patients:
            disease = options['disease'] or getattr(pi, 'disease', None)
            base_qs = _base_qs(disease)
            n = base_qs.count()
            divs = se.compare(base_qs, pi)
            summary.add(trials_compared=n, divs=divs)
            self.stdout.write(f'{label}: {len(divs)} / {n} divergences')
            for d in divs:
                if shown >= options['show']:
                    break
                self.stdout.write(
                    f'    trial={d.trial_id} {d.direction} '
                    f'matcher_not_matched={d.matcher_not_matched_attrs} '
                    f'matcher_unknown={d.matcher_unknown_attrs[:6]} '
                    f'sql_dropped={d.sql_dropped_attrs} sql_potential={d.sql_potential_attrs}'
                )
                shown += 1

        self.stdout.write('\n=== SUMMARY ===')
        self.stdout.write(f'patients={len(patients)} trials_compared={summary.trials_compared} '
                          f'divergences={summary.divergences} rate={summary.rate:.4%}')
        self.stdout.write(f'by_direction: {dict(summary.by_direction)}')
        top = dict(sorted(summary.by_attr.items(), key=lambda kv: -kv[1])[:20])
        self.stdout.write(f'top deciding attrs: {top}')

    def _load_patients(self, options):
        if options['synthetic']:
            from trials.services.patient_info.resolve import _build_in_memory
            data = json.loads(options['synthetic'])
            yield (f"synthetic[{data.get('disease', '?')}]", _build_in_memory(data))
            return

        # Real patients from the source DB — same path as search_trials_for_patients.
        from trials.management.commands.search_trials_for_patients import _build_patient_info
        from trials.management.commands.compare_trials import _psql_query_rows
        db_url = options['source_db_url']
        if not db_url:
            raise CommandError('Set PATIENT_DATABASE_URL or pass --source-db-url (or use --synthetic).')

        person_ids = [x.strip() for x in options['person_ids'].split(',') if x.strip()]
        where = f"WHERE p.person_id IN ({', '.join(str(int(x)) for x in person_ids)})" if person_ids else ''
        limit = '' if person_ids else f'LIMIT {int(options["patient_limit"])}'
        rows = _psql_query_rows(db_url, f'''
            SELECT pi.*, p.given_name, p.family_name, p.gender_source_value, p.gender_concept_id
            FROM patient_info pi JOIN person p ON pi.person_id = p.person_id
            {where} ORDER BY p.person_id {limit}
        ''')
        for row in rows:
            row = dict(row)
            pi = _build_patient_info(row)
            yield (f"person_id={row.get('person_id')}", pi)
