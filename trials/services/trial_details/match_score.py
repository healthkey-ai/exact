"""The matching score both the list and the detail show.

It is the percentage of the trial's OWN eligibility criteria that the patient
meets: `matched / (matched + unknown)`, with any `not_matched` returning 0.
Those are the rows the detail page renders, so the number agrees with what a
reader can count on screen.

Not the two other numbers this replaced:

* `UserToTrialAttrMatcher.trial_match_score()` walks the whole per-disease
  attribute inventory and counts an attribute the trial never constrained as
  "matched", which inflates a 5-criterion trial to the high nineties;
* the list's SQL `match_score` counts answered-vs-missing attributes, not
  matched ones, so the same trial read 42% in the list and 98% in the detail.
"""

from __future__ import annotations


def score_from_criteria(attributes) -> int:
    """`matched / total` over the trial's rendered eligibility attributes."""
    attributes = list(attributes or [])
    if not attributes:
        return 0
    matched = 0
    for attribute in attributes:
        status = attribute.get("matchingType") if isinstance(attribute, dict) else None
        if status == "not_matched":
            return 0
        if status == "matched":
            matched += 1
    return int(matched * 100 / len(attributes))


def criteria_match_score(trial, patient_info, attrs_to_fill_in=None) -> int:
    """Build the detail's rows for one trial and score them.

    Shared by `TrialSerializer` (list cards) and `TrialDetailsSerializer` so
    the two can never report different percentages for the same pair again.

    The score is over the canonical eligibility view, never the requested
    presentation: `?view=all_attributes_in_groups` returns named groups with no
    `trialEligibilityAttributes`, and scoring that shape reported 0% for a
    fully matched trial.
    """
    if patient_info is None:
        return 0
    from trials.services.trial_details.trial_templates import TrialTemplates

    details = TrialTemplates(trial, patient_info).potential_attributes_first_view(
        list(attrs_to_fill_in or []),
    )
    return score_from_criteria(details.get("trialEligibilityAttributes"))
