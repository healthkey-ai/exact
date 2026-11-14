"""Albumin falls back to the LOINC-coded canonical column (#5555).

PROMOP fills `albumin_g_dl` from LOINC 1751-7; the legacy `albumin_level`
column is only reached through the loose concept-name map. Without a fallback a
patient with only the canonical fact read as unknown.
"""
from types import SimpleNamespace

import pytest

from exact_matching.patient_record import PatientRecordSurface


def _surface(**fields):
    return PatientRecordSurface(SimpleNamespace(**fields))


@pytest.mark.parametrize('fields, value, unit', [
    ({'albumin_level': 3.8, 'albumin_level_units': 'g/dL', 'albumin_g_dl': 3.5}, 3.8, 'g/dL'),
    # Direct value keeps its own paired unit, even when it differs from g/dL.
    ({'albumin_level': 38, 'albumin_level_units': 'g/L', 'albumin_g_dl': 3.5}, 38, 'g/L'),
    ({'albumin_level': None, 'albumin_level_units': 'g/dL', 'albumin_g_dl': 3.5}, 3.5, 'g/dL'),
    # Canonical fallback uses g/dL even when the stale direct unit says g/L.
    ({'albumin_level': None, 'albumin_level_units': 'g/L', 'albumin_g_dl': 3.5}, 3.5, 'g/dL'),
    ({'albumin_level': None, 'albumin_level_units': 'g/dL', 'albumin_g_dl': None}, None, 'g/dL'),
])
def test_albumin_value_and_unit(fields, value, unit):
    surface = _surface(**fields)

    assert surface.albumin_level == value
    assert surface.albumin_level_units == unit
