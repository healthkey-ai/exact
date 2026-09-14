"""`?lastUpdate=` / `?firstEnrolment=` accept a date, not only a year count (#429).

Both delegate to `by_date_since`, which read the value through
`cast_str_to_int` — `int`, or a string that `isdigit()`. An ISO date is
neither, so it became None and the filter returned everything.

CancerBot's panel renders this field as `<input type="date">` and PATCHes
`2026-01-01`. Its `by_date_since` and `cast_str_to_int` are byte-identical to
ours, so the control there has never filtered anything either.

The fix is additive: a digits-only value keeps its exact meaning. The only
inputs whose behaviour changes are the ones that did nothing at all.
"""
import datetime as dt

import pytest

from trials.models import Trial
from tests.factories import TrialFactory


def _ids(value, attr='last_update'):
    method = getattr(Trial.objects.all(), f'by_{attr}')
    return {t.study_id for t in method(value)}


@pytest.mark.django_db
class TestADateIsHonoured:
    def _corpus(self):
        TrialFactory(study_id='RECENT', last_update_date=dt.date(2026, 6, 1))
        TrialFactory(study_id='OLD', last_update_date=dt.date(2020, 1, 1))
        # NULL passes every filter — the existing `__isnull=True` arm, kept.
        TrialFactory(study_id='UNDATED', last_update_date=None)

    def test_an_iso_date_narrows(self):
        self._corpus()
        assert _ids('2026-01-01') == {'RECENT', 'UNDATED'}

    def test_the_same_date_as_a_date_object(self):
        self._corpus()
        assert _ids(dt.date(2026, 1, 1)) == {'RECENT', 'UNDATED'}

    def test_surrounding_whitespace_does_not_defeat_it(self):
        self._corpus()
        assert _ids(' 2026-01-01 ') == {'RECENT', 'UNDATED'}

    def test_the_javascript_spelling_of_the_same_date(self):
        """`toISOString()` is what a TypeScript caller reaches for by default,
        and `<input type="date">` giving the bare form is luck rather than
        design — one UI change and #429 reopens in a new spelling."""
        self._corpus()
        assert _ids('2026-01-01T00:00:00') == {'RECENT', 'UNDATED'}
        assert _ids('2026-01-01T00:00:00Z') == {'RECENT', 'UNDATED'}

    def test_first_enrolment_shares_the_parsing(self):
        TrialFactory(study_id='RECENT', first_enrolment_date=dt.date(2026, 6, 1))
        TrialFactory(study_id='OLD', first_enrolment_date=dt.date(2020, 1, 1))
        # The undated arm on THIS column too: it was verified for one and
        # assumed for the other.
        TrialFactory(study_id='UNDATED', first_enrolment_date=None)

        assert _ids('2026-01-01', attr='first_enrolment_date') == {'RECENT', 'UNDATED'}


@pytest.mark.django_db
class TestTheYearCountIsUntouched:
    """The half that already worked, asserted because an additive change that
    quietly re-points the existing spelling is not additive."""

    def _corpus(self):
        TrialFactory(study_id='RECENT', last_update_date=dt.date.today())
        TrialFactory(
            study_id='OLD', last_update_date=dt.date.today() - dt.timedelta(days=365 * 4)
        )

    def test_a_number_still_means_years_ago(self):
        self._corpus()
        assert _ids(1) == {'RECENT'}
        assert _ids('1') == {'RECENT'}
        assert _ids(10) == {'RECENT', 'OLD'}

    def test_a_bare_four_digit_number_is_still_a_COUNT_of_years(self):
        """Reading `2026` as a calendar year would silently re-point any caller
        who meant the count, which is why only a value with separators is read
        as a date.

        Paired with a count that DISCRIMINATES: `{'RECENT', 'OLD'}` is also
        what "the value was ignored entirely" returns, since a 2026-year window
        covers everything. Only the narrower count proves the string was read
        as a count at all."""
        self._corpus()
        assert _ids('2026') == {'RECENT', 'OLD'}
        assert _ids('3') == {'RECENT'}

    def test_whitespace_is_tolerated_on_this_spelling_too(self):
        """It was stripped on the date path only, so the same stray whitespace
        off a query string got two different answers."""
        self._corpus()
        assert _ids(' 3 ') == {'RECENT'}


@pytest.mark.django_db
class TestWhatStillMeansNoFilter:
    def test_nonsense_is_ignored_rather_than_rejected(self):
        """Unparseable input has always meant "no filter" rather than "no
        results", and a 400 here would reject requests that work today."""
        TrialFactory(study_id='A')
        TrialFactory(study_id='B')

        for value in ('', None, 'yesterday', '01/06/2026', '2026-13-45', 0, '0'):
            assert _ids(value) == {'A', 'B'}, value

    def test_a_count_that_cannot_name_a_real_date_is_ignored_rather_than_a_500(self):
        """`docs/api.md` documents these params as DATES, so a caller typing a
        calendar year is the natural mistake — and `365 * 2030` days before now
        is not a representable date.

        This raised `OverflowError` straight out of the endpoint. The first
        version of this change asserted in a docstring that a four-digit value
        was "absurd but harmless"; it was a 500."""
        TrialFactory(study_id='A')
        TrialFactory(study_id='B')

        for value in (2030, '2030', 99999, 10 ** 20, str(10 ** 20)):
            assert _ids(value) == {'A', 'B'}, value

    def test_a_digit_string_too_long_for_int_is_ignored_rather_than_a_500(self):
        """Python refuses to parse an integer past 4300 digits, so `int()`
        raises — and stripping the whitespace is what lets such a value reach
        it, since `' 9…9 '.isdigit()` is False. The fix for one 500 opened
        another."""
        TrialFactory(study_id='A')
        TrialFactory(study_id='B')

        assert _ids(' ' + '9' * 5000 + ' ') == {'A', 'B'}
        assert _ids('9' * 5000) == {'A', 'B'}

    def test_a_negative_count_means_no_filter_in_either_spelling(self):
        """`-1` used to put the cut-off a YEAR IN THE FUTURE, so it returned
        only the undated trials — while `'-1'`, the spelling a query string
        delivers, was ignored. Two spellings of one intent, answering
        oppositely.

        This is the one input whose behaviour changes from filtering to being
        ignored. "Within the last minus one years" is not a question anyone
        asks, and `0` already meant no filter."""
        TrialFactory(study_id='DATED', last_update_date=dt.date.today())
        TrialFactory(study_id='UNDATED', last_update_date=None)

        assert _ids(-1) == {'DATED', 'UNDATED'}
        assert _ids('-1') == {'DATED', 'UNDATED'}
