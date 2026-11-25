# Benefit / patient-burden / risk component scores share a 0–TRIAL_SCORE_MAX
# scale. The goodness-score formula (Trial.get_goodness_score and
# TrialQuerySet.with_goodness_score_optimized) normalizes each component
# against this bound, so values outside [0, TRIAL_SCORE_MAX] yield an
# out-of-range composite. Enforced by field validators (admin/full_clean)
# and DB CheckConstraints (all write paths, incl. bulk/update_or_create).
TRIAL_SCORE_MAX = 20
