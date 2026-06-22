"""Trial-side therapy column names used by matching.

The single seam for the OMOP therapy migration. The search queryset
(`trials/querysets/trial.py`) and the per-trial matcher
(`trials/services/user_to_trial_attr_matcher.py`) read the trial's therapy
columns by the names defined here instead of hardcoding string literals. The
active profile flips from the legacy internal-code columns to the OMOP
`concept_id` columns based on the ``EXACT_OMOP_THERAPY`` setting — no dispatch or
matching logic changes.

Ported from CancerBot (CB epic #4447) to keep CB→EXACT ports cheap; CB owns the
upstream seam. EXACT is the downstream that flips to the OMOP profile at cutover.

Scope: this profile owns ONLY the trial-side column *names* read by matching. The
PATIENT-side codes are translated to concept_ids separately (see
``trials/services/omop/patient_therapy_codes.py``); both flip on the same flag.
This profile deliberately does NOT change dispatch order / matching semantics
(kept behavior-identical).

Only the three mapped levels (regimen / drug component / drug class) flip to
OMOP. ``planned_*`` / ``supportive_*`` stay on the legacy columns: their vocabs
have no ``omop_concept_id`` yet, so the backfill leaves the omop_* planned/
supportive columns empty — flipping them would silently drop the constraint.
Flip those two levels here once their vocabs gain concept_ids.
"""
from dataclasses import dataclass

from django.conf import settings


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
    # config-driven seam). These stay legacy under the OMOP profile (see module
    # docstring); flip BOTH seams together once their vocabs gain concept_ids.
    planned_therapies_required: str = 'planned_therapies_required'
    planned_therapies_excluded: str = 'planned_therapies_excluded'
    supportive_therapies_required: str = 'supportive_therapies_required'
    supportive_therapies_excluded: str = 'supportive_therapies_excluded'


# Legacy (internal-code) columns — the default.
LEGACY_THERAPY_MATCH_PROFILE = TherapyMatchProfile()

# OMOP cutover profile: the three mapped levels read the omop_* concept_id
# columns; planned_*/supportive_* intentionally stay legacy (see module docstring).
OMOP_THERAPY_MATCH_PROFILE = TherapyMatchProfile(
    therapies_required='omop_therapies_required',
    therapies_excluded='omop_therapies_excluded',
    therapy_components_required='omop_therapy_components_required',
    therapy_components_excluded='omop_therapy_components_excluded',
    therapy_types_required='omop_therapy_types_required',
    therapy_types_excluded='omop_therapy_types_excluded',
)


def omop_therapy_enabled() -> bool:
    """Whether trial therapy matching reads the OMOP concept_id columns.

    Off by default; capability-gated by the ``EXACT_OMOP_THERAPY`` setting. The
    patient-side code translation keys off the same flag.
    """
    return bool(getattr(settings, 'EXACT_OMOP_THERAPY', False))


def get_therapy_match_profile() -> TherapyMatchProfile:
    """Return the active profile for the current ``EXACT_OMOP_THERAPY`` setting."""
    return OMOP_THERAPY_MATCH_PROFILE if omop_therapy_enabled() else LEGACY_THERAPY_MATCH_PROFILE


class _ActiveTherapyMatchProfile:
    """Settings-aware view of the active profile.

    Resolves the ``EXACT_OMOP_THERAPY`` setting on every attribute access so the
    queryset and matcher pick up the flag without re-import (and tests can toggle
    it with ``override_settings``). Attribute writes are refused — swapping the
    profile is a settings change, never a mutation.
    """
    __slots__ = ()

    def __getattr__(self, name):
        return getattr(get_therapy_match_profile(), name)

    def __setattr__(self, name, value):
        raise AttributeError(
            "THERAPY_MATCH_PROFILE is read-only; set EXACT_OMOP_THERAPY to switch profiles."
        )


# The active profile. Reads trial therapy column names for matching; the ONLY
# place that names them. Swap profiles via the EXACT_OMOP_THERAPY setting.
THERAPY_MATCH_PROFILE = _ActiveTherapyMatchProfile()
