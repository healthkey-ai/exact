"""The score the list card and the detail header share."""
import pytest

from trials.services.trial_details.match_score import score_from_criteria

pytestmark = pytest.mark.django_db


def _criteria(*statuses):
    return [{"name": f"c{i}", "matchingType": status}
            for i, status in enumerate(statuses)]


def test_score_is_matched_over_the_rendered_criteria():
    # 4 of 5: the trial's five restrictions, one still unknown.
    assert score_from_criteria(_criteria(
        "unknown", "matched", "matched", "matched", "matched",
    )) == 80


def test_any_not_matched_is_zero():
    assert score_from_criteria(_criteria(
        "matched", "not_matched", "unknown",
    )) == 0


def test_no_criteria_is_zero():
    assert score_from_criteria([]) == 0
    assert score_from_criteria(None) == 0
