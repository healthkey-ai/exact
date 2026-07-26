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
PATIENT side is NOT translated by EXACT — EXACT is stateless and owns no patient
crosswalk: when the flag is on, the consumer (PROMOP) supplies the patient's
therapies already as OMOP concept_ids (pre-expanded), and matching is a direct
concept_id overlap against these columns. The flag therefore only swaps which
trial columns are read; both sides must speak the same vocabulary, which is the
consumer's responsibility. This profile deliberately does NOT change dispatch
order / matching semantics (kept behavior-identical).

Regimen, drug-component, and supportive flip to OMOP concept_ids under the OMOP
profile. The columns that stay on the legacy (internal-code) columns:
- ``therapy_types_*`` (drug class / component-category): types are deliberately
  NOT OMOP-mapped (HemOnc's class structure is poor) — EXACT keeps CB's own
  category vocabulary and matches types through the CB graph
  ``categories ↔ components ↔ therapies`` (the matcher reverse-maps the patient's
  component concept_ids back to internal components, then to CB categories, and
  overlaps those against the legacy ``therapy_types_*`` columns). See #197.
- ``planned_*``: stays legacy — ``PlannedTherapy`` has no ``omop_concept_id`` and
  76/85 planned codes are drug-classes/modalities, so ``omop_planned_therapies_*``
  is empty and flipping would silently drop the constraint (#230 decision pending).

``supportive_*`` flips to ``omop_supportive_therapies_*`` under the OMOP profile
(#228): the dedicated supportive matching (#4449 — the matcher's
``_match_supportive_therapies`` handler + the queryset
``eligible_for_supportive_therapies``) reads the trial columns via this profile,
and the backfill populates the omop columns (cb#4590). Patient side: promop does
NOT yet emit supportive concept_ids (promop#230) — until it does, the patient
supportive list is empty and the filter fail-opens on empty patient values
(``eligible_for_required_and_excluded_lists`` returns the queryset unchanged), so
the trial-side flip is a safe no-op. When promop#230 lands, matching works
end-to-end with no EXACT change; the #221 gate validates coverage before the prod
flip. See #231.
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
    # config-driven seam). planned stays legacy; supportive flips in the OMOP
    # profile below (both seams together) — see module docstring.
    planned_therapies_required: str = 'planned_therapies_required'
    planned_therapies_excluded: str = 'planned_therapies_excluded'
    supportive_therapies_required: str = 'supportive_therapies_required'
    supportive_therapies_excluded: str = 'supportive_therapies_excluded'


# Legacy (internal-code) columns — the default.
LEGACY_THERAPY_MATCH_PROFILE = TherapyMatchProfile()

# OMOP cutover profile: regimen + component + supportive read the omop_* concept_id
# columns. therapy_types_* and planned_* intentionally stay legacy (see module
# docstring): types are matched via the CB category graph, not OMOP concept_ids;
# planned has no concept_ids yet (#230).
OMOP_THERAPY_MATCH_PROFILE = TherapyMatchProfile(
    therapies_required='omop_therapies_required',
    therapies_excluded='omop_therapies_excluded',
    therapy_components_required='omop_therapy_components_required',
    therapy_components_excluded='omop_therapy_components_excluded',
    # Supportive flips too (#228): the dedicated supportive path (#4449 — the
    # _match_supportive_therapies matcher handler + eligible_for_supportive_therapies
    # queryset) reads the trial supportive columns via THIS profile, and the backfill
    # populates omop_supportive_therapies_* (cb#4590). The consumer supplies the
    # patient's supportive therapies as concept_ids under the flag; the cutover gate
    # (#221) validates omop_supportive coverage (no_omop supportive codes backfill to
    # [] -> would silently drop the constraint) before the flag is flipped on prod.
    supportive_therapies_required='omop_supportive_therapies_required',
    supportive_therapies_excluded='omop_supportive_therapies_excluded',
)


def omop_therapy_enabled() -> bool:
    """Whether trial therapy matching reads the OMOP concept_id columns.

    Off by default; capability-gated by the ``EXACT_OMOP_THERAPY`` setting.
    Matching is a direct concept_id overlap — patient therapies arrive as
    concept_ids from the consumer; EXACT does not translate them.
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
