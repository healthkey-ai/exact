"""Load OMOP concept_ids onto the therapy vocab models from the curated mapping.

Reads docs/omop/mapping/therapy_omop_mapping.csv (CB code -> OMOP concept_id,
produced + curated against CTOMOP, see that dir) and:
- sets omop_concept_id on Therapy / TherapyComponent / TherapyComponentCategory (#4451);
- upserts OmopConcept(concept_id -> concept_name, vocabulary_id) so the API can
  resolve the concept_ids in trial omop_* columns to OMOP titles;
- upserts TherapyOmopMapping (the per-row cb<->OMOP crosswalk, #4476) for EVERY
  CSV row, including the unmapped `needs_review` / `no_omop` rows (null
  concept_id), so coverage / SME gaps are queryable in the DB.

For the vocab-model omop_concept_id + OmopConcept writes, only rows with a
concept_id and an accepted match type are loaded; `no_omop` (procedures /
non-drug) and `needs_review` rows do not write a concept_id. If such a row's
vocab object previously had a concept_id (stale from a prior accepted run),
it is cleared to NULL so the backfill does not propagate the old value into
trial omop_* columns. The crosswalk table records all rows regardless.

Ported from CancerBot (CB epic #4447). The mapping CSV is vendored from CB so
EXACT can populate vocab concept_ids in single-DB / local runs.

    python manage.py load_therapy_omop_concept_ids [--dry-run] [--include-llm]
"""
import csv
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from trials.models import (
    Therapy, TherapyComponent, TherapyComponentCategory, OmopConcept, TherapyOmopMapping,
)

LEVEL_MODEL = {
    'regimen': Therapy,
    'component': TherapyComponent,
    'category': TherapyComponentCategory,
}
DEFAULT_CSV = os.path.join(settings.BASE_DIR, 'docs', 'omop', 'mapping', 'therapy_omop_mapping.csv')
# match types that carry a CTOMOP-verified concept_id
ACCEPTED = {'auto', 'curated', 'llm'}


class Command(BaseCommand):
    help = "Set omop_concept_id on therapy vocab models + upsert OmopConcept titles from the mapping CSV."

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
        cleared = Counter()      # level -> stale concept_ids nulled (needs_review/no_omop transition)
        missing_code = Counter()  # code not found in vocab table
        skipped = 0
        concepts = 0             # distinct OmopConcept rows upserted
        seen_concepts = set()
        crosswalk = 0            # TherapyOmopMapping rows upserted (all CSV rows)

        with open(opts['csv']) as f:
            for row in csv.DictReader(f):
                cid = int(row['omop_concept_id']) if row['omop_concept_id'] else None

                # Crosswalk (#4476): one row per (level, cb_code) for EVERY CSV
                # row — including unmapped needs_review/no_omop (null concept_id) —
                # so coverage / SME gaps are visible in the DB, not just the file.
                if not dry_run:
                    TherapyOmopMapping.objects.update_or_create(
                        level=row['level'],
                        cb_code=row['cb_code'],
                        defaults={
                            'omop_concept_id': cid,
                            'omop_name': (row.get('omop_name') or '').strip() or None,
                            'omop_vocab': (row.get('omop_vocab') or '').strip() or None,
                            'match': (row.get('match') or '').strip(),
                        },
                    )
                crosswalk += 1

                if row['match'] not in accepted or cid is None:
                    skipped += 1
                    # Clear any stale concept_id left from a previous accepted mapping.
                    # A row that transitions to needs_review/no_omop should not keep an
                    # old concept_id on the vocab model — that would cause the backfill
                    # to propagate the wrong OMOP concept into trial omop_* columns.
                    model = LEVEL_MODEL.get(row['level'])
                    if model is not None and cid is None:
                        obj = model.objects.filter(code=row['cb_code']).first()
                        if obj is not None and obj.omop_concept_id is not None:
                            cleared[row['level']] += 1
                            if not dry_run:
                                model.objects.filter(pk=obj.pk).update(omop_concept_id=None)
                    continue

                # OmopConcept (concept_id -> title/vocab): keyed by concept_id, so it
                # covers every accepted concept independent of the vocab tables.
                name = (row.get('omop_name') or '').strip()
                if name and cid not in seen_concepts:
                    seen_concepts.add(cid)
                    concepts += 1
                    if not dry_run:
                        OmopConcept.objects.update_or_create(
                            concept_id=cid,
                            defaults={
                                'concept_name': name,
                                'vocabulary_id': (row.get('omop_vocab') or '').strip() or None,
                            },
                        )

                model = LEVEL_MODEL.get(row['level'])
                if model is None:  # non-vocab level (e.g. category) — crosswalk only
                    skipped += 1
                    continue
                obj = model.objects.filter(code=row['cb_code']).first()
                if obj is None:
                    missing_code[row['level']] += 1
                    continue
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

        # Rebuild the flat component → category lookup so OMOP type matching
        # is consistent with the concept_ids just written. The loader uses
        # QuerySet.update(), which bypasses signals, so the sync must be explicit.
        from trials.services.omop.component_category_lookup import sync_component_category_lookup
        lookup_result = sync_component_category_lookup(dry_run=dry_run)

        prefix = '[dry-run] ' if dry_run else ''
        for level in ('regimen', 'component', 'category'):
            self.stdout.write(
                f"{prefix}{level:10s} set={updated[level]:3d} unchanged={unchanged[level]:3d} "
                f"code_not_found={missing_code[level]:3d}"
            )
        if sum(cleared.values()):
            self.stdout.write(f"{prefix}stale concept_ids cleared: {dict(cleared)}")
        self.stdout.write(f"{prefix}total set={sum(updated.values())} cleared={sum(cleared.values())} skipped(no-concept/review)={skipped}")
        self.stdout.write(f"{prefix}OmopConcept rows upserted={concepts}")
        self.stdout.write(f"{prefix}TherapyOmopMapping (crosswalk) rows upserted={crosswalk}")
        self.stdout.write(
            f"{prefix}ComponentCategoryOmopLookup: total={lookup_result['total']} "
            f"added={lookup_result['added']} updated={lookup_result['updated']} "
            f"removed={lookup_result['removed']} unchanged={lookup_result['unchanged']}"
        )
