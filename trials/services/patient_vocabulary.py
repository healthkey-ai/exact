"""Read-only catalog capability failures shared by normalization and options."""
from django.db import ProgrammingError
from rest_framework.exceptions import APIException


class PatientVocabularyUnavailable(APIException):
    status_code = 503
    default_code = 'patient_vocabulary_unavailable'
    default_detail = {
        'code': default_code,
        'detail': 'A required patient vocabulary is unavailable. No matching results were produced.',
    }


def catalog_rows(model):
    """Read the required CB reference only; never cache failure as an empty set."""
    try:
        rows = list(model.objects.order_by('id').values('title', 'code'))
    except ProgrammingError as exc:
        # Missing table/column are capability failures. Permissions, broken
        # transactions and arbitrary DB errors are not unknown vocabulary.
        if getattr(exc.__cause__, 'pgcode', None) not in ('42P01', '42703'):
            raise
        raise PatientVocabularyUnavailable() from None
    if not rows:
        raise PatientVocabularyUnavailable()
    return rows
