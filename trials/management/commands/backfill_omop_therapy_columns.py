"""Backfill the trial OMOP therapy columns from the vocab concept_ids (#4455).

Idempotent batch fill of the trial ``omop_*`` therapy columns (#4453) from the
vocab ``omop_concept_id`` mapping (#4451), using the shared conversion service.
Until the PROMOP release is pinned and vocab concept_ids are populated this runs
cleanly but writes empty arrays.

Ported from CancerBot (CB epic #4447). In split-DB production CB owns the trials
table and ships the populated columns; EXACT runs this in single-DB / local mode.

    python manage.py backfill_omop_therapy_columns [--dry-run] [--limit N]
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trials.models import Trial
from trials.services.omop.therapy_concept_mapper import build_omop_columns, OMOP_COLUMNS
from trials.services.omop.therapy_sync import sync_trial_omop_columns


class Command(BaseCommand):
    help = "Backfill trial omop_* therapy columns from vocab omop_concept_id mappings."

    @staticmethod
    def _accumulate(values, unmapped, mapped_concepts, unmapped_codes):
        for col, concept_ids in values.items():
            mapped_concepts[col] += len(concept_ids)
        for col, codes in unmapped.items():
            unmapped_codes[col] += len(codes)

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Compute and report changes without writing.")
        parser.add_argument('--limit', type=int, default=None,
                            help="Only process the first N trials (by id).")

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        qs = Trial.objects.all().order_by('id')
        if limit is not None:
            qs = qs[:limit]

        scanned = 0
        updated = 0
        mapped_concepts = Counter()   # omop_column -> count of concept_ids written
        unmapped_codes = Counter()    # legacy_column -> count of dropped codes

        if dry_run:
            for trial in qs.iterator():
                scanned += 1
                values, unmapped = build_omop_columns(trial)
                self._accumulate(values, unmapped, mapped_concepts, unmapped_codes)
                if any(getattr(trial, col) != values[col] for col in OMOP_COLUMNS):
                    updated += 1
        else:
            # Write through the shared locked helper (select_for_update per trial)
            # so the backfill serializes with concurrent saves — neither can clobber
            # the other with a stale result. Fetch ids first so the per-trial
            # transactions don't fight a streaming cursor.
            for trial_id in list(qs.values_list('id', flat=True)):
                scanned += 1
                values, unmapped, changed = sync_trial_omop_columns(trial_id)
                if values is None:  # trial deleted mid-run
                    continue
                self._accumulate(values, unmapped, mapped_concepts, unmapped_codes)
                if changed:
                    updated += 1

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(f"{prefix}scanned {scanned} trial(s); {updated} would change" if dry_run
                          else f"scanned {scanned} trial(s); updated {updated}")

        total_mapped = sum(mapped_concepts.values())
        total_unmapped = sum(unmapped_codes.values())
        self.stdout.write(f"  concept_ids written: {total_mapped}")
        if total_unmapped:
            self.stdout.write(f"  unmapped codes dropped: {total_unmapped}")
            for col in sorted(unmapped_codes):
                self.stdout.write(f"    {col}: {unmapped_codes[col]}")
