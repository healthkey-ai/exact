"""Trial-side demographics column names for the SEARCH QUERYSET — OMOP cutover seam.

Mirror of TherapyMatchProfile (#4445): centralizes the column name the search
queryset filters on, so flipping it onto the OMOP column is a one-place change.
Currently points at the LEGACY columns (behavior-preserving; reads do not change).

Scope (and its limits): this profile owns ONLY the column name used by the search
queryset — `TrialQuerySet.eligible_for_ethnicity` reads `ethnicity_required` from
here. It does NOT cover the per-trial matcher/status/score path: like the therapy
seam, the matcher reads ethnicity (and gender) via the config dict
`USER_TO_TRIAL_ATTRS_MAPPING` (the `ethnicity` entry's `attr` + `uvalue_function`
key; the `gender` entry's `attr`), which are hardcoded there. The OMOP cutover
therefore flips TWO surfaces together: this profile (queryset) AND that config
(matcher). Gender is matched generically (config `str_value` + `eligible_for_value`)
and is flipped entirely via the config.

OMOP counterparts (for the cutover): ethnicity_required -> omop_ethnicity_required;
gender -> omop_gender_concept_id.

Ported from CancerBot (CB owns the upstream seam). EXACT flips at cutover.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemographicsMatchProfile:
    ethnicity_required: str = 'ethnicity_required'
    gender: str = 'gender'


# Active profile. Swap these (or their values) at cutover to retarget the SEARCH
# QUERYSET at the omop_* columns — and flip USER_TO_TRIAL_ATTRS_MAPPING in the
# same change for the matcher path (see module docstring).
DEMOGRAPHICS_MATCH_PROFILE = DemographicsMatchProfile()
