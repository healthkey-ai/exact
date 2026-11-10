"""Exclusion-only Named-ORs criteria: no `criteria_required_attr` (#5521).

`_match_criteria_count` used to read `trial_attr_meta["criteria_required_attr"]`
unconditionally, so an attribute that constrains only the excluded list raised
`KeyError` in the Python matcher even though the SQL twin already treated the
inclusion side as optional. These cases pin the exclusion-only contract without
adding a real model field.
"""
from types import SimpleNamespace

import pytest

from exact_matching.matcher import UserToTrialAttrMatcher


def _meta(derived, unknown):
    """Criteria metadata with no inclusion side at all."""
    return {
        "criteria_excluded_attr": "excluded_codes",
        "criteria_derived": lambda _patient_info: derived,
        "criteria_unknown_codes": lambda _pia, codes: set(codes) & set(unknown),
        "criteria_all_unknown_codes": lambda _pia: set(unknown),
    }


def _matcher(trial):
    matcher = UserToTrialAttrMatcher.__new__(UserToTrialAttrMatcher)
    matcher.trial = trial
    matcher.patient_info = object()
    matcher.patient_info_attr = object()
    return matcher


CASES = [
    ("no_trial_list", [], "", set(), "matched"),
    ("patient_has_excluded", ["mgus"], "mgus", set(), "not_matched"),
    ("patient_confirmed_absent", ["mgus"], "", set(), "matched"),
    ("patient_unknown", ["mgus"], "", {"mgus"}, "unknown"),
    ("yes_outside_trial_list", ["mgus"], "al_amyloidosis", set(), "matched"),
    ("present_wins_over_unknown", ["mgus", "al_amyloidosis"], "mgus", {"al_amyloidosis"}, "not_matched"),
]


@pytest.mark.parametrize(
    "label, excluded_codes, derived, unknown, expected",
    CASES,
    ids=[case[0] for case in CASES],
)
def test_exclusion_only_criteria_do_not_require_an_inclusion_key(
    label, excluded_codes, derived, unknown, expected
):
    trial = SimpleNamespace(excluded_codes=excluded_codes)
    matcher = _matcher(trial)

    assert matcher._match_criteria_count(_meta(derived, unknown)) == expected
