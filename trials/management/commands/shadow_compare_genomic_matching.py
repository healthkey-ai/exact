"""Read-only shadow comparison for the finding-level genomic matcher (#5672).

Runs the per-trial genomic verdict twice on the same patient payload — once on
the legacy string path (``EXACT_OMOP_GENOMICS=False``) and once on the
finding-level path (``True``) — and reports every trial whose verdict changed.
Use it before flipping the flag; it reads no patient tables and writes nothing.

    python manage.py shadow_compare_genomic_matching --patient-json findings.json

The file is a JSON list of finding objects (the shape PROMOP exposes):
``[{"gene": "brca1", "gene_concept_id": 35950928, "interpretation": "pathogenic"}, ...]``.
``--disease`` defaults to ``breast cancer``; ``--limit`` caps the sample.
"""
import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.test import override_settings

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

MUTATION_COLUMNS = (
    'mutation_genes_required', 'mutation_variants_required',
    'mutation_origins_required', 'mutation_interpretations_required',
    'omop_mutation_genes_required', 'omop_mutation_variants_required',
    'omop_mutation_origins_required', 'omop_mutation_interpretations_required',
)


class Command(BaseCommand):
    help = 'Compare the legacy and finding-level genomic verdicts; read-only.'

    def add_arguments(self, parser):
        parser.add_argument('--patient-json', required=True,
                            help='JSON file containing a list of finding objects.')
        parser.add_argument('--disease', default='breast cancer')
        parser.add_argument('--limit', type=int, default=20, help='Sample changed trials to print.')
        parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON.')

    def handle(self, *args, **options):
        try:
            with open(options['patient_json'], encoding='utf-8') as handle:
                findings = json.load(handle)
        except (OSError, ValueError) as error:
            raise CommandError(f'Cannot read the patient findings: {error}') from error
        if not isinstance(findings, list):
            raise CommandError('The patient JSON must be a list of finding objects.')

        patient = PatientInfo(disease=options['disease'], genetic_mutations=findings)
        empty = Q()
        for column in MUTATION_COLUMNS:
            empty &= (Q(**{f'{column}__exact': []}) | Q(**{f'{column}__isnull': True}))
        trials = Trial.objects.exclude(empty).only(
            'id', *MUTATION_COLUMNS,
        ).order_by('id')

        counts = Counter()
        changed = []
        for trial in trials.iterator():
            with override_settings(EXACT_OMOP_GENOMICS=False):
                legacy = UserToTrialAttrMatcher(trial, patient).attr_match_status('genetic_mutations')
            with override_settings(EXACT_OMOP_GENOMICS=True):
                finding_level = UserToTrialAttrMatcher(trial, patient).attr_match_status('genetic_mutations')
            counts['scanned'] += 1
            if legacy == finding_level:
                counts['same'] += 1
                continue
            counts['changed'] += 1
            changed.append({
                'trial_id': trial.pk, 'legacy': legacy, 'finding_level': finding_level,
            })

        payload = {'counts': dict(sorted(counts.items())), 'changed': changed[:options['limit']]}
        if options['json']:
            payload['warning'] = 'verdicts_differ' if counts['changed'] else None
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                f"scanned {counts['scanned']} trial(s); "
                f"same {counts['same']}; changed {counts['changed']}"
            )
            for row in payload['changed']:
                self.stdout.write(
                    f"  trial {row['trial_id']}: legacy={row['legacy']} "
                    f"finding_level={row['finding_level']}"
                )
        if not options['json']:
            if counts['changed']:
                self.stdout.write(self.style.WARNING(
                    'Verdicts differ; review them before enabling EXACT_OMOP_GENOMICS.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS('No verdict changes on this patient payload.'))
