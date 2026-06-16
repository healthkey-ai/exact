"""Apply trials-app migrations to the split 'trials' database.

Gated on settings.TRIALS_DB_MIGRATE so the command is a safe no-op in the
default configuration where the trials DB is an externally-managed, read-only
catalog. When the flag is off we return before touching the 'trials'
connection at all — so we never open it or create a django_migrations recorder
on a read-only / externally-owned DB. Only when the flag is on (a copy we own,
e.g. staging) do we run `migrate --database=trials`.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Migrate trials-app schema onto the 'trials' DB when TRIALS_DB_MIGRATE is enabled."

    def handle(self, *args, **options):
        if not getattr(settings, 'TRIALS_DB_MIGRATE', False):
            self.stdout.write('TRIALS_DB_MIGRATE disabled; skipping trials DB migrate.')
            return
        if 'trials' not in settings.DATABASES:
            self.stdout.write("No 'trials' database configured; skipping trials DB migrate.")
            return
        self.stdout.write("Migrating trials app onto the 'trials' database...")
        # fake_initial: the trials copy is seeded with the 0001 tables already
        # present but no migration history; fake 0001 and apply the rest.
        call_command('migrate', 'trials', database='trials', fake_initial=True, interactive=False)
