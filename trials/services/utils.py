import math

from glom import glom


def disease_attr_applies(attr_disease, patient_disease) -> bool:
    """True if a mapping entry's `disease` restriction includes the patient.

    `attr_disease` is the `disease` value from a `USER_TO_TRIAL_ATTRS_MAPPING`
    entry — a single disease code (str) or a collection of codes. Replaces
    the inline `disease_code in trial_attr_meta["disease"]` checks at the
    matcher, queryset, mapper, trial-detail, and explainer call sites (#43),
    which silently substring-match when `attr_disease` is a string
    (`'M' in 'MM'` is True), so adding a new disease code that's a
    substring of an existing one would have caused subtle cross-disease
    pollution.
    """
    if isinstance(attr_disease, (list, tuple, set)):
        return patient_disease in attr_disease
    return attr_disease == patient_disease


def glom_or_none(data, key):
    try:
        return glom(data, key)
    except KeyError:
        return None


def to_date(value):
    if not value:
        return None
    parts = value.split('-')
    if len(parts) == 2:
        parts.append('01')
        return '-'.join(parts)
    else:
        return value


#: The query-parameter name for each weight kwarg of
#: `with_goodness_score_optimized`. What a missing or unreadable one falls
#: back to is `DEFAULT_GOODNESS_WEIGHT` below: equal weights, which is what
#: the score means with no opinion supplied, and what CB stores as its own
#: default (`trials/models.py`, `default=25.0`).
GOODNESS_WEIGHT_PARAMS = {
    'benefit_weight': 'benefitWeight',
    'patient_burden_weight': 'patientBurdenWeight',
    'risk_weight': 'riskWeight',
    'distance_penalty_weight': 'distancePenaltyWeight',
}
DEFAULT_GOODNESS_WEIGHT = 25.0


def parse_goodness_weights(params):
    """Read the four goodness weights out of query params, one at a time.

    One unreadable value used to take the other three with it: the view
    parsed all four inside a single `try`, so `riskWeight=abc` scored the
    search 25/25/25/25 and silently discarded a benefit weight that had
    arrived perfectly well.

    That was never a policy, just the shape of the `try`. A layer down,
    `normalize_goodness_weights` clamps a NEGATIVE weight to zero and drops a
    non-finite one without touching its neighbours, so which of the reader's
    four survived a bad value depended on what KIND of bad it was — `-5` kept
    the others, `abc` did not.

    What this fixes is that: the NEIGHBOURS now survive either way. The bad
    field itself still costs different amounts — unreadable defaults to 25,
    negative clamps to 0 — and that divergence is pinned by a test rather
    than papered over. (The layer below is not purely per-field either: an
    all-dead set falls back to equal weights as a whole.)

    Independent inputs, independently defaulted: a value that cannot be read
    says nothing about the three beside it. This is the smaller claim, not
    the best one — refusing the request with a 400 naming the bad parameter
    is what this same view already does for `trial_ids`, and what CB does for
    these four by validating them as `DecimalField`s. Per-field simply
    discards strictly less of what the reader sent than the reset did.
    """
    weights = {}
    for kwarg, name in GOODNESS_WEIGHT_PARAMS.items():
        raw = params.get(name)
        try:
            weights[kwarg] = DEFAULT_GOODNESS_WEIGHT if raw is None else float(raw)
        except (TypeError, ValueError):
            weights[kwarg] = DEFAULT_GOODNESS_WEIGHT
    return weights


def normalize_goodness_weights(
    benefit_weight, patient_burden_weight, risk_weight, distance_penalty_weight
):
    """Clamp goodness-score weights to a safe, non-degenerate set.

    Weights come from query params, so negative values, an all-zero set, and
    non-finite floats (inf/nan via `float('inf')`) are all attacker-reachable.
    A zero sum divides the score by zero, and inf/nan propagate to a NaN score
    that raises when cast to IntegerField in Postgres — both 500 the search
    endpoint. Guard here: drop non-finite values, clamp negatives to 0, and if
    nothing positive remains fall back to equal weights (25/25/25/25).
    Returns the four weights as floats.
    """
    weights = []
    # Named as `parse_goodness_weights` names them, and as
    # `with_goodness_score_optimized` takes them, so the four travel through
    # all three by the same names rather than being re-spelled at each seam.
    for w in (
        benefit_weight,
        patient_burden_weight,
        risk_weight,
        distance_penalty_weight,
    ):
        f = float(w)
        weights.append(max(0.0, f) if math.isfinite(f) else 0.0)
    if sum(weights) <= 0:
        weights = [25.0, 25.0, 25.0, 25.0]
    # Finite each, infinite together: `1e308` twice sums to inf. The score is
    # built from these numbers in SQL, and a term that large is not a float8
    # — Postgres rejects the parameter outright ("is out of range for type
    # double precision") and the search 500s. Measured both ways: against a
    # live instance, `benefitWeight=1e308&patientBurdenWeight=1e308` returns
    # 500, and the queryset test for this raises `DataError` without the
    # lines below.
    #
    # Scaling by the largest is free: the score divides by the sum, so the
    # weights matter only in RATIO, and dividing all four by the same number
    # changes nothing about the result. It caps the sum at 4 and cannot
    # overflow, since nothing here exceeds its own largest.
    if not math.isfinite(sum(weights)):
        largest = max(weights)
        weights = [w / largest for w in weights]
    return tuple(weights)


def get_overlap(a, b):
    def list_of_str(values):
        return [str(x) for x in values]

    return list(set(list_of_str(a)) & set(list_of_str(b)))
