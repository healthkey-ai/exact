"""Unit tests for normalize_goodness_weights (#154).

Goodness-score weights come from query params, so an all-zero or negative set
is attacker-reachable and would divide the score by zero (500 / DoS on the
core /trials/ search). The helper clamps negatives and falls back to equal
weights when nothing positive remains.
"""
import math

from trials.services.utils import normalize_goodness_weights


def test_normal_weights_pass_through_as_floats():
    assert normalize_goodness_weights(40, 30, 20, 10) == (40.0, 30.0, 20.0, 10.0)


def test_all_zero_falls_back_to_equal_weights():
    assert normalize_goodness_weights(0, 0, 0, 0) == (25.0, 25.0, 25.0, 25.0)


def test_negative_weights_clamped_to_zero():
    # -5 clamps to 0; the rest stay; sum stays positive so no fallback.
    assert normalize_goodness_weights(-5, 10, 0, 0) == (0.0, 10.0, 0.0, 0.0)


def test_all_negative_falls_back_to_equal_weights():
    assert normalize_goodness_weights(-1, -2, -3, -4) == (25.0, 25.0, 25.0, 25.0)


def test_non_finite_weights_are_dropped():
    # inf/-inf/nan would otherwise propagate to a NaN score -> 500 on cast.
    assert normalize_goodness_weights(float('inf'), 10, 0, 0) == (0.0, 10.0, 0.0, 0.0)
    assert normalize_goodness_weights(float('nan'), 10, 0, 0) == (0.0, 10.0, 0.0, 0.0)
    assert normalize_goodness_weights(float('-inf'), 10, 0, 0) == (0.0, 10.0, 0.0, 0.0)


def test_all_non_finite_falls_back_to_equal_weights():
    inf, nan = float('inf'), float('nan')
    assert normalize_goodness_weights(inf, -inf, nan, inf) == (25.0, 25.0, 25.0, 25.0)


def test_result_always_finite_and_sum_positive():
    import math
    for weights in [(0, 0, 0, 0), (-1, -1, -1, -1), (0.0, 0.0, 0.0, 0.0),
                    (float('inf'), float('nan'), float('-inf'), 0)]:
        result = normalize_goodness_weights(*weights)
        assert all(math.isfinite(w) for w in result)
        assert sum(result) > 0


def test_weights_that_are_finite_apart_and_infinite_together():
    # `1e308` twice sums to inf. The score is built from these numbers in
    # SQL, where a term that large is not a float8 at all: Postgres rejects
    # the parameter ("out of range for type double precision") and the
    # search 500s. Measured against a live instance before this:
    # `benefitWeight=1e308&patientBurdenWeight=1e308` -> 500.
    weights = normalize_goodness_weights(1e308, 1e308, 25, 25)

    assert math.isfinite(sum(weights))
    # Scaled by the largest, so the RATIO — the only thing the score reads —
    # is what it was: the two giants equal, the two 25s vanishingly small
    # beside them, which is what asking for 1e308 means.
    assert weights[0] == weights[1] == 1.0
    assert weights[2] == weights[3] < 1e-300


def test_scaling_leaves_ordinary_weights_alone():
    # The scale-down only fires on a sum that is not finite. Everything else
    # arrives exactly as the reader set it, which is what every other test
    # here asserts — this one says so directly.
    assert normalize_goodness_weights(40, 30, 20, 10) == (40.0, 30.0, 20.0, 10.0)
