"""Shadow-compare legacy vs OMOP therapy mapping (#4446).

Read-only cutover-readiness report. Run after the vocab loader + backfill in an
env to see (a) backfill/sync drift and (b) how much legacy matching can't yet be
reproduced on the omop_* columns (unmapped codes).

Ported from CancerBot (CB epic #4447); therapy-only (EXACT has not yet ported the
demographics OMOP machinery — see trials/services/omop/shadow_compare.py).

    python manage.py omop_shadow_compare [--limit N] [--disease NAME]
"""
from django.core.management.base import BaseCommand

from trials.models import Trial
from trials.services.omop.shadow_compare import compare_corpus


class Command(BaseCommand):
    help = "Report drift + cutover-divergence between legacy and omop therapy columns."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None, help="Only the first N trials (by id).")
        parser.add_argument('--disease', default=None, help="Restrict to a disease (icontains).")

    def handle(self, *args, **options):
        qs = Trial.objects.all().order_by('id')
        if options['disease']:
            qs = qs.filter(disease__icontains=options['disease'])
        if options['limit'] is not None:
            qs = qs[:options['limit']]

        r = compare_corpus(qs)

        self.stdout.write(f"scanned {r['scanned']} trial(s)")
        self.stdout.write("")
        self.stdout.write(f"BACKFILL DRIFT: {r['drifted_trials']} trial(s) have stored omop_* != computed-from-legacy")
        for col, n in sorted(r['drift_by_col'].items()):
            self.stdout.write(f"    {col}: {n}")
        if not r['drift_by_col']:
            self.stdout.write("    (none — stored omop_* matches the mapping)")
        self.stdout.write("")
        self.stdout.write(f"CUTOVER DIVERGENCE: {r['divergent_trials']} trial(s) have legacy codes with no OMOP concept")
        self.stdout.write("  (these would match differently once reads flip to the omop columns)")
        for col, n in sorted(r['unmapped_by_col'].items()):
            self.stdout.write(f"    {col}: {n} trial(s) affected")
        if r['top_unmapped_codes']:
            self.stdout.write("  top unmapped codes (by trial frequency):")
            for code, freq in r['top_unmapped_codes']:
                self.stdout.write(f"    {code}: {freq}")
