import pytest
from revu_bench.stats import (
    bootstrap_mean,
    cliffs_delta,
    cliffs_magnitude,
    holm,
    paired_bootstrap,
    wilcoxon_signed_rank,
)


def test_exact_wilcoxon_matches_the_textbook_value() -> None:
    # All five differences positive: W+ = 15, P(W+ >= 15) = 1/32, two-sided 2/32.
    result = wilcoxon_signed_rank([1, 2, 3, 4, 5], [0, 0, 0, 0, 0])
    assert result.method == "exact"
    assert result.statistic == 15
    assert result.p_value == pytest.approx(0.0625)


def test_wilcoxon_is_symmetric_and_drops_zero_differences() -> None:
    a, b = [0.5, 0.7, 0.2, 0.9, 0.4, 0.8], [0.4, 0.4, 0.3, 0.5, 0.4, 0.1]
    forward = wilcoxon_signed_rank(a, b)
    backward = wilcoxon_signed_rank(b, a)
    assert forward.n == 5  # one tie dropped
    assert forward.p_value == pytest.approx(backward.p_value)


def test_wilcoxon_uses_the_normal_approximation_for_ties_and_large_samples() -> None:
    a = [1.0] * 30 + [0.0] * 10
    b = [0.0] * 40
    result = wilcoxon_signed_rank(a, b)
    assert result.method == "normal"
    assert result.p_value < 1e-6
    assert wilcoxon_signed_rank([0.3, 0.3], [0.3, 0.3]).p_value == 1.0


def test_paired_bootstrap_of_a_constant_difference_is_that_constant() -> None:
    ci = paired_bootstrap([0.6, 0.7, 0.8], [0.5, 0.6, 0.7], resamples=2000)
    assert ci.estimate == pytest.approx(0.1)
    assert ci.low == pytest.approx(0.1) and ci.high == pytest.approx(0.1)


def test_bootstrap_interval_contains_the_mean_and_is_reproducible() -> None:
    values = [0.1, 0.4, 0.35, 0.8, 0.5, 0.2, 0.9]
    first, second = bootstrap_mean(values, seed=7), bootstrap_mean(values, seed=7)
    assert first.low <= first.estimate <= first.high
    assert (first.low, first.high) == (second.low, second.high)
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0])


def test_holm_adjusts_in_step_down_order_and_keeps_input_order() -> None:
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5]) == [0.5]


def test_cliffs_delta_and_its_magnitude_labels() -> None:
    assert cliffs_delta([2, 3], [1, 1]) == 1.0
    assert cliffs_delta([1, 1], [2, 3]) == -1.0
    assert cliffs_delta([1, 2], [1, 2]) == 0.0
    assert [cliffs_magnitude(d) for d in (0.1, 0.2, 0.4, 0.9)] == [
        "negligible",
        "small",
        "medium",
        "large",
    ]
