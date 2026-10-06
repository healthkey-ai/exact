"""Unit tests for get_user_therapy_component_ids().

The None/[] distinction is load-bearing for OMOP therapy matching:
  None  = field absent from PatientInfo → fail-closed (return 'unknown', don't filter)
  []    = explicitly empty list sent by PROMOP → known-empty, no components
  [ids] = known list → filter by overlap
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

pytestmark = pytest.mark.django_db


def _attr(**kw):
    return PatientInfoAttributes(PatientInfo(**kw))


def test_absent_returns_none():
    assert _attr().get_user_therapy_component_ids() is None


def test_none_value_returns_none():
    assert _attr(therapy_component_ids=None).get_user_therapy_component_ids() is None


def test_empty_list_returns_empty_list():
    result = _attr(therapy_component_ids=[]).get_user_therapy_component_ids()
    assert result == []


def test_valid_ids_returned_as_strings():
    result = _attr(therapy_component_ids=[1001, 1002]).get_user_therapy_component_ids()
    assert result == ['1001', '1002']


def test_non_digit_values_dropped():
    result = _attr(therapy_component_ids=[1001, None, 'abc']).get_user_therapy_component_ids()
    assert result == ['1001']
