"""Unit tests for normalize_goodness_weights (#154).

Goodness-score weights come from query params, so an all-zero or negative set
is attacker-reachable and would divide the score by zero (500 / DoS on the
core /trials/ search). The helper clamps negatives and falls back to equal
weights when nothing positive remains.
"""
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
