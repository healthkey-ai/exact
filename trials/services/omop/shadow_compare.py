"""Shadow comparison: legacy therapy vs its OMOP mapping (#4446).

A read-only safety gate for the OMOP cutover. For each trial it:
  1. re-derives the expected omop_* values from the LEGACY columns (the same
     conversion the backfill uses) and compares them to the STORED omop_* columns
     -> "drift" (backfill/write-sync is stale or never ran), and
  2. counts LEGACY codes that don't map to any OMOP concept -> "divergence risk":
     a trial whose legacy required column has an unmapped code would match
     differently once reads flip to the (empty-for-that-code) omop column.

Output feeds both cutover-readiness and the SME mapping review (top unmapped codes
by trial frequency). No writes; no schema change.

Ported from CancerBot (CB epic #4447). DIVERGENCE FROM CB: the CB version also
shadow-compares the OMOP demographics columns (ethnicity/gender). EXACT has not
yet ported the demographics OMOP machinery (a separate, later phase), so this
copy is therapy-only. Re-add the demographics branch when EXACT ports it.
"""
from collections import Counter

from trials.services.omop.therapy_concept_mapper import build_omop_columns


def _differs(stored, computed):
    # computed is the source of truth shape: list columns -> order-insensitive and
    # None (a NULLed list column) treated as the empty list; scalars -> direct.
    if isinstance(computed, list):
        return sorted(stored or []) != sorted(computed)
    return stored != computed


def compare_trial(trial):
    """Return (drift, unmapped) for one trial.

    drift: {omop_column: (stored, computed)} where the stored column differs from
        what the mapping computes from the current legacy values.
    unmapped: {legacy_column: [codes]} legacy codes with no OMOP concept.
    """
    t_values, t_unmapped = build_omop_columns(trial)

    drift = {}
    for col, computed in t_values.items():
        stored = getattr(trial, col)
        if _differs(stored, computed):
            drift[col] = (stored, computed)

    return drift, dict(t_unmapped)


def compare_corpus(queryset):
    """Aggregate compare_trial over a queryset. Returns a report dict."""
    scanned = 0
    drift_by_col = Counter()        # omop column -> # trials with stale stored value
    drifted_trials = 0
    unmapped_by_col = Counter()     # legacy column -> # trials with >=1 unmapped code
    unmapped_codes = Counter()      # legacy code -> trial frequency
    divergent_trials = 0            # trials with any unmapped code (cutover would differ)

    for trial in queryset.iterator():
        scanned += 1
        drift, unmapped = compare_trial(trial)
        if drift:
            drifted_trials += 1
            for col in drift:
                drift_by_col[col] += 1
        if unmapped:
            divergent_trials += 1
            trial_codes = set()
            for col, codes in unmapped.items():
                unmapped_by_col[col] += 1
                trial_codes.update(codes)
            # count each unmapped code once per trial (it's a trial-frequency metric)
            for code in trial_codes:
                unmapped_codes[code] += 1

    return {
        'scanned': scanned,
        'drifted_trials': drifted_trials,
        'drift_by_col': dict(drift_by_col),
        'divergent_trials': divergent_trials,
        'unmapped_by_col': dict(unmapped_by_col),
        'top_unmapped_codes': unmapped_codes.most_common(25),
    }
