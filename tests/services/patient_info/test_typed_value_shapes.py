"""A patient value is absent, usable, or wrong — and who sent it decides
what wrong costs. (#594)

Before this, one mistake had four answers and nobody had chosen any of them.
Measured on `pd_l1_tumor_cels`, an `IntegerField`:

    'abc'   -> silently None -> no PD-L1 filter applied at all
    true    -> silently kept -> the ORM reads it as 1 and filters on one
    ''      -> 500 ValueError out of the ORM
    [1] {}  -> untouched, and a 500 TypeError from any filter that uses it

The silent pair is the dangerous half rather than the safe one:
`eligible_for_min_max_value` returns the whole scope for a `None`, so a
numeric value quietly dropped shows the reader trials a real value would
have excluded.

But "name it loudly" is only right for somebody who can fix it. The first
review round on this change caught the other half: `_build_in_memory` has
three callers and only the inline payload is a caller who typed the value.
A CTOMOP row holding a censored `'<0.5'` is not the reader's fault and not
theirs to fix, and the batch commands have no guard at all.
"""
import datetime as dt

import pytest
from rest_framework.exceptions import ValidationError

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.resolve import (
    MalformedPatientValue,
    _build_in_memory,
    _coerce_dates,
    _coerce_numerics,
    _patient_from_inline,
)

NUMBER = 'pd_l1_tumor_cels'   # IntegerField
FLOAT = 'weight'              # FloatField
DECIMAL = 'absolute_neutrophile_count'  # DecimalField
DATE = 'first_line_date'


def numeric(value, strict=True):
    data = {NUMBER: value}
    _coerce_numerics(data, PatientInfo, strict)
    return data[NUMBER]


def date(value, strict=True):
    data = {DATE: value}
    _coerce_dates(data, PatientInfo, strict)
    return data[DATE]


class TestAbsent:
    """No key, null, or an empty box. "Not answered", and never an error."""

    @pytest.mark.parametrize("nothing", [None, "", "   ", "\t\n"])
    def test_a_number_that_was_not_answered_becomes_none(self, nothing):
        # An empty form field is the most common way a real payload reaches
        # here, and it is what `''` meant before the 500: nothing.
        assert numeric(nothing) is None

    @pytest.mark.parametrize("nothing", [None, "", "   "])
    def test_a_date_that_was_not_answered_becomes_none(self, nothing):
        assert date(nothing) is None

    def test_a_key_that_is_absent_is_left_absent(self):
        # Not written as None: a key nobody sent is not an answer of any
        # kind, and inventing one would put a column into the filtered dict
        # the caller never mentioned.
        data = {}
        _coerce_numerics(data, PatientInfo, True)
        _coerce_dates(data, PatientInfo, True)
        assert data == {}


class TestUsable:
    """What goes through, or through one obvious conversion."""

    @pytest.mark.parametrize("value,expected", [(12, 12), (0, 0), ("12", 12), ("  12 ", 12)])
    def test_a_whole_number_arrives_as_one(self, value, expected):
        assert numeric(value) == expected

    def test_zero_is_an_answer(self):
        # `_says_something` says so, and it matters: a PD-L1 of 0 is a
        # finding. What happens to it downstream is `is_attr_blank`'s
        # business and a separate argument; nothing here may lose it.
        assert numeric(0) == 0

    def test_a_date_arrives_as_a_date(self):
        assert date("2026-01-31") == dt.date(2026, 1, 31)

    @pytest.mark.parametrize("value", ["2026-01-31T10:00:00", "2026-01-31 10:00:00"])
    def test_a_datetime_is_a_date_with_a_time_on_it(self, value):
        # NOT a malformed date, and this one is not hypothetical: the CTOMOP
        # adapter calls `.isoformat()` on anything that is a `date`, and a
        # `datetime` IS one, so it emits this shape itself. Rejecting it
        # meant telling a producer its own output was wrong.
        assert date(value) == dt.date(2026, 1, 31)

    def test_a_date_that_is_already_a_date_is_left_alone(self):
        given = dt.date(2026, 1, 31)
        assert date(given) is given


class TestWrongFromSomebodyWhoCanFixIt:
    """The inline payload. Named, not swallowed and not crashed."""

    @pytest.mark.parametrize("value", ["abc", [1], [], {}, {"a": 1}, 1.5j])
    def test_a_number_that_cannot_be_one_is_refused(self, value):
        with pytest.raises(MalformedPatientValue) as raised:
            numeric(value)
        assert raised.value.field_name == NUMBER

    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_wrong_on_a_number(self, value):
        # Python says `True` is an `int` and the ORM would take it as 1 —
        # which is a plausible enough PD-L1 percentage that nothing
        # downstream would look odd. Nobody means one by `true`.
        with pytest.raises(MalformedPatientValue):
            numeric(value)

    @pytest.mark.parametrize("column", [FLOAT, DECIMAL])
    @pytest.mark.parametrize("value", ["nan", "inf", "-inf", "Infinity", float("nan")])
    def test_a_number_that_is_not_finite_is_wrong(self, column, value):
        # On a FLOAT and a DECIMAL column, not the integer one. `int('nan')`
        # raises on its own, so the integer column refuses these whether or
        # not the finiteness check exists — the first version of this test
        # sat there and passed for that reason, catching nothing. Measured.
        #
        # `float('nan')` and `Decimal('Infinity')` both construct, so these
        # passed as usable and answered 500 from DRF's renderer, which will
        # not emit a NaN. `lab_number` in `ctomop_adapter` already refuses
        # them and gives the quieter reason: every threshold comparison
        # against a NaN is False, so the patient fails criteria nobody can
        # see them failing.
        data = {column: value}
        with pytest.raises(MalformedPatientValue):
            _coerce_numerics(data, PatientInfo, True)

    @pytest.mark.parametrize("value", ["nope", "31/01/2026", [1], {}, 7])
    def test_a_date_that_cannot_be_one_is_refused(self, value):
        with pytest.raises(MalformedPatientValue) as raised:
            date(value)
        assert raised.value.field_name == DATE

    def test_the_message_names_the_field_and_what_it_wanted(self):
        # The point over a 500: a caller with ~170 fields in the payload is
        # told which one, and what it should have been.
        with pytest.raises(MalformedPatientValue) as raised:
            numeric([1])
        said = raised.value.as_message()
        assert "whole number" in said
        assert "list" in said
        assert "null" in said

    def test_an_unparseable_string_no_longer_passes_as_no_answer(self):
        # The regression this is most likely to be "fixed" back into. It
        # used to become None, which reads as harmless and is not: the
        # filter is then skipped and the reader is shown trials that a real
        # value would have excluded.
        with pytest.raises(MalformedPatientValue):
            numeric("abc")


class TestWrongFromSomebodyWhoCannot:
    """An upstream row, and the batch commands. Dropped, as before.

    This is the half the first review round found missing. `'unknown'` and a
    censored `'<0.5'` are named in `ctomop_adapter` as values it expects; the
    reader cannot fix PROMOP, and two of the three batch commands have no
    guard, so raising here killed a run on a row that used to print.
    """

    @pytest.mark.parametrize("value", ["unknown", "<0.5", "abc", [1], {}, True, "nan"])
    def test_a_number_it_cannot_use_is_dropped(self, value):
        assert numeric(value, strict=False) is None

    @pytest.mark.parametrize("value", ["nope", "31/01/2026", [1], 7])
    def test_a_date_it_cannot_use_is_dropped(self, value):
        assert date(value, strict=False) is None

    def test_the_whole_patient_still_builds(self):
        # The one that matters for `search_trials_for_patients`: one
        # unusable lab must not cost the patient their match run.
        built = _build_in_memory({'disease': 'breast cancer', NUMBER: '<0.5'})
        assert built.pd_l1_tumor_cels is None
        assert built.disease == 'breast cancer'

    def test_that_is_the_default(self):
        # Lenient unless asked, because two of the three callers are not
        # HTTP at all and neither of them passes a flag.
        assert numeric("abc", strict=False) is None
        data = {NUMBER: "abc"}
        _coerce_numerics(data, PatientInfo)
        assert data[NUMBER] is None


class TestThroughTheInlinePath:
    """Where a wrong value becomes a 400, and whose key it names."""

    def test_a_wrong_value_is_a_validation_error(self):
        with pytest.raises(ValidationError):
            _patient_from_inline({'disease': 'breast cancer', NUMBER: [1]}, 'patient_info')

    def test_it_names_the_key_the_caller_actually_wrote(self):
        # `_no_recognised_fields_message` exists for this reason and says so:
        # "Names the keys the CALLER typed, not what they became."  An error
        # naming `patient_age` at somebody who sent `patientAge` makes them
        # search their payload for a key that is not in it.
        with pytest.raises(ValidationError) as raised:
            _patient_from_inline({'disease': 'breast cancer', 'patientAge': 'forty'}, 'patient_info')
        assert 'patientAge' in raised.value.detail

    def test_it_names_the_documented_spelling_of_an_aliased_field(self):
        # The producer sends `pd_l1_tumor_cells`; EXACT stores
        # `pd_l1_tumor_cels`, missing an `l`. Telling them about EXACT's
        # misspelling sends them looking for a key they did not send.
        with pytest.raises(ValidationError) as raised:
            _patient_from_inline(
                {'disease': 'breast cancer', 'pd_l1_tumor_cells': [1]}, 'patient_info'
            )
        assert 'pd_l1_tumor_cells' in raised.value.detail

    def test_it_names_the_spelling_that_carried_the_BAD_value(self):
        # Two spellings reach one column and an alias never overwrites, so
        # the value that fails here is the one under EXACT's own key. Naming
        # whichever came first in the payload would hand back
        # `pd_l1_tumor_cells`, whose 12 is fine: the caller corrects a field
        # that was never the problem and gets the same 400 back.
        with pytest.raises(ValidationError) as raised:
            _patient_from_inline(
                {
                    'disease': 'breast cancer',
                    'pd_l1_tumor_cells': 12,
                    'pd_l1_tumor_cels': 'abc',
                },
                'patient_info',
            )
        assert 'pd_l1_tumor_cels' in raised.value.detail
        assert 'pd_l1_tumor_cells' not in raised.value.detail

    def test_the_mirror_case_never_reaches_validation_at_all(self):
        # Measured, because I assumed the opposite and wrote a test for it.
        # With a USABLE value under EXACT's own spelling the alias does not
        # copy — `_normalise_inbound_keys` only fills a gap (#593) — so the
        # `'abc'` under the documented name never reaches a column and
        # nothing refuses it. The patient builds on the 12.
        #
        # That is a silent drop of something the caller typed, and it is
        # #593's tie-break rather than anything #594 introduced: changing it
        # means changing which spelling wins, which is not a decision to
        # take inside a bugfix. Pinned here so it is a known shape rather
        # than a surprise.
        built = _patient_from_inline(
            {
                'disease': 'breast cancer',
                'pd_l1_tumor_cells': 'abc',
                'pd_l1_tumor_cels': 12,
            },
            'patient_info',
        )
        assert built.pd_l1_tumor_cels == 12

    def test_an_empty_box_builds_a_patient_with_no_answer(self):
        built = _patient_from_inline({'disease': 'breast cancer', NUMBER: ''}, 'patient_info')
        assert built.pd_l1_tumor_cels is None

    def test_one_bad_field_does_not_hide_behind_a_good_one(self):
        with pytest.raises(ValidationError) as raised:
            _patient_from_inline(
                {'disease': 'breast cancer', 'patient_age': 'forty'}, 'patient_info'
            )
        assert 'patient_age' in raised.value.detail
