"""Regression tests for BlankAttributeRecordsCount after the .extra() → aggregate() port (#25)."""
import pytest

from trials.models import Trial
from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount


class TestBlankAttributeRecordsCount:
    def test_none_patient_info_returns_empty(self):
        """No patient → no candidate attrs → empty dict, no DB hit."""
        result = BlankAttributeRecordsCount().counts(patient_info=None)
        assert result == {}

    @pytest.mark.django_db
    def test_empty_scope_returns_empty(self, patient_info):
        """`aggregate()` on `.none()` returns `{key: None}` for every key,
        which the final non-None filter strips to `{}`. Pre-port this path
        exited via `if not out: return {}` after `.extra(select=).values()`
        — preserve the empty-dict contract.
        """
        result = BlankAttributeRecordsCount().counts(
            scope=Trial.objects.none(), patient_info=patient_info
        )
        assert result == {}
