"""Apply trials-app migrations to the split 'trials' database.

Gated on settings.TRIALS_DB_MIGRATE so the command is a safe no-op in the
default configuration where the trials DB is an externally-managed, read-only
catalog. When the flag is off we return before touching the 'trials'
connection at all — so we never open it or create a django_migrations recorder
on a read-only / externally-owned DB. Only when the flag is on (a copy we own,
e.g. staging) do we run `migrate --database=trials`.

On staging the trials DB is CB-managed: CB's own migrations have already
created every table and column that EXACT's migrations want to add. So we need
to fake the migrations whose schema already exists rather than running them for
real. The strategy: inspect the leaf-to-root plan, attempt each pending
migration, and fake it if it fails with DuplicateTable / DuplicateColumn.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import ProgrammingError


_ALREADY_EXISTS_MARKERS = (
    'already exists',
    'DuplicateTable',
    'DuplicateColumn',
)


def _schema_already_exists(exc):
    msg = str(exc)
    return any(m in msg for m in _ALREADY_EXISTS_MARKERS)


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

        connection = connections['trials']
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes('trials')
        plan = executor.migration_plan(targets)

        if not plan:
            self.stdout.write('  No migrations to apply.')
            return

        for migration, _backwards in plan:
            name = migration.name
            self.stdout.write(f'  Applying trials.{name}...', ending=' ')
            try:
                call_command(
                    'migrate', 'trials', name,
                    database='trials', interactive=False, verbosity=0,
                )
                self.stdout.write('OK')
            except ProgrammingError as exc:
                if not _schema_already_exists(exc):
                    self.stdout.write(f'FAILED: {exc}')
                    raise
                # Schema was already created by CB (or a prior EXACT deploy) —
                # record the migration as applied without re-running the DDL.
                call_command(
                    'migrate', 'trials', name,
                    database='trials', fake=True, interactive=False, verbosity=0,
                )
                self.stdout.write('FAKED (schema pre-exists)')
