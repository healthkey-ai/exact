"""Regression tests for BlankAttributeRecordsCount after the .extra() → aggregate() port (#25)."""
import pytest

from tests.factories import TrialFactory
from trials.models import Trial
from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount


class TestBlankAttributeRecordsCount:
    def test_none_patient_info_returns_empty(self):
        """No patient → no candidate attrs → empty dict, no DB hit."""
        result = BlankAttributeRecordsCount().counts(patient_info=None)
        assert result == {}

    @pytest.mark.django_db
    def test_empty_scope_returns_empty(self, patient_info):
        """`aggregate()` on `.none()` returns `{key: None}` for every key,
        which the final non-None filter strips to `{}`. Pre-port this path
        exited via `if not out: return {}` after `.extra(select=).values()`
        — preserve the empty-dict contract.
        """
        result = BlankAttributeRecordsCount().counts(
            scope=Trial.objects.none(), patient_info=patient_info
        )
        assert result == {}

    @pytest.mark.django_db
    def test_counts_a_scope_that_carries_distinct(self, patient_info):
        """A `.distinct()` scope must still count.

        Regression: QA on cb-like-trials, 2026-09-14.
        Report: .gstack/qa-reports/qa-report-localhost-5201-2026-09-14.md

        The CASE-WHEN strings name columns UNQUALIFIED. Against the table that
        resolves; against a DERIVED TABLE it does not, and Django computes
        `aggregate()` over a subquery as soon as the scope carries a
        `.distinct()`. Postgres then answers `column "age_low_limit" does not
        exist` and the search endpoint 500s.

        `by_location` produces exactly this scope — it joins LocationTrial and
        ends in `.distinct()` — so a search carrying a resolvable country hits
        it. Measured over real HTTP on THIS branch before the fix:
        `GET /trials/search/` with a JSON body → 200, and the same request with
        `?country=<title>` → 500. (`POST /trials/search/match/` is a route on
        `cb-like-trials`, not here — it 405s on this branch.)

        Qualifying the columns would not have helped: the outer query's FROM
        holds only the derived table.
        """
        TrialFactory(age_low_limit=None, age_high_limit=None)
        TrialFactory(age_low_limit=18, age_high_limit=65)

        service = BlankAttributeRecordsCount()
        plain = service.counts(scope=Trial.objects.all(), patient_info=patient_info)
        distinct = service.counts(
            scope=Trial.objects.all().distinct(), patient_info=patient_info
        )

        assert plain, 'the fixture must produce at least one counted attribute'
        # Same scope, same answer — the `.distinct()` changes how the rows are
        # collected, never which trials are counted.
        assert distinct == plain
