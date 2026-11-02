"""A subform entry's number and its unit must come from the same scale.

`PatientInfoAttributes.get_value()` converts a measurement into the matcher's
unit. The subform used that converted number while `uunits` named the unit the
patient actually stored, so a record holding 120 g/L rendered as `12` beside
"G/Liter" and an editor submitted 12 into a field expecting 120.
"""
import pytest

from tests.factories import TrialFactory
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.trial_details.trial_attributes import TrialAttributes


@pytest.mark.django_db
def test_a_subform_entry_pairs_the_stored_value_with_the_stored_unit(patient_info):
    patient_info.weight = 220.46
    patient_info.weight_units = 'lb'
    normalize_patient_info(patient_info)

    entries = TrialAttributes(
        TrialFactory(bmi_min=30), patient_info,
    ).get_user_subform_attrs()
    by_field = {entry['upatientField']: entry
                for group in entries.values() for entry in group}

    weight = by_field['weight']
    # The record's own number and scale, not the matcher's converted one (100 kg).
    assert weight['value'] == pytest.approx(220.46)
    assert weight['uunits'] == 'Pounds'
    assert weight['units'] == 'Kilograms'


@pytest.mark.django_db
def test_the_matcher_value_is_still_converted(patient_info):
    """Control for the bug: this is why the subform needed the raw value."""
    patient_info.weight = 220.46
    patient_info.weight_units = 'lb'
    normalize_patient_info(patient_info)

    assert patient_info.attributes_service.get_value('weight') == pytest.approx(99.999, abs=0.01)
