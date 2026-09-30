"""A field the producer sends under a name EXACT stores differently.

EXACT's column is `pd_l1_tumor_cels` — one `l` short of "cells". PROMOP
spells it correctly (`omop_core/models.py:3232`, written at
`patient_record_service.py:2440`), so the key was not in `model_fields`, was
dropped at the field filter in `_build_in_memory`, and the request answered
200 with the patient's PD-L1 result missing (#593).

The direction that matters: `eligible_for_min_max_value` returns the whole
scope when the value is `None`. So a missing PD-L1 does not narrow anything —
a patient whose result is BELOW a trial's minimum is shown that trial anyway.
The bug produces false positives, not missing trials, which is why it cannot
be noticed by looking at a result list.

Same shape as `patientInfo` (#375) and `preExistingConditionCategories`, both
already documented in `resolve.py`: a key the caller sends, means, and never
learns was ignored.
"""
import pytest

from trials.services.patient_info.resolve import (
    _build_in_memory,
    _normalise_inbound_keys,
    _patient_from_inline,
)


class TestTheKeyArrives:
    def test_promops_spelling_lands_on_exacts_field(self):
        pi = _build_in_memory({'pd_l1_tumor_cells': 40, 'disease': 'breast cancer'})
        assert pi.pd_l1_tumor_cels == 40

    def test_exacts_own_spelling_still_works(self):
        pi = _build_in_memory({'pd_l1_tumor_cels': 40})
        assert pi.pd_l1_tumor_cels == 40

    def test_the_camel_case_the_host_may_send(self):
        # `_to_snake_case` runs first, so the alias is keyed on the
        # snake_cased name and both wire spellings reach it.
        pi = _build_in_memory({'pdL1TumorCells': 40})
        assert pi.pd_l1_tumor_cels == 40

    def test_a_string_is_still_coerced(self):
        # `_coerce_numerics` runs after the alias, not before it — the CB API
        # sends numbers as strings and that has to keep working through the
        # aliased name too.
        pi = _build_in_memory({'pd_l1_tumor_cells': '40'})
        assert pi.pd_l1_tumor_cels == 40


class TestTheAliasNeverOverwrites:
    def test_exacts_own_name_wins_when_both_are_sent(self):
        # Same tie-break as `PATIENT_INFO_KEYS`: between two usable spellings
        # the existing contract decides. A caller who wrote EXACT's name meant
        # EXACT's name.
        pi = _build_in_memory({'pd_l1_tumor_cels': 10, 'pd_l1_tumor_cells': 90})
        assert pi.pd_l1_tumor_cels == 10

    def test_but_it_fills_a_gap(self):
        # `None` under EXACT's name is not an answer, so the one the producer
        # sent is used rather than discarded.
        pi = _build_in_memory({'pd_l1_tumor_cels': None, 'pd_l1_tumor_cells': 90})
        assert pi.pd_l1_tumor_cels == 90

    def test_zero_is_an_answer_and_is_not_overwritten(self):
        # `_says_something` treats 0 as a value, unlike '' or None, so the
        # alias leaves it alone. Note what that does NOT buy: see
        # `test_zero_is_kept_here_and_discarded_downstream` below — the
        # matcher throws 0 away regardless, so this is consistency with the
        # rest of the module, not a rescue.
        pi = _build_in_memory({'pd_l1_tumor_cels': 0, 'pd_l1_tumor_cells': 90})
        assert pi.pd_l1_tumor_cels == 0


class TestTheGateSeesItToo:
    """The gate and the builder must agree about what EXACT understands.

    `_patient_from_inline` decides 400 / None / patient from the same payload
    `_build_in_memory` later reads. They need different things from the alias
    table and take them from the same constant: the gate needs the NAME to
    count as recognised (`_known_attribute_names`), the builder needs the
    VALUE moved (`_normalise_inbound_keys`).

    Getting only the second was the first draft of this change, and it made a
    payload naming only the producer's spelling answer 400 — a well-formed
    request refused. Getting only the first would recognise the name and then
    drop the value, which is the bug being fixed. Both come off one table, so
    adding an entry cannot supply one and forget the other.
    """

    def test_a_payload_naming_only_the_producers_spelling_is_a_patient(self):
        pi = _patient_from_inline({'pd_l1_tumor_cells': 40}, 'patient_info')
        assert pi is not None
        assert pi.pd_l1_tumor_cels == 40

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_the_two_spellings_agree_about_the_empty_state(self, empty):
        """The case that says whether the alias is understood or merely copied.

        `_patient_from_inline` has three answers, and the middle one — "names
        recognised, no value in any of them" — is `None`, not 400, because a
        form-backed client serialising every field as null has read the
        contract and simply has nothing in it yet.

        With the alias applied but its name left out of
        `_known_attribute_names`, `{"pd_l1_tumor_cells": null}` fell into
        `set(snake) - recognised` and answered 400, while
        `{"pd_l1_tumor_cels": null}` answered `None`. Two spellings of one
        field disagreeing about the empty state, and the client most likely
        to send every field is exactly the one that would have met it.
        """
        assert _patient_from_inline({'pd_l1_tumor_cells': empty}, 'patient_info') is None
        assert _patient_from_inline({'pd_l1_tumor_cels': empty}, 'patient_info') is None

    def test_an_empty_alias_does_not_disarm_the_refusal(self):
        # The empty state must not become a way to smuggle an unreadable
        # payload past the gate — that is #375's shape.
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            _patient_from_inline({'pd_l1_tumor_cells': None, 'diseas': 'x'}, 'patient_info')

    def test_an_unreadable_payload_is_still_refused(self):
        # The alias widens what EXACT understands; it must not disarm the
        # check that #466 added.
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError):
            _patient_from_inline({'diseas': 'myeloma'}, 'patient_info')


class TestTheNormaliserItself:
    def test_it_snake_cases_and_then_aliases(self):
        assert _normalise_inbound_keys({'pdL1TumorCells': 1}) == {
            'pd_l1_tumor_cells': 1,
            'pd_l1_tumor_cels': 1,
        }

    def test_it_leaves_everything_else_alone(self):
        assert _normalise_inbound_keys({'patientAge': 61}) == {'patient_age': 61}


class TestWhatItChangesForAPatient:
    """The end of the chain, not just the resolver.

    `eligible_for_min_max_value` returns the whole scope when the value is
    `None` (`trial.py:1252-1253`), so a dropped PD-L1 does not narrow: the
    patient is shown trials whose minimum they do not meet. The alias is only
    worth having if it closes that, so this asserts on the trial list rather
    than on the attribute.
    """

    @pytest.fixture
    def corpus(self, db):
        from tests.factories import TrialFactory

        TrialFactory(disease='breast cancer', pd_l1_tumor_cels_min=50)
        TrialFactory(disease='breast cancer')
        return None

    def _search(self, payload):
        from trials.models import Trial

        result, _ = Trial.objects.filter_by_patient_info(_build_in_memory(payload))
        return result.count()

    @pytest.mark.django_db
    def test_a_patient_below_the_minimum_no_longer_sees_that_trial(self, corpus):
        below = {'disease': 'breast cancer', 'pd_l1_tumor_cells': 10}
        assert self._search(below) == 1

    @pytest.mark.django_db
    def test_a_patient_above_it_still_does(self, corpus):
        above = {'disease': 'breast cancer', 'pd_l1_tumor_cells': 80}
        assert self._search(above) == 2

    @pytest.mark.django_db
    def test_a_patient_who_said_nothing_sees_both(self, corpus):
        # Unchanged, and deliberately so: no answer is not a failed answer,
        # and EXACT does not exclude a trial for a question the patient has
        # not been asked.
        assert self._search({'disease': 'breast cancer'}) == 2


class TestWhatTheAliasMustNotLetThrough:
    """An alias is a new route to a typed column, and the guard is on the SOURCE.

    Before this change the producer's key was dropped at the field filter, so
    nothing it carried could reach `pd_l1_tumor_cels`. Copying unconditionally
    changed that: `_coerce_numerics` skips `''`, `is_attr_blank` only blanks on
    `== 0`, and `eligible_for_min_max_value` then hands the value to Django,
    which raises. A 500 reachable through the spelling every client sends,
    where EXACT's own misspelling — which no client sends — was the only way
    in before.

    The cases below are mixed payloads on purpose: a lone key returns at the
    gate before any filtering, so it cannot reach the crash.
    """

    @pytest.fixture
    def corpus(self, db):
        from tests.factories import TrialFactory

        TrialFactory(disease='breast cancer', pd_l1_tumor_cels_min=50)
        TrialFactory(disease='breast cancer')
        return None

    def _search(self, payload):
        from trials.models import Trial

        result, _ = Trial.objects.filter_by_patient_info(_build_in_memory(payload))
        return result.count()

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_an_empty_source_is_not_copied_at_all(self, empty):
        # The GUARD, not the outcome. `''` is the only one of these that
        # currently reaches a crash, and the obvious fix for #594 —
        # `_coerce_numerics` treating `''` as `None` instead of skipping it —
        # would make the outcome pass with the guard deleted. Asserting the
        # copy did not happen survives that.
        assert 'pd_l1_tumor_cels' not in _normalise_inbound_keys(
            {'pd_l1_tumor_cells': empty}
        )

    @pytest.mark.parametrize("empty", ["", "   ", None])
    @pytest.mark.django_db
    def test_and_so_the_search_is_unaffected(self, corpus, empty):
        payload = {'disease': 'breast cancer', 'pd_l1_tumor_cells': empty}
        assert _build_in_memory(payload).pd_l1_tumor_cels is None
        assert self._search(payload) == 2

    @pytest.mark.django_db
    def test_a_malformed_value_fails_the_same_way_under_both_spellings(self, corpus):
        """What the alias is responsible for, and what it is not.

        `_says_something([1])` is True — a list is an answer, just not one
        this column can take — so the source guard does not stop it and the
        filter hands it to Django, which raises. That is not new: EXACT's own
        spelling has always done it. The alias's job is to make the
        producer's spelling behave like EXACT's own, and here it does,
        including in the failure.

        It used to be a 500, under either spelling — a `TypeError` out of
        Django when the filter handed it a list. This test was left
        expecting the `TypeError` on purpose so that it had to be revisited
        when #594 landed, rather than quietly passing for a new reason.

        Revisited. The requirement was always "the same way under both
        spellings" and it still is; only the way changed. `_search` builds
        the patient directly rather than through the inline path, which is
        the LENIENT audience — so what it shows is the drop, identically
        under both keys. The 400 belongs to the inline path and is tested
        in `test_typed_value_shapes.py`.
        """
        for key in ('pd_l1_tumor_cells', 'pd_l1_tumor_cels'):
            built = _build_in_memory({'disease': 'breast cancer', key: [1]})
            assert built.pd_l1_tumor_cels is None
            # And it is a drop, not a crash: the search still runs.
            assert self._search({'disease': 'breast cancer', key: [1]}) == 2

    @pytest.mark.django_db
    def test_zero_is_kept_here_and_discarded_downstream(self, corpus):
        # Measured rather than assumed, because the tie-break above reads as
        # if preserving 0 protects the patient. It does not: `is_attr_blank`
        # blanks an IntegerField on `== 0` and `filter_by_patient_info` skips
        # it, so a patient whose PD-L1 really is 0 gets no PD-L1 filtering and
        # still sees the `min=50` trial. Changing that means
        # `allow_blank_values` on the dispatch entry, which is a separate
        # decision and not one to take inside a bugfix.
        payload = {'disease': 'breast cancer', 'pd_l1_tumor_cells': 0}
        assert _build_in_memory(payload).pd_l1_tumor_cels == 0
        assert self._search(payload) == 2


class TestTheTableItself:
    def test_no_alias_shadows_a_real_field(self):
        """The invariant `_known_attribute_names` quietly depends on.

        A source name that is already a model field would make the alias
        overwrite a legitimate value. Asserted here rather than at import,
        because the field set needs Django loaded.
        """
        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.resolve import _INBOUND_ALIASES

        fields = {f.name for f in PatientInfo._meta.get_fields() if hasattr(f, 'column')}
        assert set(_INBOUND_ALIASES).isdisjoint(fields)

    def test_every_target_is_a_real_field(self):
        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.resolve import _INBOUND_ALIASES

        fields = {f.name for f in PatientInfo._meta.get_fields() if hasattr(f, 'column')}
        assert set(_INBOUND_ALIASES.values()) <= fields

    def test_the_table_only_holds_snake_case_sources(self):
        """Both readers snake-case first, so a camelCase source never matches.

        `PATIENT_INFO_KEYS` and `FIELD_TOOLTIPS` next door are keyed in
        camelCase, so `'pdL1TumorCells': ...` is the natural mistake to make
        here — and it would fail silently, in both readers, with every test
        still green.
        """
        from trials.services.patient_info.resolve import _INBOUND_ALIASES, camel_to_snake

        assert all(key == camel_to_snake(key) for key in _INBOUND_ALIASES)

    def test_no_alias_chains_into_another(self):
        from trials.services.patient_info.resolve import _INBOUND_ALIASES

        assert not (set(_INBOUND_ALIASES) & set(_INBOUND_ALIASES.values()))

    def test_no_two_aliases_share_a_target(self):
        from trials.services.patient_info.resolve import _INBOUND_ALIASES

        assert len(set(_INBOUND_ALIASES.values())) == len(_INBOUND_ALIASES)
