import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from trials.schema_contract import CatalogSchemaError, check_catalog_configuration, inspect_trials_schema


class Command(BaseCommand):
    help = 'Read-only schema-subset preflight against the routed trial catalog; never repairs schema.'

    def add_arguments(self, parser):
        parser.add_argument('--configuration-only', action='store_true',
                            help='Check read-only startup configuration before any bootstrap writes; no DB access.')

    def handle(self, *args, **options):
        try:
            check_catalog_configuration()
            if options['configuration_only']:
                self.stdout.write('Read-only catalog configuration: OK')
                return
            result = inspect_trials_schema()
        except CatalogSchemaError as exc:
            raise CommandError(str(exc)) from None
        except DatabaseError:
            # Do not echo DSNs, credentials, connection errors or query values.
            raise CommandError('Could not inspect the trial catalog; schema compatibility is unverified.') from None
        self.stdout.write(json.dumps(result, sort_keys=True, indent=2))
        if not result['compatible']:
            raise CommandError('Trial catalog schema is incompatible; no traffic or schema repair is authorized.')
