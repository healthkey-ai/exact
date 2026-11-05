"""Read-only shadow comparison for the marker-level genomic matcher (#5674).

Runs the per-trial marker verdict twice on the same patient marker summary —
once on the legacy string path (``EXACT_OMOP_GENOMICS=False``) and once on the
typed-envelope path (``True``) — and reports every trial whose verdict changed.
Use it before flipping the flag; it reads no patient tables and writes nothing.

    python manage.py shadow_compare_genomic_markers \
        --molecular-markers tp53Mutation,krasMutation \
        --cytogenic-markers del17p13

The values are the canonical CB marker codes stored on the patient record.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.test import override_settings

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

FIELDS = (
    ('molecular_markers', 'molecular_markers_required',
     'omop_molecular_markers_required', 'molecular_markers_excluded'),
    ('cytogenic_markers', 'cytogenic_markers_required',
     'omop_cytogenic_markers_required', 'cytogenic_markers_excluded'),
)


def _empty_q(legacy, typed, excluded):
    return (
        (Q(**{f'{legacy}__exact': []}) | Q(**{f'{legacy}__isnull': True}))
        & (Q(**{f'{typed}__exact': []}) | Q(**{f'{typed}__isnull': True}))
        & (Q(**{f'{excluded}__exact': []}) | Q(**{f'{excluded}__isnull': True}))
    )


class Command(BaseCommand):
    help = 'Compare the legacy and typed-envelope marker verdicts; read-only.'

    def add_arguments(self, parser):
        parser.add_argument('--molecular-markers', default='',
                            help='Comma-separated canonical molecular marker codes.')
        parser.add_argument('--cytogenic-markers', default='',
                            help='Comma-separated canonical cytogenic marker codes.')
        parser.add_argument('--disease', default='multiple myeloma')
        parser.add_argument('--limit', type=int, default=20,
                            help='Sample changed trials to print.')
        parser.add_argument('--json', action='store_true',
                            help='Emit machine-readable JSON.')

    def handle(self, *args, **options):
        patient = PatientInfo(
            disease=options['disease'],
            molecular_markers=options['molecular_markers'],
            cytogenic_markers=options['cytogenic_markers'],
        )
        counts = Counter()
        changed = []
        for field, legacy, typed, excluded in FIELDS:
            empty = _empty_q(legacy, typed, excluded)
            trials = Trial.objects.exclude(empty).only(
                'id', legacy, typed, excluded,
            ).order_by('id')
            for trial in trials.iterator():
                with override_settings(EXACT_OMOP_GENOMICS=False):
                    old = UserToTrialAttrMatcher(
                        trial, patient).attr_match_status(field)
                with override_settings(EXACT_OMOP_GENOMICS=True):
                    new = UserToTrialAttrMatcher(
                        trial, patient).attr_match_status(field)
                counts[f'{field}:scanned'] += 1
                if old == new:
                    counts[f'{field}:same'] += 1
                    continue
                counts[f'{field}:changed'] += 1
                changed.append({
                    'trial_id': trial.pk, 'field': field,
                    'legacy': old, 'typed': new,
                })

        payload = {
            'counts': dict(sorted(counts.items())),
            'changed': changed[:options['limit']],
        }
        if options['json']:
            payload['warning'] = 'verdicts_differ' if changed else None
            import json
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        self.stdout.write(
            f"scanned {counts.get('molecular_markers:scanned', 0) + counts.get('cytogenic_markers:scanned', 0)} "
            f"trial row(s); changed {len(changed)}"
        )
        for row in payload['changed']:
            self.stdout.write(
                f"  trial {row['trial_id']} {row['field']}: "
                f"legacy={row['legacy']} typed={row['typed']}"
            )
        if changed:
            self.stdout.write(self.style.WARNING(
                'Verdicts differ; review them before enabling EXACT_OMOP_GENOMICS.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                'No verdict changes on this patient payload.'
            ))
