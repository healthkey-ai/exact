"""Audit the trial molecular/cytogenic marker envelopes (#5674).

The typed columns are already populated for most marker requirements, but the
legacy lists and the envelopes are not guaranteed to agree. This read-only
command reports, per field:

- ``ok``                 a valid envelope whose concepts are in the reviewed map;
- ``missing_envelope``   a legacy required marker with no typed envelope
                         (per-trial fallback; the 40 known molecular gaps);
- ``missing_excluded_envelope`` a legacy excluded-only marker with no typed
                         envelope (the 147 gaps that carry no required marker);
- ``orphan_envelope``    an envelope with no legacy requirement;
- ``malformed_envelope`` a non-envelope/structurally invalid value;
- ``unresolved_code``    an unresolved code outside the reviewed set
                         (``bcmaExpression`` is the known one);
- ``unmapped_concept``   a feature/assertion concept absent from the reviewed
                         code -> concept table;
- ``contradiction``      the same legacy code in required and excluded.

It writes nothing. ``--strict`` exits 1 for the structural problems
(malformed/unresolved/unmapped/contradiction); ``missing_envelope`` is a
reported gap because the evaluator falls back per trial, not a structural
failure.

``unmapped_concept`` checks that the envelope's concept is in the reviewed
table, not that it matches the legacy code's own identity. A TP53 legacy row
whose envelope names ATR is still ``ok`` for mapping coverage; code-level
identity is a separate review. The ``contradiction`` status is the
required/excluded overlap, a different data issue.
"""
import json
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from exact_matching.omop.genomic_markers import _term_concept, _valid_typed
from exact_matching.omop.marker_concepts import (
    MARKER_FIELDS, TRIAL_MARKER_CONCEPTS, UNRESOLVED_MARKER_CODES,
)
from trials.models import Trial

STRICT_STATUSES = frozenset({
    'malformed_envelope', 'unresolved_code', 'unmapped_concept', 'contradiction',
})
KNOWN_CONCEPTS = frozenset(
    concept for concepts in TRIAL_MARKER_CONCEPTS.values() for concept in concepts
)


def _envelope_concepts(typed):
    concepts = set()
    for requirement in typed or []:
        for term in requirement.get('terms') or []:
            concept = _term_concept(term)
            if concept is not None:
                concepts.add(concept)
    return concepts


def _envelope_unresolved(typed):
    return {
        str(code).strip()
        for requirement in typed or []
        for code in (requirement.get('unresolved_codes') or [])
        if str(code).strip()
    }


def audit_field(trial, field):
    legacy_attr, typed_attr, excluded_attr = MARKER_FIELDS[field]
    legacy = list(getattr(trial, legacy_attr, None) or [])
    excluded = list(getattr(trial, excluded_attr, None) or [])
    typed = getattr(trial, typed_attr, None)

    if set(legacy) & set(excluded):
        return 'contradiction'
    if typed is None or typed == []:
        if legacy:
            return 'missing_envelope'
        if excluded:
            return 'missing_excluded_envelope'
        return None
    if not isinstance(typed, list) or not _valid_typed(typed):
        return 'malformed_envelope'
    unresolved = _envelope_unresolved(typed)
    if unresolved - UNRESOLVED_MARKER_CODES:
        return 'unresolved_code'
    concepts = _envelope_concepts(typed)
    if concepts - KNOWN_CONCEPTS:
        return 'unmapped_concept'
    if not legacy and not excluded:
        return 'orphan_envelope'
    return 'ok'


class Command(BaseCommand):
    help = 'Audit trial omop_molecular/cytogenic marker envelopes; read-only.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON.')
        parser.add_argument('--limit', type=int, default=10, help='Sample trial ids per status.')
        parser.add_argument('--strict', action='store_true',
                            help='Exit 1 on malformed/unresolved/unmapped/contradiction.')

    def handle(self, *args, **options):
        counts = Counter()
        samples = {}
        scanned = 0
        rows = Trial.objects.only(
            'id',
            'molecular_markers_required', 'molecular_markers_excluded',
            'omop_molecular_markers_required',
            'cytogenic_markers_required', 'cytogenic_markers_excluded',
            'omop_cytogenic_markers_required',
        ).iterator()
        for trial in rows:
            for field in MARKER_FIELDS:
                status = audit_field(trial, field)
                if status is None:
                    continue
                scanned += 1
                counts[status] += 1
                samples.setdefault(status, []).append(f'{trial.pk}:{field}')

        payload = {
            'scanned': scanned,
            'statuses': dict(sorted(counts.items())),
            'samples': {
                status: ids[:options['limit']] for status, ids in sorted(samples.items())
            },
        }
        problems = sum(count for status, count in counts.items()
                       if status in STRICT_STATUSES)
        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            if options['strict'] and problems:
                raise CommandError(
                    f'{problems} marker envelope(s) are structurally not ready; '
                    'fix or report them before relying on the typed marker path.'
                )
            return

        self.stdout.write(f'scanned {scanned} marker criterion row(s)')
        for status, count in sorted(counts.items()):
            self.stdout.write(
                f'{status}: {count} (e.g. {samples[status][:options["limit"]]})'
            )
        if not counts:
            self.stdout.write('no trial carries a marker requirement')

        if options['strict'] and problems:
            raise CommandError(
                f'{problems} marker envelope(s) are structurally not ready; '
                'fix or report them before relying on the typed marker path.'
            )
        if not options['strict']:
            self.stdout.write(self.style.SUCCESS(
                'Report only; rerun with --strict to fail on structural problems.'
            ))
