"""RBC is million/µL, not cells/L (#5555).

PROMOP's canonical `rbc_million_per_ul` is copied into the legacy
`red_blood_cell_count` column, whose default unit is CELLS/L. Pairing that number
with the default unit is a scale error: the trial thresholds are authored
on the million/µL scale (`10*12/L`).
"""
from types import SimpleNamespace

import pytest

from exact_matching.patient_record import PatientRecordSurface


def _surface(**fields):
    return PatientRecordSurface(SimpleNamespace(**fields))


@pytest.mark.parametrize('fields, value, unit', [
    # Canonical present (the normal PROMOP projection).
    ({'rbc_million_per_ul': 4.5, 'red_blood_cell_count': 4.5,
      'red_blood_cell_count_units': 'CELLS/L'}, 4.5, '10*6/uL'),
    # Canonical only, direct column empty.
    ({'rbc_million_per_ul': 4.5, 'red_blood_cell_count': None,
      'red_blood_cell_count_units': 'CELLS/L'}, 4.5, '10*6/uL'),
    # Only the legacy alias populated (a source-value-only fact).
    ({'rbc_million_per_ul': None, 'red_blood_cell_count': 4.5,
      'red_blood_cell_count_units': 'CELLS/L'}, 4.5, '10*6/uL'),
    # A pending user edit lives in the direct column while the canonical column
    # still holds the pre-edit value; the edit wins.
    ({'rbc_million_per_ul': 4.5, 'red_blood_cell_count': 3.2,
      'red_blood_cell_count_units': 'CELLS/L'}, 3.2, '10*6/uL'),
    # Neither populated: unknown, with the stored unit left alone.
    ({'rbc_million_per_ul': None, 'red_blood_cell_count': None,
      'red_blood_cell_count_units': 'CELLS/L'}, None, 'CELLS/L'),
])
def test_rbc_value_and_unit_are_paired_on_the_million_scale(fields, value, unit):
    surface = _surface(**fields)

    assert surface.red_blood_cell_count == value
    assert surface.red_blood_cell_count_units == unit
