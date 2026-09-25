"""The erasure path that does not depend on HTTP, a token, or PROMOP.

`forget_identity` over the API is how this is meant to happen. This exists
because "meant to" is not a plan: the endpoint needs `SERVICE_AUTH_TOKEN`
configured, PROMOP to actually call it, and both to be working on the day
somebody asks to be forgotten. A right to erasure that can be blocked by a
missing environment variable is not one.
"""
from django.core.management.base import BaseCommand, CommandError

from user_state.models import forget_identity


class Command(BaseCommand):
    help = "Delete everything EXACT holds for one identity (issuer + sub)."

    def add_arguments(self, parser):
        parser.add_argument('--issuer', required=True)
        parser.add_argument('--sub', required=True)

    def handle(self, *args, **options):
        result = forget_identity(options['issuer'], options['sub'])
        if not result['found']:
            raise CommandError(
                f"No identity {options['issuer']} / {options['sub']} here. "
                "Nothing was deleted — check the issuer spelling before "
                "concluding the rows are gone."
            )
        for table, count in sorted(result['removed'].items()):
            self.stdout.write(f'{table}: {count}')
        self.stdout.write(self.style.SUCCESS('identity and tokens removed'))
