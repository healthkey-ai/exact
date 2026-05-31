"""Unit tests for the consolidated receptor parent-code expansion module.

The end-to-end behaviour (BC trial matching against ER/PR/HR hierarchy)
is covered by `tests/querysets/test_trial_querysets.py::test_estrogen_receptor_status_hierarchy`
and the matcher's hierarchy tests at
`tests/services/test_user_to_trial_attr_matcher.py`. This file locks in
the helper signatures so a future drift between SQL-filter and
match-status display layers (which both consume this module) is caught
at the unit level too.
"""
import pytest

from trials.services.receptor_hierarchy import expand_uvalue, expand_values


class TestExpandValues:
    """SQL-filter layer: append parent code to list of patient values."""

    def test_child_value_gets_parent_appended(self):
        assert expand_values(['er_plus_with_hi_exp'], 'er') == [
            'er_plus_with_hi_exp', 'er_plus',
        ]

    def test_parent_only_passes_through(self):
        assert expand_values(['er_plus'], 'er') == ['er_plus']

    def test_unknown_code_passes_through(self):
        assert expand_values(['er_minus'], 'er') == ['er_minus']

    def test_empty_list_returns_empty(self):
        assert expand_values([], 'er') == []

    def test_order_preserved(self):
        result = expand_values(['er_minus', 'er_plus_with_hi_exp'], 'er')
        assert result == ['er_minus', 'er_plus_with_hi_exp', 'er_plus']

    def test_parent_not_duplicated_when_already_present(self):
        result = expand_values(['er_plus_with_hi_exp', 'er_plus'], 'er')
        assert result == ['er_plus_with_hi_exp', 'er_plus']

    def test_input_list_not_mutated(self):
        values = ['er_plus_with_hi_exp']
        expand_values(values, 'er')
        assert values == ['er_plus_with_hi_exp']

    @pytest.mark.parametrize('kind,child,parent', [
        ('er', 'er_plus_with_hi_exp', 'er_plus'),
        ('er', 'er_plus_with_low_exp', 'er_plus'),
        ('pr', 'pr_plus_with_hi_exp', 'pr_plus'),
        ('pr', 'pr_plus_with_low_exp', 'pr_plus'),
        ('hr', 'hr_plus_with_hi_exp', 'hr_plus'),
        ('hr', 'hr_plus_with_low_exp', 'hr_plus'),
    ])
    def test_all_known_hierarchies(self, kind, child, parent):
        assert expand_values([child], kind) == [child, parent]


class TestExpandUvalue:
    """Matcher uvalue layer: comma-join code with parent for CSV-split overlap."""

    @pytest.mark.parametrize('kind,child,parent', [
        ('er', 'er_plus_with_hi_exp', 'er_plus'),
        ('er', 'er_plus_with_low_exp', 'er_plus'),
        ('pr', 'pr_plus_with_hi_exp', 'pr_plus'),
        ('pr', 'pr_plus_with_low_exp', 'pr_plus'),
        ('hr', 'hr_plus_with_hi_exp', 'hr_plus'),
        ('hr', 'hr_plus_with_low_exp', 'hr_plus'),
    ])
    def test_child_value_produces_csv(self, kind, child, parent):
        assert expand_uvalue(child, kind) == f'{child},{parent}'

    def test_parent_only_passes_through_unchanged(self):
        assert expand_uvalue('er_plus', 'er') == 'er_plus'

    def test_unknown_code_passes_through_unchanged(self):
        assert expand_uvalue('er_minus', 'er') == 'er_minus'

    def test_none_passes_through(self):
        assert expand_uvalue(None, 'er') is None

    def test_empty_string_passes_through(self):
        assert expand_uvalue('', 'er') == ''
