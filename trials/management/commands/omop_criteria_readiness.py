"""Report readiness for the single EXACT_OMOP_CRITERIA flag.

The all-55 typed runtime is the consumer of this flag. Therapy and language keep
their own cutover flags; every other criterion is served by the typed runtime and
fails closed (503 `typed_matching_unavailable`) until a trusted host resolver is
installed. This command makes that state explicit before anyone flips the flag.
"""
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from exact_matching.contracts.inventory import CRITERIA

THERAPY_FIELDS = frozenset({
    'therapies_required', 'therapies_excluded',
    'therapy_components_required', 'therapy_components_excluded',
    'therapy_types_required', 'therapy_types_excluded',
    'supportive_therapies_required', 'supportive_therapies_excluded',
})
LANGUAGE_FIELDS = frozenset({'languages_skills_required'})


class Command(BaseCommand):
    help = 'Report per-field readiness for EXACT_OMOP_CRITERIA; fail if enabled without a resolver.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON.')

    def handle(self, *args, **options):
        typed_enabled = bool(getattr(settings, 'EXACT_TYPED_MATCHING_ENABLED', False))
        criteria_flag = bool(getattr(settings, 'EXACT_OMOP_CRITERIA', False) or typed_enabled)
        resolver = getattr(settings, 'EXACT_TYPED_MATCHING_RESOLVER', None)
        therapy_flag = bool(getattr(settings, 'EXACT_OMOP_THERAPY', False))
        language_flag = bool(getattr(settings, 'EXACT_OMOP_LANGUAGES', False))

        # An installed resolver serves every field: the typed runtime replaces
        # the whole evaluation, including therapy and language. The per-field
        # cutover flags only decide what serves a field while that is not the
        # case.
        typed_ready = criteria_flag and resolver is not None
        criteria_fail_closed = criteria_flag and resolver is None
        rows = []
        for spec in CRITERIA:
            field = spec.field
            if typed_ready:
                ready = True
                reader = 'typed_runtime'
                reason = 'EXACT_OMOP_CRITERIA on, resolver installed'
            elif criteria_fail_closed:
                # resolve_evaluation raises before any field is evaluated, so
                # every criterion — therapy and language included — is
                # unavailable, not just the non-therapy ones.
                ready = False
                reader = 'typed_runtime'
                reason = 'EXACT_OMOP_CRITERIA on, resolver not installed (fail-closed 503)'
            elif field in THERAPY_FIELDS:
                ready = therapy_flag
                reader = 'therapy_match_profile'
                reason = 'EXACT_OMOP_THERAPY on' if ready else 'EXACT_OMOP_THERAPY off'
            elif field in LANGUAGE_FIELDS:
                ready = language_flag
                reader = 'languages_match_profile'
                reason = 'EXACT_OMOP_LANGUAGES on' if ready else 'EXACT_OMOP_LANGUAGES off'
            else:
                ready = False
                reader = 'typed_runtime'
                reason = 'EXACT_OMOP_CRITERIA off'
            rows.append({
                'field': field,
                'family': spec.family,
                'operator': spec.operator,
                'reader': reader,
                'ready': ready,
                'reason': reason,
            })

        payload = {
            'EXACT_OMOP_CRITERIA': criteria_flag,
            'EXACT_TYPED_MATCHING_ENABLED': typed_enabled,
            'resolver_installed': resolver is not None,
            'EXACT_OMOP_THERAPY': therapy_flag,
            'EXACT_OMOP_LANGUAGES': language_flag,
            'criteria_count': len(rows),
            'ready_count': sum(1 for row in rows if row['ready']),
            'fields': rows,
        }

        if options['json']:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self.stdout.write(
                f"EXACT_OMOP_CRITERIA={criteria_flag} "
                f"resolver_installed={resolver is not None} "
                f"EXACT_OMOP_THERAPY={therapy_flag} "
                f"EXACT_OMOP_LANGUAGES={language_flag}"
            )
            self.stdout.write(f"{'field':44s} {'operator':18s} {'ready':5s} {'reader':24s} reason")
            for row in rows:
                self.stdout.write(
                    f"{row['field']:44s} {row['operator']:18s} "
                    f"{str(row['ready']):5s} {row['reader']:24s} {row['reason']}"
                )
            self.stdout.write(
                f"ready {payload['ready_count']} / {payload['criteria_count']}"
            )

        if criteria_flag and resolver is None:
            raise CommandError(
                'EXACT_OMOP_CRITERIA is enabled without EXACT_TYPED_MATCHING_RESOLVER; '
                'non-therapy requests will fail closed with 503 typed_matching_unavailable.'
            )
