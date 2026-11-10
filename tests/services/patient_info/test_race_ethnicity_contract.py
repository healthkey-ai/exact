"""#5556: EXACT's race-like field comes from PROMOP `race`, never OMOP ethnicity.

PROMOP stores OMOP race in `race` and OMOP ethnicity in `ethnicity`, while
EXACT's `ethnicity` field holds race categories (#4447). The adapter must not
translate an ethnicity answer into a race, and the ULN calculators must receive
the mapped race.
"""
import pytest

from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row

pytestmark = pytest.mark.django_db


def _patient(**row):
    return build_patient_info_from_promop_row(row)


class TestAdapterSource:
    def test_race_supplies_the_race_like_field(self):
        patient = _patient(race='Asian', ethnicity='Not Hispanic or Latino')
        assert patient.ethnicity == 'asian'

    def test_contradictory_ethnicity_does_not_override_race(self):
        patient = _patient(race='White', ethnicity='Hispanic or Latino')
        assert patient.ethnicity == 'caucasian_or_european'

    def test_legacy_race_label_falls_back_when_race_is_blank(self):
        patient = _patient(race='', ethnicity='White')
        assert patient.ethnicity == 'caucasian_or_european'

    def test_omop_ethnicity_alone_is_unknown(self):
        assert _patient(ethnicity='Hispanic or Latino').ethnicity is None

    def test_omop_ethnicity_alone_is_unknown_when_race_column_is_absent(self):
        assert _patient(ethnicity='Not Hispanic or Latino').ethnicity is None

    def test_both_sources_absent_stays_unknown(self):
        assert _patient().ethnicity is None

    def test_an_unmapped_race_does_not_fall_back(self):
        patient = _patient(
            race='Native Hawaiian or Other Pacific Islander',
            ethnicity='White',
        )
        assert patient.ethnicity is None


class TestUlnCalculators:
    # Asian/F norms: ALT 32, AST 33, total bilirubin 1.3.
    # White/F norms: ALT 34, AST 35, total bilirubin 1.2.
    _labs = dict(
        gender='F',
        liver_enzyme_levels_ast=80,
        liver_enzyme_levels_alt=78,
        serum_bilirubin_level_total=1.9,
    )

    def test_race_feeds_the_uln_calculators(self):
        patient = _patient(race='Asian', ethnicity='Not Hispanic or Latino',
                           **self._labs)
        attributes = PatientInfoAttributes(patient)

        assert attributes.get_uln_value('liver_enzyme_levels_alt') == pytest.approx(78 / 32)
        assert attributes.get_uln_value('liver_enzyme_levels_ast') == pytest.approx(80 / 33)
        assert attributes.get_uln_value('serum_bilirubin_level_total') == pytest.approx(1.9 / 1.3)
        assert attributes.hepatic_adequacy_status is True

    def test_omop_ethnicity_alone_leaves_adequacy_unresolved(self):
        patient = _patient(ethnicity='Hispanic or Latino', **self._labs)
        attributes = PatientInfoAttributes(patient)

        assert patient.ethnicity is None
        assert attributes.get_uln_value('liver_enzyme_levels_alt') is None
        # The PROMOP adapter names the adequacy fields, so missing inputs are
        # unresolved (None), not the legacy False that means "not adequate".
        assert attributes.hepatic_adequacy_status is None


class TestSubformRaceInput:
    """The ULN subform edits race, so its value must speak PROMOP's labels."""

    def test_each_code_names_the_record_label_it_is_edited_by(self):
        from trials.services.patient_info.promop_adapter import (
            RACE_CODE_TO_PROMOP_LABEL, promop_race_label,
        )

        for code, label in RACE_CODE_TO_PROMOP_LABEL.items():
            assert promop_race_label(code) == label
            # And the label maps back to the same code, so saving it is a
            # round trip rather than a one-way translation.
            assert _patient(race=label).ethnicity == code

    def test_a_legacy_title_is_translated_too(self):
        from trials.services.patient_info.promop_adapter import promop_race_label

        assert promop_race_label('Caucasian/European') == 'White'
        assert promop_race_label('African/Black') == 'Black or African American'

    def test_an_unrecognised_value_is_left_visible(self):
        from trials.services.patient_info.promop_adapter import promop_race_label

        assert promop_race_label('Klingon') == 'Klingon'
        assert promop_race_label(None) is None
