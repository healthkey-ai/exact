"""The other trial-detail rows that had no editing door.

- The ALP ×ULN rows read age, gender and ethnicity, so the dialog must offer
  those inputs (and the birth date age comes from) instead of listing them
  read-only.
- "Therapy Lines Count Minimum" is authored from the therapy episodes; it now
  opens the same line dialog the therapy groups use.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.trial_details.configs import SUBFORM_ATTRS_MAPPING
from trials.services.trial_details.trial_attributes import TrialAttributes
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db


def test_the_alp_uln_group_names_the_demographics_and_the_birth_date():
    expected = [
        'liver_enzyme_levels_alp', 'gender', 'ethnicity', 'patient_age',
        'date_of_birth', 'date_of_birth_precision',
    ]

    assert SUBFORM_ATTRS_MAPPING['liver_enzyme_level_alp_uln_min'] == expected
    assert SUBFORM_ATTRS_MAPPING['liver_enzyme_level_alp_uln_max'] == expected


def test_the_therapy_count_row_opens_the_line_dialog():
    pi = PatientInfo(
        disease='multiple myeloma',
        prior_therapy='One line',
        first_line_therapy='vrd',
    )
    normalize_patient_info(pi)
    trial = TrialFactory(therapy_lines_count_min=1)
    attrs = TrialAttributes(trial, patient_info=pi)

    subforms = attrs.get_user_subform_attrs()
    assert 'prior_therapy' in subforms
    fields = [entry['upatientField'] for entry in subforms['prior_therapy']]
    assert 'first_line_therapy' in fields

    details = attrs.get_user_details(subform_attrs=subforms)
    row = details['therapy_lines_count_min']
    assert row['ufield'] == 'prior_therapy'
    assert row['subform_details'], 'the count row has no door again'


def test_meets_lugano_is_an_input_not_a_computed_row():
    """Nothing in the record derives it; the clinician's answer is the value.

    The `is_computed_value` display flag made the row read-only even after
    PROMOP started accepting the boolean.
    """
    trial = TrialFactory(meets_lugano=True)
    attrs = TrialAttributes(
        trial, patient_info=PatientInfo(disease='follicular lymphoma'),
    )

    row = attrs.get_user_details(subform_attrs={})['meets_lugano']

    assert row['ureadonly'] is False
