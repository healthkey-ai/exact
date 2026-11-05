"""A trial row's patient value and its unit must come from the same scale.

`get_value()` converts a measurement into the matcher's unit, while `uunits`
names the unit the patient stored. Pairing them printed a record holding
120 g/L as `12 g/Liter`, and a writable row would seed its editor with the
converted number.
"""
import pytest

from tests.factories import TrialFactory
from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.trial_details.trial_attributes import TrialAttributes


@pytest.mark.django_db
def test_a_row_pairs_the_stored_value_with_the_stored_unit(patient_info):
    patient_info.weight = 220.46
    patient_info.weight_units = 'lb'
    normalize_patient_info(patient_info)

    row = TrialAttributes(
        TrialFactory(weight_min=50), patient_info,
    ).details()['weightMin']

    assert row['uvalue'] == pytest.approx(220.46)
    assert row['uunits'] == 'Pounds'
    assert row['units'] == 'Kilograms'


@pytest.mark.django_db
def test_a_converted_lab_row_shows_the_stored_scale(patient_info):
    patient_info.serum_creatinine_level = 88.4
    patient_info.serum_creatinine_level_units = 'MICROMOLES/L'
    normalize_patient_info(patient_info)

    row = TrialAttributes(
        TrialFactory(serum_creatinine_level_abs_min=1.5), patient_info,
    ).details()['serumCreatinineLevelAbsMin']

    assert row['uvalue'] == pytest.approx(88.4)
    assert row['uunits'] == 'micromoles/L'
    assert row['units'] == 'mg/dL'


@pytest.mark.django_db
def test_a_uln_row_does_not_borrow_the_physical_unit(patient_info):
    # The ×ULN row's patient value is a RATIO (1.0), not a creatinine level.
    # Labelling it "micromoles/L" would describe a number it does not hold.
    patient_info.serum_creatinine_level = 88.4
    patient_info.serum_creatinine_level_units = 'MICROMOLES/L'
    normalize_patient_info(patient_info)

    row = TrialAttributes(
        TrialFactory(serum_creatinine_level_uln_min=1.0), patient_info,
    ).details()['serumCreatinineLevelUlnMin']

    assert 'uunits' not in row
    assert 'units' not in row


@pytest.mark.django_db
def test_a_mixed_case_stored_unit_still_gets_its_label(patient_info):
    # Converters accept `g/L`; the enum key is `G/L`. A case-sensitive lookup
    # returned None and the client fell back to the DEFAULT label, printing a
    # raw 120 as "g/deciliter".
    patient_info.hemoglobin_level = 120
    patient_info.hemoglobin_level_units = 'g/L'
    normalize_patient_info(patient_info)

    row = TrialAttributes(
        TrialFactory(hemoglobin_level_min=9), patient_info,
    ).details()['hemoglobinLevelMin']

    assert row['uvalue'] == pytest.approx(120)
    assert row['uunits'] == 'g/Liter'
    assert row['units'] == 'g/deciliter'
    # The graph compares this against the trial's 9 g/dL.
    assert row['uvalue_canonical'] == pytest.approx(12)


@pytest.mark.django_db
def test_an_unknown_stored_unit_is_not_labelled_with_the_default(patient_info):
    patient_info.weight = 220.46
    patient_info.weight_units = 'stone'
    normalize_patient_info(patient_info)

    row = TrialAttributes(
        TrialFactory(weight_min=50), patient_info,
    ).details()['weightMin']

    # Say nothing about units rather than printing the DEFAULT label over a
    # code the option map does not know.
    assert 'uunits' not in row
    assert 'units' not in row
    assert 'uvalue_canonical' not in row
