"""`upatientRecomputed` / `upatientRecomputedWhen` on an eligibility row.

The register landed with #449 and answers a boolean. This is the follow-up:
the two cases that boolean gets wrong, and the reason it needs one.

* a value that is not stored at all answered `false` — "this is yours to
  edit" — over a property with no column and no setter;
* a value recomputed only under a condition answers `true` for every patient,
  so `mipi_risk` hides its control from a breast-cancer patient whose
  `mipi_risk` nothing here touches.
"""
import pytest

from trials.services.patient_info.normalize import RECOMPUTED_ALWAYS
from trials.services.trial_details.trial_attributes import TrialAttributes


#: Always-recomputed fields that cannot appear on an eligibility row, and why.
#: A fact about `USER_TO_TRIAL_ATTRS_MAPPING`, written down so a field that
#: falls off a row LATER fails instead of quietly joining this list.
NOT_ON_A_ROW = {
    'metastatic_status': 'not in USER_TO_TRIAL_ATTRS_MAPPING',
}


def _stamped(name, ufield=None):
    fields = TrialAttributes.with_patient_field_names(
        {'a': {'name': name, 'ufield': name if ufield is None else ufield}}
    )
    return fields['a']


class TestAValueThatIsNotStoredAtAll:
    @pytest.mark.parametrize(
        'camel,snake',
        [
            ('abnormalKappaLambdaRatio', 'abnormal_kappa_lambda_ratio'),
            ('meetsMeasOrBoneStatus', 'meets_meas_or_bone_status'),
        ],
    )
    def test_is_not_offered_as_the_readers(self, camel, snake):
        row = _stamped(camel)
        assert row['upatientField'] == snake, 'fixture no longer reaches a row'
        assert row['upatientRecomputed'] is True
        assert row['upatientRecomputedWhen'] == {'when': 'never-stored'}

    def test_a_stored_boolean_is_still_the_readers(self):
        """The other direction, and the pair #449 names: `meets_gelf` carries
        `is_computed_value` and is a plain stored BooleanField. A net cast
        wide enough to catch the properties must not catch this."""
        row = _stamped('meetsGELF')
        assert row['upatientField'] == 'meets_gelf'
        assert row['upatientRecomputed'] is False
        assert row['upatientRecomputedWhen'] is None


class TestTheConditionReachesTheRow:
    def test_a_conditional_field_carries_its_condition(self):
        row = _stamped('mipiRisk')
        assert row['upatientRecomputed'] is True
        note = row['upatientRecomputedWhen']
        assert note['when'] == 'sometimes'
        assert 'mantle cell lymphoma' in note['condition']

    def test_an_always_field_says_so_without_one(self):
        row = _stamped('tnbcStatus')
        assert row['upatientRecomputedWhen'] == {'when': 'always'}

    def test_a_row_about_no_patient_attribute_says_nothing(self):
        row = _stamped('studyId', ufield=None)
        assert row['upatientField'] is None
        assert row['upatientRecomputed'] is False
        assert row['upatientRecomputedWhen'] is None

    def test_a_trial_side_name_is_not_mistaken_for_a_patient_one(self):
        """`therapies` puts the TRIAL attribute in `ufield`, so
        `therapiesRequired` would resolve to a patient field nobody has."""
        row = _stamped('therapiesRequired')
        assert row['upatientField'] is None
        assert row['upatientRecomputedWhen'] is None


class TestTheRegisterIsWhatIsBeingRead:
    @pytest.mark.parametrize('field', sorted(RECOMPUTED_ALWAYS))
    def test_every_always_field_that_reaches_a_row_says_always(self, field):
        """Walks the set rather than sampling it, so a field added to it
        cannot be one the row never reports.

        A field that cannot reach a row is named in `NOT_ON_A_ROW` rather than
        skipped where it is found: a bare skip would also swallow a NEW
        always-field nobody mapped, and a future breakage in `AttributeNames`
        resolution — both arriving as a green run.
        """
        from trials.services.attribute_names import AttributeNames

        if field in NOT_ON_A_ROW:
            pytest.skip(f'{field}: {NOT_ON_A_ROW[field]}')
        camel = AttributeNames.get_by_snake_case(field)
        assert camel, f'{field} no longer resolves to a camelCase name'
        row = _stamped(camel)
        assert row['upatientField'] == field, (
            f'{field} no longer reaches a row. If deliberate, name it in '
            'NOT_ON_A_ROW with the reason; if not, the row has gone quiet '
            'about a value EXACT overwrites on every match.'
        )
        assert row['upatientRecomputedWhen'] == {'when': 'always'}
