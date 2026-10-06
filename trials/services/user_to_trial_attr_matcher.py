"""Back-compat shim — the per-trial matcher moved to `exact_matching` (E1.2).

Kept so existing `from trials.services.user_to_trial_attr_matcher import ...`
imports (models, queryset, serializers, mapper, commands, tests) keep working
unchanged during the incremental extraction. Import from `exact_matching.matcher`
in new code. Data-access decoupling of the model queries is E1.2b.
"""
from exact_matching.matcher import *  # noqa: F401,F403
# Explicit re-exports — `import *` skips underscore-prefixed names, but callers/tests
# import these by name from the old path (relocation must stay transparent).
from exact_matching.matcher import (  # noqa: F401
    UserToTrialAttrMatcher,
    _resolve_omop_concepts,
)
