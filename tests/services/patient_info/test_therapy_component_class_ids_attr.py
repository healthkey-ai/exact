"""Unit tests for get_user_therapy_component_class_ids() (promop#370, ADR 0002).

Mirror of test_therapy_component_ids_attr — the None/[] distinction is the same
load-bearing contract for the OMOP drug-class "type" match-values consumed once
EXACT_OMOP_THERAPY_TYPES is on (#285):
  None  = field absent → fail-closed (unknown, don't filter)
  []    = explicitly empty list sent by PROMOP → known-empty, no class ids
  [ids] = known list → overlap match
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

pytestmark = pytest.mark.django_db


def _attr(**kw):
    return PatientInfoAttributes(PatientInfo(**kw))


def test_absent_returns_none():
    assert _attr().get_user_therapy_component_class_ids() is None


def test_none_value_returns_none():
    assert _attr(therapy_component_class_ids=None).get_user_therapy_component_class_ids() is None


def test_empty_list_returns_empty_list():
    result = _attr(therapy_component_class_ids=[]).get_user_therapy_component_class_ids()
    assert result == []


def test_valid_ids_returned_as_strings():
    result = _attr(therapy_component_class_ids=[35807295, 35807403]).get_user_therapy_component_class_ids()
    assert result == ['35807295', '35807403']


def test_non_digit_values_dropped():
    result = _attr(therapy_component_class_ids=[35807295, None, 'abc']).get_user_therapy_component_class_ids()
    assert result == ['35807295']
