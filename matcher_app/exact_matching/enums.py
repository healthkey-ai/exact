from enum import StrEnum

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class RunStatus(TextChoices):
    NEW = 'new', _('NEW')
    IN_PROGRESS = 'in_progress', _('IN_PROGRESS')
    LOADING = 'loading', _('LOADING')
    COMPLETE = 'complete', _('COMPLETE')
    ERROR = 'error', _('ERROR')
    QUEUED = 'queued', _('QUEUED')
    UNKNOWN = 'unknown', _('UNKNOWN')
    RUNNING = 'running', _('RUNNING')
    SUCCESS = 'success', _('SUCCESS')
    FAILED = 'failed', _('FAILED')
    CANCELLED = 'cancelled', _('CANCELLED')
    SCHEDULED = 'scheduled', _('SCHEDULED')


class PriorTherapyLines(StrEnum):
    ZERO = "zero"
    AT_LEAST_ONE = "at_least_one"
    AT_MOST_ONE = "at_most_one"
    EXACTLY_ONE = "exactly_one"
    AT_LEAST_TWO = "at_least_two"
    AT_MOST_TWO = "at_most_two"
    EXACTLY_TWO = "exactly_two"
    AT_LEAST_THREE = "at_least_three"

    @classmethod
    def choices(cls) -> list[tuple[str, str]]:
        return [(key.value, key.name) for key in cls]
