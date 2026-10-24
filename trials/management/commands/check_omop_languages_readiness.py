"""Report whether OMOP language-skill matching can be switched on (CB #5350).

Runs the same checks as the EXACT_OMOP_LANGUAGES readiness gate, fresh (no cache),
prints the reasons and warnings, and exits non-zero when not ready.

    python manage.py check_omop_languages_readiness
"""
from django.core.management.base import BaseCommand, CommandError

from trials.services.omop.languages_readiness import check_languages_readiness


class Command(BaseCommand):
    help = "Check the OMOP language-skill data is ready for EXACT_OMOP_LANGUAGES."

    def handle(self, *args, **opts):
        report = check_languages_readiness()
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f'warning: {warning}'))
        if not report.ok:
            for reason in report.reasons:
                self.stdout.write(self.style.ERROR(f'not ready: {reason}'))
            raise CommandError('OMOP language-skill data is not ready')
        self.stdout.write(self.style.SUCCESS('ready'))
