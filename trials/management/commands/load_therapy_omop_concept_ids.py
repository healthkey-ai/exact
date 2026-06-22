"""Load OMOP concept_ids onto the therapy vocab models from the curated mapping.

Reads docs/omop/mapping/therapy_omop_mapping.csv (CB code -> OMOP concept_id,
produced + curated against CTOMOP, see that dir) and sets omop_concept_id on
Therapy / TherapyComponent / TherapyComponentCategory (#4451).

Only rows with a concept_id and an accepted match type are loaded; `no_omop`
(procedures / non-drug) and `needs_review` rows are skipped.

Ported from CancerBot (CB epic #4447). The mapping CSV is vendored from CB so
EXACT can populate vocab concept_ids in single-DB / local runs.

    python manage.py load_therapy_omop_concept_ids [--dry-run] [--include-llm]
"""
import csv
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from trials.models import Therapy, TherapyComponent, TherapyComponentCategory

LEVEL_MODEL = {
    'regimen': Therapy,
    'component': TherapyComponent,
    'category': TherapyComponentCategory,
}
DEFAULT_CSV = os.path.join(settings.BASE_DIR, 'docs', 'omop', 'mapping', 'therapy_omop_mapping.csv')
# match types that carry a CTOMOP-verified concept_id
ACCEPTED = {'auto', 'curated', 'llm'}


class Command(BaseCommand):
    help = "Set omop_concept_id on therapy vocab models from the curated mapping CSV."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")
        parser.add_argument('--csv', default=DEFAULT_CSV, help="Mapping CSV path.")
        parser.add_argument('--include-llm', dest='include_llm', action='store_true', default=True,
                            help="Load DB-verified LLM-proposed matches (default on).")
        parser.add_argument('--exclude-llm', dest='include_llm', action='store_false',
                            help="Load only exact/curated matches (skip llm).")

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        accepted = set(ACCEPTED) if opts['include_llm'] else (ACCEPTED - {'llm'})

        updated = Counter()      # level -> rows set
        unchanged = Counter()    # already had the value
        missing_code = Counter()  # code not found in vocab table
        skipped = 0

        with open(opts['csv']) as f:
            for row in csv.DictReader(f):
                if row['match'] not in accepted or not row['omop_concept_id']:
                    skipped += 1
                    continue
                model = LEVEL_MODEL[row['level']]
                obj = model.objects.filter(code=row['cb_code']).first()
                if obj is None:
                    missing_code[row['level']] += 1
                    continue
                cid = int(row['omop_concept_id'])
                if obj.omop_concept_id == cid:
                    unchanged[row['level']] += 1
                    continue
                updated[row['level']] += 1
                if not dry_run:
                    # Plain bulk write via QuerySet.update(): EXACT has no signal on
                    # these vocab models, and this load only touches omop_concept_id,
                    # so a full model save would be pointless overhead. (CB uses
                    # .update() here to skip a value-options-cache post_save signal
                    # it has; EXACT keeps the same write for consistency.)
                    model.objects.filter(pk=obj.pk).update(omop_concept_id=cid)

        prefix = '[dry-run] ' if dry_run else ''
        for level in ('regimen', 'component', 'category'):
            self.stdout.write(
                f"{prefix}{level:10s} set={updated[level]:3d} unchanged={unchanged[level]:3d} "
                f"code_not_found={missing_code[level]:3d}"
            )
        self.stdout.write(f"{prefix}total set={sum(updated.values())} skipped(no-concept/review)={skipped}")
