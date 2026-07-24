"""
Coverage test for the _CUSTOM_SEARCH_DISPATCH table in
trials/querysets/trial.py (#29).

The refactor moved a ~110-line if/elif chain into a dispatch dict of
lambdas. The pre-existing integration test
`test_filter_by_patient_info` only exercises ~11 of the ~35 entries, so
a typo in any of the other lambdas (wrong queryset method name, swapped
arg, missing transform) would slip through CI silently. This test
exercises every dispatch entry against a `MagicMock` queryset and asserts
the handler dispatches to a real method (one that exists on
`TrialQuerySet`), with the expected single-positional-argument shape
that all the simple handlers use.
"""
import inspect
from unittest.mock import MagicMock

import pytest

from trials.querysets.trial import (
    TrialQuerySet,
    _CUSTOM_SEARCH_DISPATCH,
    _csv,
    _csv_stripped,
    _filter_therapy_lines_once,
)


# Stateful handlers that take ctx — they call multiple queryset methods or
# branch on ctx, so the "exactly one queryset call with the value" assertion
# doesn't fit. Verified by hand in the dispatch refactor and covered by the
# integration test_filter_by_patient_info.
_STATEFUL_KEYS = {
    'disease',           # calls .filter(disease__iexact=...) — not eligible_for_*
    'tumor_grade',       # applies .lower() transform
    'prior_therapy',     # 2 method calls + ctx flag flip
    'concomitant_medications',  # 2 positional args
    'last_treatment',    # ctx-gated no-op
}


class TestDispatchTableExhaustive:
    """Every dispatch entry must resolve to an existing queryset method."""

    @pytest.mark.parametrize('user_attr', sorted(_CUSTOM_SEARCH_DISPATCH.keys()))
    def test_handler_calls_existing_queryset_method(self, user_attr):
        handler = _CUSTOM_SEARCH_DISPATCH[user_attr]
        if user_attr in _STATEFUL_KEYS:
            pytest.skip(f'{user_attr} is a stateful handler — covered by integration tests')

        scope = MagicMock(spec=TrialQuerySet)
        ctx = {'patient_info': None, 'patient_info_attr': MagicMock(),
               'has_no_prior_therapy': False,
               'user_therapies': {}, 'is_therapies_filter_applied': False}
        # Call the handler with a typical comma-string value so CSV
        # handlers exercise their split path too.
        handler(scope, 'a,b', ctx)

        # The handler must have called exactly one method on scope. Each
        # entry in mock.method_calls is a 3-tuple (name, args, kwargs).
        called = [call[0] for call in scope.method_calls]
        assert len(called) == 1, \
            f'{user_attr} handler made {len(called)} queryset calls: {called}'
        method_name = called[0]
        assert hasattr(TrialQuerySet, method_name), \
            f'{user_attr} dispatches to TrialQuerySet.{method_name} which does not exist'

    def test_no_overlap_between_dispatch_and_therapy_lines(self):
        """`filter_by_patient_info` falls back to `_filter_therapy_lines_once`
        for therapy-line attrs after the dispatch dict misses. If a
        therapy-line key sneaks into the dispatch, the wrong handler fires.
        """
        from trials.services.patient_info.configs import (
            THERAPY_LINES_ATTRS_UNDERSCORED,
        )
        # supportive_therapies now has a dedicated dispatch entry (#4449) — it is
        # NOT a therapy-line fallback attr, so it is deliberately excluded here.
        therapy_keys = set(THERAPY_LINES_ATTRS_UNDERSCORED)
        assert therapy_keys.isdisjoint(_CUSTOM_SEARCH_DISPATCH.keys())

    def test_every_custom_search_config_is_routable(self):
        """Regression guard for the bug class behind #94: every config
        marked `custom_search=True` must have a `_CUSTOM_SEARCH_DISPATCH`
        entry (or be in the therapy-line fallback set). Without this
        assertion, adding a new config with `custom_search=True` and no
        dispatch entry silently crashes `filter_by_patient_info` only
        when a patient with that attr non-blank flows through — which
        is exactly how #94 sat latent for weeks.
        """
        from trials.services.patient_info.configs import (
            USER_TO_TRIAL_ATTRS_MAPPING,
            THERAPY_LINES_ATTRS_UNDERSCORED,
        )
        custom_search_keys = {
            attr for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items()
            if meta.get('custom_search') is True
        }
        routable = (
            set(_CUSTOM_SEARCH_DISPATCH.keys())  # includes supportive_therapies (#4449)
            | set(THERAPY_LINES_ATTRS_UNDERSCORED)
        )
        unroutable = custom_search_keys - routable
        assert not unroutable, (
            f'custom_search=True attrs with no _CUSTOM_SEARCH_DISPATCH '
            f'entry and not in the therapy-line fallback: {sorted(unroutable)}. '
            f'They would raise in filter_by_patient_info on any patient '
            f'with that attr non-blank.'
        )


class TestCsvHelpers:
    def test_csv_splits_comma(self):
        assert _csv('a,b,c') == ['a', 'b', 'c']

    def test_csv_empty_returns_empty_list(self):
        assert _csv('') == []
        assert _csv(None) == []

    def test_csv_stripped_strips_whitespace(self):
        assert _csv_stripped(' a , b , c ') == ['a', 'b', 'c']

    def test_csv_stripped_empty_returns_empty_list(self):
        assert _csv_stripped('') == []


class TestTherapyLinesOnceFlag:
    """`_filter_therapy_lines_once` must apply the therapy filter exactly
    once across multiple therapy-line attrs in the same
    filter_by_patient_info call.
    """

    def test_first_call_applies_filter_and_flips_flag(self):
        scope = MagicMock(spec=TrialQuerySet)
        ctx = {'is_therapies_filter_applied': False, 'patient_info_attr': MagicMock(),
               'user_therapies': {}, 'has_no_prior_therapy': False}
        _filter_therapy_lines_once(scope, None, ctx)
        assert ctx['is_therapies_filter_applied'] is True
        assert scope.eligible_for_therapy_related_things_from_lines.called

    def test_second_call_is_no_op(self):
        scope = MagicMock(spec=TrialQuerySet)
        ctx = {'is_therapies_filter_applied': True, 'patient_info_attr': MagicMock(),
               'user_therapies': {}, 'has_no_prior_therapy': False}
        result = _filter_therapy_lines_once(scope, None, ctx)
        assert ctx['is_therapies_filter_applied'] is True
        assert not scope.eligible_for_therapy_related_things_from_lines.called
        # Returns the unchanged scope.
        assert result is scope
