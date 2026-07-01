"""
Unit tests for disease_attr_applies (#43).

The helper replaces five inline `disease_code in trial_attr_meta["disease"]`
checks where the operator was string-vs-list-polymorphic. With a list, `in`
checks membership (good). With a string, `in` does substring matching
(bad — `'M' in 'MM'` is True), so a future disease code like 'M' would
have silently matched 'MM' / 'MCL' restrictions. The call sites:
matcher.trial_match_status / trial_match_score, queryset
filter_by_patient_info, user_to_trial_attrs_mapper.potential_attrs_to_check,
trial_details.trial_attributes get_user_details, and
trial_match_explainer.explain.
"""
from trials.services.utils import disease_attr_applies


class TestDiseaseAttrApplies:
    # --- string restriction --------------------------------------------------

    def test_string_exact_match(self):
        assert disease_attr_applies('MM', 'MM') is True

    def test_string_non_match(self):
        assert disease_attr_applies('MM', 'CLL') is False

    def test_string_substring_does_not_match(self):
        # Pre-#43 inline check: `'M' in 'MM'` is True (substring). The helper
        # must treat string restrictions as exact equality, not substring.
        assert disease_attr_applies('MM', 'M') is False

    def test_string_substring_reversed(self):
        # And the opposite direction: a longer patient code should not match
        # a shorter trial code.
        assert disease_attr_applies('M', 'MM') is False

    def test_string_case_sensitive(self):
        # Disease codes are upper-case canonical (MM/FL/BC/CLL/MCL). The
        # helper does a strict equality check — no lowercasing.
        assert disease_attr_applies('MM', 'mm') is False

    # --- list restriction ----------------------------------------------------

    def test_list_member_matches(self):
        assert disease_attr_applies(['MM', 'CLL'], 'MM') is True
        assert disease_attr_applies(['MM', 'CLL'], 'CLL') is True

    def test_list_non_member(self):
        assert disease_attr_applies(['MM', 'CLL'], 'BC') is False

    def test_empty_list_matches_nothing(self):
        assert disease_attr_applies([], 'MM') is False

    def test_list_substring_of_member_does_not_match(self):
        # `'C' in ['CLL']` is False (list membership ignores substrings).
        # Sanity-check the polymorphism didn't reintroduce substring behavior.
        assert disease_attr_applies(['CLL'], 'C') is False

    # --- list-like containers ------------------------------------------------

    def test_tuple_restriction(self):
        # Tuples are also accepted (configs sometimes use tuples for
        # immutability; the helper should treat them the same as lists).
        assert disease_attr_applies(('MM', 'CLL'), 'MM') is True
        assert disease_attr_applies(('MM', 'CLL'), 'BC') is False

    def test_set_restriction(self):
        assert disease_attr_applies({'MM', 'CLL'}, 'MM') is True
        assert disease_attr_applies({'MM', 'CLL'}, 'BC') is False


class TestRealConfigShapes:
    """Spot-check against the actual `USER_TO_TRIAL_ATTRS_MAPPING` shapes
    so a config refactor (e.g. switching a list to a tuple, or vice
    versa) doesn't silently break the helper's contract.
    """

    def test_mm_only_restriction(self):
        # configs.py:272 — "disease": "MM"
        assert disease_attr_applies('MM', 'MM') is True
        assert disease_attr_applies('MM', 'FL') is False

    def test_mm_or_cll_restriction(self):
        # configs.py:257 — "disease": ["MM", "CLL"]
        assert disease_attr_applies(['MM', 'CLL'], 'MM') is True
        assert disease_attr_applies(['MM', 'CLL'], 'CLL') is True
        assert disease_attr_applies(['MM', 'CLL'], 'MCL') is False
        assert disease_attr_applies(['MM', 'CLL'], 'BC') is False
