"""Set omop_concept_id on the Ethnicity vocab model from the curated mapping.

Loads ETHNICITY_OMOP_CONCEPT_ID (CB code -> OMOP race concept_id, verified in
CTOMOP) onto Ethnicity.omop_concept_id. Codes not in the mapping (e.g. 'other')
are left unmapped. Gender is a fixed enum, not a vocab table, so it has no
loader — its mapping is consumed directly by the trial-side backfill.

Ported from CancerBot (CB epic #4447).

    python manage.py load_ethnicity_omop_concept_ids [--dry-run]
"""
from django.core.management.base import BaseCommand

from trials.models import Ethnicity
from trials.services.omop.demographics import ETHNICITY_OMOP_CONCEPT_ID


class Command(BaseCommand):
    help = "Set omop_concept_id on Ethnicity from the curated demographics mapping."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Report without writing.")

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        updated = unchanged = missing = cleared = 0

        for code, cid in ETHNICITY_OMOP_CONCEPT_ID.items():
            obj = Ethnicity.objects.filter(code=code).first()
            if obj is None:
                missing += 1
                continue
            if obj.omop_concept_id == cid:
                unchanged += 1
                continue
            updated += 1
            if not dry_run:
                # update() to avoid any save-signal side effects
                Ethnicity.objects.filter(pk=obj.pk).update(omop_concept_id=cid)

        # The curated map is authoritative: any code NOT in it must stay unmapped.
        # Clear stale concept_ids (e.g. a code dropped from the map, or a pre-existing
        # value on 'other') so build_omop_demographics never treats it as mapped.
        stale = Ethnicity.objects.exclude(
            code__in=ETHNICITY_OMOP_CONCEPT_ID.keys()
        ).filter(omop_concept_id__isnull=False)
        cleared = stale.count()
        if cleared and not dry_run:
            stale.update(omop_concept_id=None)

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(
            f"{prefix}ethnicity: set={updated} unchanged={unchanged} cleared={cleared} "
            f"code_not_found={missing} (codes outside the curated map are left unmapped)"
        )
