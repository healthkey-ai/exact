"""Audit the trial gene envelope before flipping EXACT_OMOP_GENOMICS (#5673).

The typed ``omop_mutation_genes_required`` column is written by CB. EXACT reads
it; it must not guess. This command checks every trial that carries a legacy
gene requirement (or an envelope without one) against the reviewed HGNC/OMOP
Genomic crosswalk:

- ``ok``               the envelope ids are exactly the legacy symbols' ids;
- ``orphan_envelope``  an envelope with no legacy requirement;
- ``unmapped_symbol``  a legacy symbol absent from the reviewed map;
- ``missing_envelope`` no parseable envelope;
- ``mismatch``         envelope ids differ from the legacy symbols, or include
                       an excluded gene this gene-only slice cannot honour.

Exit 1 on anything but ``ok``/no requirement, so the flag cannot be flipped on
a silent partial backfill.
"""
import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from exact_matching.omop.genomics import legacy_gene_ids, trial_gene_requirements
from trials.models import Trial


class Command(BaseCommand):
    help = 'Audit trial omop_mutation_genes_required envelopes against the reviewed gene map.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON.')
        parser.add_argument('--limit', type=int, default=10, help='Sample trial ids per problem.')

    def handle(self, *args, **options):
        counts = Counter()
        samples = {}
        rows = Trial.objects.only(
            'id', 'mutation_genes_required', 'omop_mutation_genes_required',
        ).iterator()
        for trial in rows:
            legacy = list(trial.mutation_genes_required or [])
            envelopes = trial.omop_mutation_genes_required
            has_envelope = envelopes not in (None, [], '')
            parsed = trial_gene_requirements(trial)
            if not legacy:
                if parsed and (parsed[0] or parsed[1]):
                    status = 'orphan_envelope'
                elif has_envelope and parsed is None:
                    # A non-empty value that is not an envelope (a dict, a
                    # scalar, a malformed list) is a data error even though it
                    # carries no legacy requirement.
                    status = 'malformed_envelope'
                else:
                    continue
            else:
                expected = legacy_gene_ids(legacy)
                if expected is None:
                    status = 'unmapped_symbol'
                elif parsed is None:
                    status = 'malformed_envelope' if has_envelope else 'missing_envelope'
                elif parsed[0] != expected or parsed[1]:
                    status = 'mismatch'
                else:
                    status = 'ok'
            counts[status] += 1
            samples.setdefault(status, []).append(trial.pk)

        payload = {
            'statuses': dict(sorted(counts.items())),
            'samples': {status: ids[:options['limit']] for status, ids in sorted(samples.items())},
        }
        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for status, count in sorted(counts.items()):
                self.stdout.write(f'{status}: {count} (e.g. {samples[status][:options["limit"]]})')
            if not counts:
                self.stdout.write('no trial carries a genomic gene requirement')

        problems = sum(count for status, count in counts.items() if status != 'ok')
        if problems:
            raise CommandError(
                f'{problems} trial gene envelope(s) are not ready for EXACT_OMOP_GENOMICS; '
                'run the CB backfill or fix the legacy symbols, then rerun.'
            )
        self.stdout.write(self.style.SUCCESS('All trial gene envelopes match the reviewed gene map.'))
