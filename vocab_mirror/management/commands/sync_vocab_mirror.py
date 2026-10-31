"""Sync the local OMOP vocabulary mirror from promop (promop#334, #250).

Intended to run as a scheduled **Cloud Run Job** (not inside a web request) —
Cloud Scheduler triggers it every ~6h in prod / ~1h in staging. It is safe to
run concurrently: the sync holds a Postgres advisory lock, so a second
invocation exits cleanly rather than double-loading.
"""
from django.core.management.base import BaseCommand

from vocab_mirror.sync import DEFAULT_TABLES, sync_vocab_mirror


class Command(BaseCommand):
    help = 'Sync the local OMOP vocabulary mirror from promop (promop#334).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-activate', action='store_true',
            help='Load + verify the new release but do not activate it.')
        parser.add_argument(
            '--tables', nargs='+', choices=DEFAULT_TABLES, metavar='TABLE',
            help='Sync only these table slugs (default: all).')

    def handle(self, *args, **options):
        outcome = sync_vocab_mirror(
            tables=options.get('tables') or None,
            activate=not options['no_activate'],
        )
        msg = f'vocab sync: {outcome.status}'
        if outcome.release_id is not None:
            msg += f' release={outcome.release_id}'
        if outcome.counts:
            msg += f' counts={outcome.counts}'
        self.stdout.write(self.style.SUCCESS(msg))
