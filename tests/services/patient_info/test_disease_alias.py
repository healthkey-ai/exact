"""PROMOP sends the raw SNOMED concept name for the disease.

The trial corpus stores ``breast cancer`` and the list filter is
``disease__iexact``, so an unaliased ``Malignant tumor of breast`` matched no
trials at all. Unknown diseases must pass through untouched.
"""
import pytest

from trials.services.patient_info.promop_adapter import normalize_promop_row

# The session fixture seeds reference data, and pytest-django only installs the
# test database for a test that asks for it; the disease path itself is pure.
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('raw', [
    'Malignant tumor of breast',
    'malignant tumor of breast',
    '  Malignant tumor of breast  ',
    'Malignant tumor of breast (disorder)',
    'Malignant neoplasm of breast',
    'Carcinoma of breast',
    'Primary malignant neoplasm of breast',
    'Malignant neoplasm of female breast',
])
def test_breast_surface_names_map_to_the_corpus_title(raw):
    assert normalize_promop_row({'disease': raw})['disease'] == 'Breast Cancer'


def test_alias_is_idempotent():
    once = normalize_promop_row({'disease': 'Malignant tumor of breast'})
    twice = normalize_promop_row(dict(once))
    assert twice['disease'] == 'Breast Cancer'


@pytest.mark.parametrize('raw', [
    'Multiple Myeloma',
    'Follicular Lymphoma',
    'Mantle Cell Lymphoma',
    'Chronic Lymphocytic Leukemia',
])
def test_other_diseases_pass_through(raw):
    assert normalize_promop_row({'disease': raw})['disease'] == raw


def test_missing_disease_is_left_absent():
    assert 'disease' not in normalize_promop_row({})
