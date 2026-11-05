"""Regression: ValueOptions memoizes the disease-by-code lookup.

Ported from CB perf(value-options) memoize disease-by-code (CANCERBOT-BACKEND-NQ
on /patient-info/user/). One `all_options()` build fans out to ~40 per-disease /
per-line therapy lists, and each `therapy_*_by_disease_code` helper re-ran
`Disease WHERE code IN (lower, upper)`. Memoizing on the ValueOptions instance
means each disease is resolved once and reused across all of its lists.

EXACT diverges from CB by matching both `code` cases (`code__in=[lower, upper]`),
so the memo keys by the upper-cased code; results stay byte-identical.
"""
import pytest

from trials.services.value_options import ValueOptions
from tests.factories import DiseaseFactory


def _disease_lookups(captured):
    return [
        q for q in captured.captured_queries
        if 'from "trials_disease"' in q['sql'].lower()
    ]


@pytest.mark.django_db
def test_disease_lookup_memoized_across_therapy_lists(django_assert_max_num_queries):
    DiseaseFactory(code='MM')

    vo = ValueOptions()
    with django_assert_max_num_queries(500) as captured:
        vo.therapies_by_disease_code_and_line_code('MM', 'first_line_therapy')
        vo.therapies_by_disease_code_and_line_code('MM', 'second_line_therapy')
        vo.therapies_by_disease_code('MM')
        vo.therapy_components_by_disease_code('MM')
        vo.therapy_types_by_disease_code('MM')

    lookups = _disease_lookups(captured)
    assert len(lookups) == 1, (
        f"Disease resolved per therapy list (N+1); expected one memoized lookup. "
        f"Saw {len(lookups)}"
    )


@pytest.mark.django_db
def test_disease_lookup_cache_is_per_code_and_case_insensitive(django_assert_max_num_queries):
    DiseaseFactory(code='MM')
    DiseaseFactory(code='MCL')

    vo = ValueOptions()
    with django_assert_max_num_queries(500) as captured:
        vo.therapies_by_disease_code('MM')
        vo.therapies_by_disease_code('mm')   # same disease, lower case -> same memo
        vo.therapies_by_disease_code('MCL')

    # one for MM (reused for 'mm'), one for MCL
    assert len(_disease_lookups(captured)) == 2


@pytest.mark.django_db
def test_disease_lookup_cache_is_per_instance():
    """A fresh ValueOptions must not reuse another instance's memo — each
    all_options() build resolves diseases against the current DB."""
    DiseaseFactory(code='MM')
    vo1 = ValueOptions()
    vo1.therapies_by_disease_code('MM')
    assert 'MM' in vo1._disease_by_code_cache

    vo2 = ValueOptions()
    assert vo2._disease_by_code_cache == {}
