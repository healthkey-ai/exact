"""Prune old vocab-mirror generations (#259).

Drops mirror rows + the MirrorRelease row for stale non-ACTIVE generations,
keeping the ACTIVE one, the ``--keep`` most-recent non-active generations, and
anything younger than ``--min-age-hours``. Safe to run on a schedule (Cloud
Scheduler → Cloud Run Job); holds the sync singleton advisory lock so it never
races a sync/activation.

    python manage.py reap_vocab_mirror [--keep N] [--min-age-hours H] [--dry-run]
"""
from django.core.management.base import BaseCommand

from vocab_mirror.reaper import DEFAULT_KEEP, reap_under_lock


class Command(BaseCommand):
    help = 'Prune old (non-ACTIVE, past-retention) vocab-mirror generations.'

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=DEFAULT_KEEP,
                            help='Keep this many most-recent non-active generations '
                                 f'(default {DEFAULT_KEEP}).')
        parser.add_argument('--min-age-hours', type=float, default=6.0,
                            help='Never reap a generation younger than this (default 6).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be reaped without deleting.')

    def handle(self, *args, **opts):
        from datetime import timedelta
        result = reap_under_lock(
            keep=opts['keep'],
            min_age=timedelta(hours=opts['min_age_hours']),
            dry_run=opts['dry_run'],
        )
        if result is None:
            self.stdout.write('reap skipped: another sync/reap holds the lock')
            return
        prefix = '[dry-run] ' if opts['dry_run'] else ''
        if not result:
            self.stdout.write(f'{prefix}nothing to reap')
            return
        for r in result:
            total = sum(r['rows'].values())
            self.stdout.write(
                f"{prefix}reaped release {r['release_id']} [{r['state']}] — {total} rows")
        self.stdout.write(f'{prefix}reaped {len(result)} generation(s)')
