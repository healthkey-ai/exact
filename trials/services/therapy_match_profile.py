"""Trial-side therapy column names used by matching.

The single seam for the OMOP therapy migration. The search queryset
(`trials/querysets/trial.py`) and the per-trial matcher
(`trials/services/user_to_trial_attr_matcher.py`) read the trial's therapy
columns by the names defined here instead of hardcoding string literals. To flip
matching onto the OMOP `concept_id` columns, override this profile in one place
(behind a feature flag at cutover) — no dispatch or matching logic changes.

Ported from CancerBot (CB epic #4447) to keep CB→EXACT ports cheap; CB owns the
upstream seam. EXACT is the downstream that flips to the OMOP profile at cutover.

Scope: this profile owns ONLY the trial-side column *names* read by matching. It
deliberately does NOT change dispatch order / matching semantics (kept
behavior-identical), the patient-side therapy code derivation, or config-driven
surfaces (`USER_TO_TRIAL_ATTRS_MAPPING` `attr` lists, the eligibility-count
mapper SQL) — those are a separate seam.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TherapyMatchProfile:
    therapies_required: str = 'therapies_required'
    therapies_excluded: str = 'therapies_excluded'
    therapy_components_required: str = 'therapy_components_required'
    therapy_components_excluded: str = 'therapy_components_excluded'
    therapy_types_required: str = 'therapy_types_required'
    therapy_types_excluded: str = 'therapy_types_excluded'
    # planned/supportive: the QUERYSET filter reads these via the profile, but the
    # per-trial matcher reads them through USER_TO_TRIAL_ATTRS_MAPPING (the
    # config-driven seam). At OMOP cutover BOTH seams must flip together.
    planned_therapies_required: str = 'planned_therapies_required'
    planned_therapies_excluded: str = 'planned_therapies_excluded'
    supportive_therapies_required: str = 'supportive_therapies_required'
    supportive_therapies_excluded: str = 'supportive_therapies_excluded'


# The active profile. Swap this (or its field values) to retarget matching at the
# OMOP columns. Keep it the ONLY place that names trial therapy columns for match.
THERAPY_MATCH_PROFILE = TherapyMatchProfile()
