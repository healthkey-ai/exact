"""Publish a trial-projection release attestation (CB → EXACT, #265).

CB calls this (or a future HTTP endpoint) after a release-wide backfill has stamped
every trial for release R, so EXACT's mirror activation gate can confirm the
projection agrees on R without a cross-DB read.

    python manage.py publish_projection_attestation --release-id R \
        [--run-id X] [--trial-count N] [--checksum C]
"""
from django.core.management.base import BaseCommand, CommandError

from vocab_mirror.attestation import publish_projection_attestation


class Command(BaseCommand):
    help = 'Publish a trial-projection release attestation (CB → EXACT, #265).'

    def add_arguments(self, parser):
        parser.add_argument('--release-id', type=int, required=True,
                            help='The vocab release the projection is attested for.')
        parser.add_argument('--run-id', default='', help='CB backfill run id (provenance).')
        parser.add_argument('--trial-count', type=int, default=None,
                            help='Number of trials stamped for this release.')
        parser.add_argument('--checksum', default='', help='Optional projection checksum.')

    def handle(self, *args, **opts):
        if opts['release_id'] < 0:
            raise CommandError('--release-id must be >= 0')
        obj = publish_projection_attestation(
            opts['release_id'], run_id=opts['run_id'],
            trial_count=opts['trial_count'], checksum=opts['checksum'])
        self.stdout.write(self.style.SUCCESS(
            f'attested trial projection for release {obj.release_id} '
            f'(run={obj.run_id or "-"}, trials={obj.trial_count})'))
