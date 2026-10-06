"""Shared parent-code expansion for BC receptor statuses.

Patient receptor codes have a 2-level hierarchy: specific subtypes
(`*_with_hi_exp`, `*_with_low_exp`) imply the generic parent
(`er_plus`, `pr_plus`, `hr_plus`). A trial that requires the parent
code must match patients carrying any subtype.

Consumed at two layers — both must include the parent so the SQL
filter and the per-trial match-status display agree:

- SQL filter (`trials/querysets/trial.py`'s `eligible_for_*_statuses`)
  uses `expand_values()` to append the parent to the values list before
  the IN-overlap check.
- Match-status display (`USER_TO_TRIAL_ATTRS_MAPPING.*.uvalue_function`
  in `trials/services/patient_info/configs.py`) uses `expand_uvalue()`
  to comma-join the patient's code with its parent so the matcher's
  CSV-split overlap picks up either.

Previously each layer had its own copy of the parent map; editing one
without the other silently desynced search results from match badges.
This module is the single source of truth.
"""
from typing import Literal

ReceptorType = Literal['er', 'pr', 'hr']

_PARENT_CODES_BY_TYPE: dict[ReceptorType, dict[str, str]] = {
    'er': {'er_plus_with_hi_exp': 'er_plus', 'er_plus_with_low_exp': 'er_plus'},
    'pr': {'pr_plus_with_hi_exp': 'pr_plus', 'pr_plus_with_low_exp': 'pr_plus'},
    'hr': {'hr_plus_with_hi_exp': 'hr_plus', 'hr_plus_with_low_exp': 'hr_plus'},
}


def expand_values(values: list[str], kind: ReceptorType) -> list[str]:
    """Append the parent code for every child in `values`. Order preserved.

    Used by the SQL-filter layer. Returns a new list; `values` is unmodified.
    """
    parent_map = _PARENT_CODES_BY_TYPE[kind]
    expanded = list(values)
    for v in values:
        parent = parent_map.get(v)
        if parent and parent not in expanded:
            expanded.append(parent)
    return expanded


def expand_uvalue(code: str | None, kind: ReceptorType) -> str | None:
    """Comma-join `code` with its parent (if any) for matcher consumption.

    Used by `uvalue_function` lambdas in the matcher pipeline; the
    matcher splits the returned string by comma so a CSV of
    `"child,parent"` overlaps against either trial requirement.
    Returns `code` unchanged when it has no parent mapping. Falsy
    inputs (None, empty string) pass through.
    """
    if not code:
        return code
    parent = _PARENT_CODES_BY_TYPE[kind].get(code)
    return f"{code},{parent}" if parent else code
