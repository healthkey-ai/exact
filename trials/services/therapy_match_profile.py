"""Back-compat shim — the therapy match profile moved to `exact_matching` (E1.1).

Kept so existing `from trials.services.therapy_match_profile import ...` imports
(queryset, matcher, trial_attributes, promop_adapter, therapy_graph, tests) keep
working unchanged during the incremental extraction. Import from
`exact_matching.therapy_match_profile` in new code.
"""
from exact_matching.therapy_match_profile import (  # noqa: F401
    TherapyMatchProfile,
    LEGACY_THERAPY_MATCH_PROFILE,
    OMOP_THERAPY_MATCH_PROFILE,
    omop_therapy_enabled,
    omop_therapy_types_enabled,
    get_therapy_match_profile,
    THERAPY_MATCH_PROFILE,
)
