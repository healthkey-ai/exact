"""Rebuild the ComponentCategoryOmopLookup from the local component<->category M2M graph.

Reconciles the flat ``component concept_id → CB category codes`` table with the
current ``TherapyComponentCategoryConnection`` M2M joined with
``TherapyComponent.omop_concept_id``. Run after ``load_therapy_omop_concept_ids``
or any vocab change that bypasses post_save signals (QuerySet.update, admin bulk
M2M ops). With ``--dry-run`` writes nothing and reports the drift.

    python manage.py rebuild_component_category_omop_lookup [--dry-run]

Ported from CancerBot (CB owns the upstream table; EXACT rebuilds from its local
vocab copy, which is seeded from the same CB-authored mapping CSV).
"""
from django.core.management.base import BaseCommand

from trials.services.omop.component_category_lookup import sync_component_category_lookup


class Command(BaseCommand):
    help = "Rebuild ComponentCategoryOmopLookup from the component<->category M2M + component omop_concept_id."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Report drift (stored vs computed) without writing.")

    def handle(self, *args, **opts):
        dry_run = opts['dry_run']
        r = sync_component_category_lookup(dry_run=dry_run)
        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(
            f"{prefix}lookup rows: total={r['total']} added={r['added']} "
            f"updated={r['updated']} removed={r['removed']} unchanged={r['unchanged']}"
        )
        drift = r['added'] + r['updated'] + r['removed']
        if dry_run:
            if drift:
                self.stdout.write(
                    f"DRIFT: stored lookup differs from computed in {drift} row(s) — "
                    "run without --dry-run to reconcile."
                )
            else:
                self.stdout.write("no drift — stored lookup matches the computed mapping.")
