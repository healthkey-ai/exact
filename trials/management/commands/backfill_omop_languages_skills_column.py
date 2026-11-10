"""Backfill the trial ``omop_languages_skills_required`` column (#5350).

Idempotent batch fill from the (language, skill) concept_ids on the language
vocab rows; run ``load_language_omop_concept_ids`` first. Real writes go
through the shared locked helper so the backfill serializes with concurrent
saves. ``--dry-run`` lists every stored code that has no pair, which is the
audit of what the column actually holds.

    python manage.py backfill_omop_languages_skills_column [--dry-run] [--limit N]
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trials.models import Trial
from trials.services.omop.languages import build_omop_languages_skills, load_concept_ids, OMOP_LANGUAGES_COLUMNS
from trials.services.omop.languages_sync import sync_trial_omop_languages


class Command(BaseCommand):
    help = "Backfill trial omop_languages_skills_required."

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
        pairs_written = 0
        emptied = 0  # had a requirement, maps to none: would read as "no requirement"
        unmapped_codes = Counter()

        def tally(values, unmapped):
            nonlocal pairs_written, emptied
            pairs = values['omop_languages_skills_required']
            pairs_written += len(pairs)
            if unmapped and not pairs:
                emptied += 1
            for code in unmapped:
                unmapped_codes[code] += 1

        concept_ids = load_concept_ids()
        if not all(concept_ids):
            self.stdout.write(self.style.WARNING(
                "  language vocab has no OMOP concept_ids loaded for its languages or its skills; "
                "every pair will be unmapped. Run load_language_omop_concept_ids first."))

        if dry_run:
            for trial in qs.iterator():
                scanned += 1
                values, unmapped = build_omop_languages_skills(trial, concept_ids)
                tally(values, unmapped)
                if any(getattr(trial, c) != values[c] for c in OMOP_LANGUAGES_COLUMNS):
                    updated += 1
        else:
            for trial_id in list(qs.values_list('id', flat=True)):
                scanned += 1
                values, unmapped, changed = sync_trial_omop_languages(trial_id, concept_ids)
                if values is None:  # deleted mid-run
                    continue
                tally(values, unmapped)
                if changed:
                    updated += 1

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(f"{prefix}scanned {scanned} trial(s); {'would change' if dry_run else 'updated'} {updated}")
        self.stdout.write(f"  language-skill pairs written: {pairs_written}")
        if unmapped_codes:
            total = sum(unmapped_codes.values())
            self.stdout.write(f"  unmapped language-skill codes dropped: {total} ({dict(unmapped_codes)})")
        if emptied:
            self.stdout.write(
                f"  trials whose language requirement maps to no pair (empty = no requirement at cutover): {emptied}"
            )
