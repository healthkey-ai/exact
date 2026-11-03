"""The age row is edited through the birth date it is computed from (#5664).

`patient_age` is a number PROMOP computes from `date_of_birth`, and writing the
number directly is wrong: the next read replaces it. The row therefore opens a
dialog whose entry is the birth date itself.
"""
from datetime import date

import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.trial_details.trial_attributes import TrialAttributes
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

HEADER = {'disease': 'multiple myeloma'}
BIRTH_DATE = date(1980, 5, 20)


def _attributes(**overrides):
    pi = PatientInfo(**{**HEADER, 'patient_age': 45,
                        'date_of_birth': BIRTH_DATE,
                        'date_of_birth_precision': 'day', **overrides})
    trial = TrialFactory(age_low_limit=18)
    return TrialAttributes(trial, patient_info=pi)


def test_the_age_row_opens_on_the_birth_date():
    attrs = _attributes()
    subforms = attrs.get_user_subform_attrs()

    assert [entry['upatientField'] for entry in subforms['patient_age']] == [
        'date_of_birth', 'date_of_birth_precision',
    ]
    assert subforms['patient_age'][0]['type'] == 'date'
    assert subforms['patient_age'][0]['value'] == BIRTH_DATE


def test_the_age_rows_carry_the_dialog_and_stay_read_only_themselves():
    attrs = _attributes()
    details = attrs.get_user_details(subform_attrs=attrs.get_user_subform_attrs())

    for row_name in ('age_low_limit',):
        row = details[row_name]
        assert row['ufield'] == 'patient_age'
        assert row['ureadonly'] is True, (
            'the number is derived; the box belongs to the birth date'
        )
        entries = row['subform_details']
        assert [entry['upatientField'] for entry in entries] == [
            'date_of_birth', 'date_of_birth_precision',
        ]


def test_the_record_boundary_carries_the_birth_date():
    pi = build_patient_info_from_promop_row({
        **HEADER, 'patient_age': 45, 'date_of_birth': '1980-05-20',
        'date_of_birth_precision': 'month',
    })

    assert pi.date_of_birth == BIRTH_DATE
    assert pi.date_of_birth_precision == 'month', (
        'a year-only or month-only birth date must not be promoted by a read'
    )
    assert pi.patient_age == 45, 'the supplied age is not recomputed a second time'


def test_a_row_without_a_birth_date_still_builds_the_dialog():
    """The dialog is how the reader SUPPLIES the missing date, so its absence
    must not remove the door."""
    attrs = _attributes(date_of_birth=None)
    subforms = attrs.get_user_subform_attrs()

    assert [entry['upatientField'] for entry in subforms['patient_age']] == [
        'date_of_birth', 'date_of_birth_precision',
    ]
    assert subforms['patient_age'][0]['value'] is None
