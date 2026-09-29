"""Load OMOP concept_ids onto the language vocab models from the curated mapping.

Reads docs/omop/mapping/language_omop_mapping.csv, the same format as
therapy_omop_mapping.csv (``level,cb_code,cb_title,omop_concept_id,omop_name,
omop_vocab,match``), and sets ``omop_concept_id`` on ``Language`` (level
``language``) and ``LanguageSkillLevel`` (level ``skill``). Only rows with an
accepted match and a concept_id are loaded. Every other vocab row, whether
``no_omop``, ``needs_review`` or absent from the CSV, is cleared to NULL, so a
stale concept_id cannot reach the trial column.

Run ``backfill_omop_languages_skills_column`` afterwards: this command writes
through QuerySet.update() and fires no trial resync.

    python manage.py load_language_omop_concept_ids [--dry-run] [--csv PATH]
"""
import csv
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import router, transaction

from trials.models import Language, LanguageSkillLevel

LEVEL_MODEL = {
    'language': Language,
    'skill': LanguageSkillLevel,
}
DEFAULT_CSV = os.path.join(settings.BASE_DIR, 'docs', 'omop', 'mapping', 'language_omop_mapping.csv')
# Same accepted set as load_therapy_omop_concept_ids.
ACCEPTED = {'auto', 'curated', 'llm'}


class Command(BaseCommand):
    help = "Set omop_concept_id on Language / LanguageSkillLevel from the language mapping CSV."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")
        parser.add_argument('--csv', default=DEFAULT_CSV, help="Mapping CSV path.")

    def handle(self, *args, **opts):
        # EXACT adaptation: one transaction on the DB the vocab routes to (split-DB
        # read-only trials DB), not CB's bare atomic(), which targets ``default``.
        with transaction.atomic(using=router.db_for_write(Language) or 'default'):
            self._handle(opts)

    def _handle(self, opts):
        dry_run = opts['dry_run']

        wanted = {level: {} for level in LEVEL_MODEL}  # level -> {cb_code: concept_id}
        seen = set()
        with open(opts['csv']) as f:
            for row in csv.DictReader(f):
                model = LEVEL_MODEL.get(row['level'])
                if model is None:
                    raise CommandError(f"unknown level {row['level']!r} for {row['cb_code']!r}")
                if (row['level'], row['cb_code']) in seen:
                    raise CommandError(f"duplicate row for {row['level']}:{row['cb_code']}")
                seen.add((row['level'], row['cb_code']))
                cid = int(row['omop_concept_id']) if row['omop_concept_id'] else None
                if row['match'] in ACCEPTED and cid is not None:
                    wanted[row['level']][row['cb_code']] = cid

        stats = Counter()
        for level, model in LEVEL_MODEL.items():
            codes = wanted[level]
            for obj in model.objects.all():
                cid = codes.get(obj.code)
                if obj.omop_concept_id == cid:
                    stats[f'{level}_unchanged'] += 1
                    continue
                stats[f'{level}_{"set" if cid is not None else "cleared"}'] += 1
                if not dry_run:
                    model.objects.filter(pk=obj.pk).update(omop_concept_id=cid)
            present = set(model.objects.values_list('code', flat=True))
            stats[f'{level}_code_not_found'] = len(set(codes) - present)

        prefix = '[dry-run] ' if dry_run else ''
        for level in LEVEL_MODEL:
            self.stdout.write(
                f"{prefix}{level}: set={stats[f'{level}_set']} unchanged={stats[f'{level}_unchanged']} "
                f"cleared={stats[f'{level}_cleared']} code_not_found={stats[f'{level}_code_not_found']}"
            )
