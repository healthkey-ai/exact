"""`uoverwritten` on an eligibility row (#449).

The register lives in `normalize.py` and is held to that module by
`tests/services/patient_info/test_overwrite_register.py`. This is the other
half: that the answer actually reaches the row a client reads, keyed on the
resolved patient attribute rather than on the trial-side name.

Why it matters is not visual. A value EXACT recomputes is accepted by PROMOP,
saved, and reverted by the next match — so a client that offers a control over
it shows the reader an edit that works once and then undoes itself, with
nothing on screen explaining why.
"""
import pytest

from trials.services.patient_info.normalize import OVERWRITTEN_ALWAYS
from trials.services.trial_details.trial_attributes import TrialAttributes


#: Always-overwritten fields that cannot appear on an eligibility row, and
#: why. A fact about `USER_TO_TRIAL_ATTRS_MAPPING`, not about the register —
#: written down so a field that falls off a row LATER fails instead of quietly
#: joining this list.
NOT_ON_A_ROW = {
    'metastatic_status': 'not in USER_TO_TRIAL_ATTRS_MAPPING',
}


def _stamped(fields):
    return TrialAttributes.with_patient_field_names(fields)


class TestTheRowCarriesTheAnswer:
    def test_an_always_overwritten_field_says_always(self):
        fields = _stamped({'a': {'name': 'meetsCRAB', 'ufield': 'meetsCRAB'}})
        assert fields['a']['upatientField'] == 'meets_crab'
        assert fields['a']['uoverwritten'] == {'when': 'always'}

    def test_a_conditional_field_names_its_condition(self):
        fields = _stamped({'a': {'name': 'mipiRisk', 'ufield': 'mipiRisk'}})
        assert fields['a']['upatientField'] == 'mipi_risk'
        note = fields['a']['uoverwritten']
        assert note['when'] == 'sometimes'
        assert 'mantle cell lymphoma' in note['condition']

    def test_a_field_exact_never_writes_says_nothing(self):
        """`meets_gelf` by name: it carries `is_computed_value` in the
        attribute mapping and is a plain stored boolean nothing derives. The
        draft that read that flag would have hidden the only control that can
        set it."""
        fields = _stamped({'a': {'name': 'meetsGELF', 'ufield': 'meetsGELF'}})
        assert fields['a']['upatientField'] == 'meets_gelf'
        assert fields['a']['uoverwritten'] is None

    def test_a_row_about_no_patient_attribute_says_nothing(self):
        """A trial-only row. `None` here is the same `None` as "the reader
        owns this" — the client tells them apart by `upatientField`, which is
        also `None` only in this case."""
        fields = _stamped({'a': {'name': 'studyId', 'ufield': None}})
        assert fields['a']['upatientField'] is None
        assert fields['a']['uoverwritten'] is None

    def test_a_trial_side_name_that_is_not_a_patient_attribute(self):
        """`therapies` puts the TRIAL attribute in `ufield`, so
        `therapiesRequired` would resolve to `therapies_required`, which is
        not a patient attribute at all. The stamp already checks that for
        `upatientField`; this key must not skip the check."""
        fields = _stamped({'a': {'name': 'therapiesRequired', 'ufield': 'therapiesRequired'}})
        assert fields['a']['upatientField'] is None
        assert fields['a']['uoverwritten'] is None

    def test_non_dict_entries_are_left_alone(self):
        fields = _stamped({'a': 'not a dict'})
        assert fields['a'] == 'not a dict'


class TestItIsTheRegisterBeingRead:
    @pytest.mark.parametrize('field', sorted(OVERWRITTEN_ALWAYS))
    def test_every_always_field_that_reaches_a_row_says_always(self, field):
        """Walks the register rather than sampling it, so a field added to
        `OVERWRITTEN_ALWAYS` cannot be one the row never reports.

        A field that cannot reach a row is named in `NOT_ON_A_ROW` rather than
        skipped where it is found. A bare skip would also swallow a NEW
        always-field that nobody mapped, and a future breakage in
        `AttributeNames` resolution — both of which are the gap this walk
        exists to catch, arriving as a green run.
        """
        from trials.services.attribute_names import AttributeNames

        if field in NOT_ON_A_ROW:
            pytest.skip(f'{field}: {NOT_ON_A_ROW[field]}')
        camel = AttributeNames.get_by_snake_case(field)
        assert camel, f'{field} no longer resolves to a camelCase name'
        fields = _stamped({'a': {'name': camel, 'ufield': camel}})
        assert fields['a']['upatientField'] == field, (
            f'{field} no longer reaches a row. If that is deliberate, name it '
            'in NOT_ON_A_ROW with the reason; if it is not, the row has gone '
            'quiet about a value EXACT overwrites on every match.'
        )
        assert fields['a']['uoverwritten'] == {'when': 'always'}
