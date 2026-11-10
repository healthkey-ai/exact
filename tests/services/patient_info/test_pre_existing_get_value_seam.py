"""QR4d §3d / L7 — host-agnostic get_value('pre_existing_condition_categories').

The package must not silently blank answered pre-existing conditions when a
drained CB PatientInfoAttributes wraps a CB PatientInfo, which exposes a Django
related manager (values_list('category__code')) rather than the pre-populated
`_pre_existing_condition_categories` list EXACT's API consumer sets. Cover all
four shapes.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

pytestmark = pytest.mark.django_db


class _Code:
    def __init__(self, code):
        self.code = code


class _FakeRelatedManager:
    """Mimics CB's PatientInfo.pre_existing_condition_categories related manager."""
    def __init__(self, codes):
        self._codes = codes

    def values_list(self, field, flat=False):
        assert field == 'category__code' and flat is True
        return list(self._codes)


def _get(pi):
    return PatientInfoAttributes(pi).get_value('pre_existing_condition_categories')


def test_no_pre_existing_conditions_returns_none_sentinel():
    assert _get(PatientInfo(no_pre_existing_conditions=True)) == ['none']


def test_exact_prepopulated_list_shape():
    pi = PatientInfo(no_pre_existing_conditions=False)
    pi._pre_existing_condition_categories = [_Code('diabetes'), _Code('hypertension')]
    assert _get(pi) == ['diabetes', 'hypertension']


def test_cb_related_manager_shape_is_not_silently_blanked():
    # The §3d/L7 regression: a CB-shaped patient (related manager, no pre-populated
    # list) must yield its codes, not [].
    pi = PatientInfo(no_pre_existing_conditions=False)
    pi.pre_existing_condition_categories = _FakeRelatedManager(['diabetes', 'copd'])
    assert _get(pi) == ['diabetes', 'copd']


def test_cb_related_manager_codes_are_memoized():
    pi = PatientInfo(no_pre_existing_conditions=False)
    pi.pre_existing_condition_categories = _FakeRelatedManager(['diabetes'])
    assert _get(pi) == ['diabetes']
    assert pi._pre_existing_condition_codes_cache == ['diabetes']


def test_no_source_falls_back_to_empty():
    pi = PatientInfo(no_pre_existing_conditions=False)
    # No _pre_existing_condition_categories and no values_list-bearing related manager.
    assert _get(pi) == []
