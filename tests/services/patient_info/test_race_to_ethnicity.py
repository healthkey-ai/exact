"""PROMOP's race axis is the one EXACT's ULN ratios read (#5672).

PROMOP keeps `race` (White/Black/Asian/…) and `ethnicity` (Hispanic or not)
apart; EXACT has one `ethnicity` field whose codes are race categories. The
adapter used to read PROMOP's ethnicity only, so the ULN adjustment never
applied and the dialog offered a control that did not move it.
"""
from datetime import date

import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.promop_adapter import normalize_promop_row
from exact_matching.patient_info.convertors.alp_uln_calculator import AlpUlnCalculator

pytestmark = pytest.mark.django_db

HEADER = {'disease': 'multiple myeloma'}


@pytest.mark.parametrize('race,code', [
    ('White', 'caucasian_or_european'),
    ('Black or African American', 'african_or_black'),
    ('Asian', 'asian'),
    ('American Indian or Alaska Native', 'native_american'),
    ('Native Hawaiian or Other Pacific Islander', 'other'),
    ('3', 'african_or_black'),
])
def test_race_drives_the_ethnicity_the_uln_ratios_read(race, code):
    row = normalize_promop_row({**HEADER, 'race': race})

    assert row['ethnicity'] == code
    assert row['race'] == race, 'the race value stays available to the dialog'


def test_ethnicity_is_the_fallback_when_race_is_absent():
    row = normalize_promop_row({**HEADER, 'ethnicity': 'African/Black'})

    assert row['ethnicity'] == 'african_or_black'
    assert 'race' not in row


def test_alp_applies_the_race_adjustment_for_a_stored_code():
    # The calculator used title spellings only; the adapter stores codes.
    pi = PatientInfo(
        disease='multiple myeloma', ethnicity='african_or_black',
        gender='M', patient_age=40, date_of_birth=date(1985, 1, 1),
    )

    ratio = AlpUlnCalculator.call(140, pi)

    # 140 / (130 + 10), not 140 / 130.
    assert round(ratio, 4) == 1.0
