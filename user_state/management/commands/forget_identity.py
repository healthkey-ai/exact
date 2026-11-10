"""The erasure path that does not depend on HTTP, a token, or PROMOP.

`forget_identity` over the API is how this is meant to happen. This exists
because "meant to" is not a plan: the endpoint needs `SERVICE_AUTH_TOKEN`
configured, PROMOP to actually call it, and both to be working on the day
somebody asks to be forgotten. A right to erasure that can be blocked by a
missing environment variable is not one.
"""
from django.core.management.base import BaseCommand

from user_state.models import forget_identity


class Command(BaseCommand):
    help = "Delete everything EXACT holds for one identity (issuer + sub)."

    def add_arguments(self, parser):
        parser.add_argument('--issuer', required=True)
        parser.add_argument('--sub', required=True)

    def handle(self, *args, **options):
        result = forget_identity(options['issuer'], options['sub'])
        if not result['found']:
            # Success, not an error. The end state asked for is "nothing
            # here", and that is the end state. An erasure runbook loops over
            # subjects and re-runs after a failure; aborting on the first one
            # already done would be the command working against its only use.
            # It cannot mean "check your spelling" either — see the receipt
            # contract in `forget_identity`: after an erasure there is nothing
            # left to tell a typo from a job well done, deliberately.
            self.stdout.write('nothing here for that identity')
            return
        for table, count in sorted(result['removed'].items()):
            self.stdout.write(f'{table}: {count}')
        self.stdout.write(self.style.SUCCESS('identity and tokens removed'))
