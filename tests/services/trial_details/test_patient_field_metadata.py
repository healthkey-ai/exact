"""`upatientField` — the attribute a row is about, in snake_case (EXACT #421).

`ufield` is camelCase for the UI; PROMOP's API is snake_case and answers an
unknown field with 200 and no change, so a client deriving the name fails
silently. This key names the attribute and promises nothing about writability —
that is the writable-fields descriptor's answer.
"""
import pytest

from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.trial_details.trial_attributes import TrialAttributes


class TestPatientFieldName:
    @pytest.mark.django_db
    def test_every_row_carries_it(self, patient_info):
        rows = TrialAttributes(TrialFactory(), patient_info).details()
        assert rows, 'no rows to check'
        assert all('upatientField' in row for row in rows.values())

    @pytest.mark.django_db
    def test_the_rows_built_away_from_get_field_carry_it_too(self, patient_info):
        # Computed rows are assembled by `computed_general_fields`, which never
        # touches `get_field` — so a key stamped there would be one these rows
        # quietly lack, and a client cannot tell a missing key from "this row
        # is about nothing".
        rows = TrialAttributes(TrialFactory(), patient_info).details()
        computed = {n: r for n, r in rows.items()
                    if n in ('distance', 'goodnessScore', 'matchScore', 'distancePenalty')}
        assert len(computed) == 4, computed.keys()
        assert all('upatientField' in r and r['upatientField'] is None
                   for r in computed.values())

    @pytest.mark.django_db
    def test_it_is_the_snake_case_name(self, patient_info):
        trial = TrialFactory(hemoglobin_level_min=5, hemoglobin_level_max=15)
        row = TrialAttributes(trial, patient_info).details()['hemoglobinLevelMin']
        assert row['ufield'] == 'hemoglobinLevel'
        assert row['upatientField'] == 'hemoglobin_level'

    def test_every_attribute_survives_the_round_trip(self):
        # The property that matters, over the whole mapping rather than one
        # example: every user field must come back as itself. A name that does
        # not is a PATCH against a field nobody has, answered 200.
        from trials.services.attribute_names import AttributeNames
        from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING

        broken = [
            name for name in USER_TO_TRIAL_ATTRS_MAPPING
            if AttributeNames.get_by_camel_case(AttributeNames.get_by_snake_case(name)) != name
        ]
        assert broken == []

    @pytest.mark.django_db
    def test_a_row_with_no_patient_field_says_nothing(self, patient_info):
        # Trial-only attributes have no `ufield`, so there is nothing to name
        # and the key must not invent one.
        row = TrialAttributes(TrialFactory(), patient_info).details()['briefTitle']
        assert row['ufield'] is None
        assert row['upatientField'] is None

    @pytest.mark.django_db
    def test_a_name_that_is_not_a_patient_attribute_is_not_invented(self):
        # The therapies builder puts the TRIAL attribute in `ufield`, so a
        # blind conversion hands the client `therapies_required` — not a
        # patient attribute at all, and another silent 200.
        patient_info = PatientInfo(disease='multiple myeloma')
        trial = TrialFactory(therapies_required=['vrd'], disease='Multiple Myeloma')
        rows = TrialAttributes(trial, patient_info).details()
        assert rows['therapiesRequired']['ufield'] == 'therapiesRequired'
        assert rows['therapiesRequired']['upatientField'] is None
        # …while a row whose ufield really is a patient attribute keeps it.
        assert rows['therapyLinesCountMin']['upatientField'] == 'prior_therapy'

    @pytest.mark.django_db
    def test_it_does_not_claim_to_say_what_exact_recomputes(self, patient_info):
        # `ucomputed` read the mapping's presentation flag as "EXACT overwrites
        # this" and got it wrong in both directions. The row now carries
        # `upatientRecomputed`/`upatientRecomputedWhen` instead; the old key
        # must not come back.
        rows = TrialAttributes(TrialFactory(), patient_info).details()
        assert all('ucomputed' not in row for row in rows.values())

    @pytest.mark.django_db
    def test_naming_a_field_is_not_calling_it_writable(self, patient_info):
        rows = TrialAttributes(TrialFactory(), patient_info).details()
        read_only_but_named = [
            n for n, r in rows.items()
            if r.get('ureadonly') and r.get('upatientField')
        ]
        assert read_only_but_named, 'expected rows that are named and behind a subform'

    def test_a_value_computed_on_read_is_never_stored(self):
        # `day_since_stem_cell_transplant` is computed by
        # `PatientInfoAttributes.get_value`, not by a PatientInfo property, so
        # the property check cannot see it. An edit is dropped all the same.
        from trials.services.patient_info.normalize import recompute_note

        assert recompute_note('day_since_stem_cell_transplant') == {
            'when': 'never-stored',
        }
