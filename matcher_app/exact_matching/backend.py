"""ExactMatcher — the EXACT-published implementation of CB's MatcherBackend (E1.4).

CB installs the ``exact_matching`` package and flips
``MATCHER_BACKEND = "exact_matching.backend.ExactMatcher"`` (A2). This class
implements the same interface as CB's ``trials.matching.local.LocalMatcher``, but
its filter/scoring logic is the package's own ``TrialQuerySet`` (extracted in
E1.1–E1.3), so CB and EXACT run one matching codebase instead of two drifting
copies.

Host-agnostic by contract — it never imports CB (no circular dep):
- ``patient`` / ``prefs`` are consumed **duck-typed** (CB passes its
  ``ResolvedPatient`` / ``StudyPrefs``; EXACT tests pass equivalents).
- ``trials`` is whatever the host's ``Trial`` manager produces; both hosts use
  this package's ``TrialQuerySet``, so ``filtered_trials`` / ``with_*_optimized``
  resolve identically.

Scoring seam (decision B): the package's ``with_goodness_score_optimized`` is
**userless** — it takes four explicit weight floats, not a ``UserProfile`` (EXACT
has no User model). So the goodness weights are read as explicit fields off
``prefs`` (``benefit_weight`` etc.); the host resolves them from its own profile
BEFORE calling. Missing fields default to 25.0 each = equal weighting, matching
the queryset's own defaults, so a host that hasn't populated them yet still gets
a well-defined (uniform) score rather than a crash.
"""
from __future__ import annotations
from django.conf import settings
from django.db.models import QuerySet

from exact_matching import __version__

#: goodness-score weight fields read off `prefs`, in the queryset's positional order.
_WEIGHT_FIELDS = (
    "benefit_weight",
    "patient_burden_weight",
    "risk_weight",
    "distance_penalty_weight",
)


class ExactMatcher:
    version = f"exact-{__version__}"

    def search(self, trials: QuerySet, patient, prefs) -> QuerySet:
        pi = patient.as_patient_info()
        qs, _traces = trials.filtered_trials(
            search_options=prefs.query_params,
            study_info=prefs.study_info,
            patient_info=pi,
            add_traces=getattr(settings, "ADD_SEARCH_TRIALS_TRACES", False),
            search_type=prefs.search_type,
        )
        if "distance" not in qs.query.annotations:
            qs = qs.with_distance_optimized(
                patient.geo_point,
                recruitment_status=prefs.recruitment_status,
            )
        weights = {f: getattr(prefs, f, 25.0) for f in _WEIGHT_FIELDS}
        qs = qs.with_goodness_score_optimized(
            **weights,
            geo_point=patient.geo_point,
            recruitment_status=prefs.recruitment_status,
        )
        return qs

    # --- per-trial explain: documented contract, NOT view-wired yet (P0 follow-up). ---
    # Host-signature convergence (same bucket as decision B): the package is
    # userless, so match_status calls Trial.matching_type with patient_info by
    # KEYWORD. That is exact for EXACT's `matching_type(self, patient_info)`. CB's
    # current signature is `matching_type(user, patient_info=...)` with a required
    # `user`, so THIS CALL WILL NOT WORK IN CB until CB converges it (drop/default
    # `user`) — the CB companion, tracked with the StudyPrefs-weights change. The
    # keyword form is deliberate: in CB it fails loudly on the missing `user`
    # rather than silently binding patient_info into `user`. Nothing routes here
    # today, so there is no live break. high_risk_breakdown runs the package's own
    # matcher, so it is host-uniform already.
    def match_status(self, trial, patient):
        return trial.matching_type(patient_info=patient.as_patient_info())

    def attrs_to_fill_in(self, trial, patient, counts: dict):
        return trial.attrs_to_fill_in(counts)

    def high_risk_breakdown(self, trial, patient):
        from exact_matching.matcher import UserToTrialAttrMatcher
        return UserToTrialAttrMatcher(
            trial, patient.as_patient_info()
        ).high_risk_mcl_criteria_breakdown()
