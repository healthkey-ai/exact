"""Backfill the trial OMOP demographics columns from the vocab/constant mappings.

Idempotent batch fill of `omop_ethnicity_required` / `omop_gender_concept_id`
(#4447 PR 2) from the Ethnicity vocab's omop_concept_id (#4461) and
GENDER_OMOP_CONCEPT_ID. Real writes go through the shared locked helper so the
backfill serializes with concurrent saves.

Ported from CancerBot (CB epic #4447). In split-DB production CB owns the trials
table and ships the populated columns; EXACT runs this in single-DB / local mode.

    python manage.py backfill_omop_demographics_columns [--dry-run] [--limit N]
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trials.models import Trial
from trials.services.omop.demographics import build_omop_demographics, OMOP_DEMOGRAPHICS_COLUMNS
from trials.services.omop.demographics_sync import sync_trial_omop_demographics


class Command(BaseCommand):
    help = "Backfill trial omop_ethnicity_required / omop_gender_concept_id."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")
        parser.add_argument('--limit', type=int, default=None, help="Only the first N trials (by id).")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        qs = Trial.objects.all().order_by('id')
        if limit is not None:
            qs = qs[:limit]

        scanned = updated = 0
        ethnicity_concepts = 0
        gender_set = 0
        unmapped_codes = Counter()

        def tally(values, unmapped):
            nonlocal ethnicity_concepts, gender_set
            ethnicity_concepts += len(values['omop_ethnicity_required'])
            if values['omop_gender_concept_id'] is not None:
                gender_set += 1
            for code in unmapped:
                unmapped_codes[code] += 1

        if dry_run:
            for trial in qs.iterator():
                scanned += 1
                values, unmapped = build_omop_demographics(trial)
                tally(values, unmapped)
                if any(getattr(trial, c) != values[c] for c in OMOP_DEMOGRAPHICS_COLUMNS):
                    updated += 1
        else:
            for trial_id in list(qs.values_list('id', flat=True)):
                scanned += 1
                values, unmapped, changed = sync_trial_omop_demographics(trial_id)
                if values is None:  # deleted mid-run
                    continue
                tally(values, unmapped)
                if changed:
                    updated += 1

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(f"{prefix}scanned {scanned} trial(s); {'would change' if dry_run else 'updated'} {updated}")
        self.stdout.write(f"  ethnicity concept_ids written: {ethnicity_concepts}; gender set: {gender_set}")
        if unmapped_codes:
            total = sum(unmapped_codes.values())
            self.stdout.write(f"  unmapped ethnicity codes dropped: {total} ({dict(unmapped_codes)})")
