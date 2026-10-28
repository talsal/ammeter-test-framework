"""Tests for the analysis module, checked against hand-computed values."""
import json
import math

import pytest

from src.analysis import (
    CONSISTENCY_POOR,
    CONSISTENCY_UNKNOWN,
    analyze,
    compute_statistics,
    rate_consistency,
)
from src.utils.config import AnalysisConfig
from factories import make_result


# ---------------------------------------------------------------- compute_statistics

def test_basic_statistics_match_hand_calculation():
    s = compute_statistics([1, 2, 3, 4, 5])
    assert (s.count, s.mean, s.median, s.minimum, s.maximum) == (5, 3, 3, 1, 5)
    assert s.stdev == pytest.approx(math.sqrt(2.5))       # sample (n-1) stdev: sqrt(10 / 4)
    assert s.cv == pytest.approx(math.sqrt(2.5) / 3)
    assert s.outlier_count == 0


def test_median_of_an_even_count_is_the_midpoint():
    assert compute_statistics([1, 2, 3, 4]).median == 2.5


def test_median_ignores_input_order():
    assert compute_statistics([9, 1, 5]).median == 5


def test_empty_input_gives_none():
    assert compute_statistics([]) is None


def test_single_value_has_undefined_spread():
    s = compute_statistics([2.5])
    assert (s.count, s.mean, s.median, s.minimum, s.maximum) == (1, 2.5, 2.5, 2.5, 2.5)
    assert s.stdev is None and s.cv is None and s.outlier_count == 0


def test_identical_values_have_zero_spread():
    s = compute_statistics([4.0, 4.0, 4.0])
    assert s.stdev == 0 and s.cv == 0 and s.outlier_count == 0


def test_zero_mean_leaves_cv_undefined():
    s = compute_statistics([-1.0, 1.0])
    assert s.mean == 0 and s.stdev == pytest.approx(math.sqrt(2)) and s.cv is None


def test_cv_uses_the_absolute_mean_for_negative_currents():
    assert compute_statistics([-2.0, -4.0]).cv > 0


def test_outlier_is_flagged_by_z_score():
    s = compute_statistics([1.0] * 19 + [100.0])           # z of the 100 is about 4.25
    assert s.outlier_count == 1


def test_outlier_threshold_is_configurable():
    values = [1.0] * 19 + [100.0]
    assert compute_statistics(values, outlier_z_threshold=5.0).outlier_count == 0


def test_zscore_cannot_flag_a_single_outlier_in_a_tiny_sample():
    # Documents a known limitation: with sample stdev, max |z| is (n-1)/sqrt(n), 2.85 for n=10,
    # so a single wild value among 10 can never exceed a threshold of 3.
    assert compute_statistics([1.0] * 9 + [1000.0]).outlier_count == 0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(bad):
    with pytest.raises(ValueError):
        compute_statistics([1.0, bad])


# ---------------------------------------------------------------- consistency rating

@pytest.mark.parametrize("cv, label", [
    (0.0, "excellent"), (0.009, "excellent"), (0.01, "good"), (0.049, "good"),
    (0.05, "fair"), (0.199, "fair"), (0.20, CONSISTENCY_POOR), (3.0, CONSISTENCY_POOR),
    (None, CONSISTENCY_UNKNOWN),
])
def test_rate_consistency(cv, label):
    assert rate_consistency(cv) == label


# ---------------------------------------------------------------- analyze

def test_analyze_combines_statistics_and_reliability():
    a = analyze(make_result([10.0, 10.3, None, 9.7], latency=0.02, attempts=[1, 2, 3, 1]))
    assert (a.ammeter, a.requested, a.successful) == ("fake", 4, 3)
    assert a.success_rate == pytest.approx(0.75)
    assert a.retried_count == 2                         # samples 1 and 2 needed retries
    assert a.current.count == 3 and a.current.mean == pytest.approx(10.0)
    assert a.consistency == "good"                      # CV = 0.3 / 10 = 3%
    assert a.latency_mean_s == pytest.approx(0.02) and a.latency_max_s == pytest.approx(0.02)
    assert a.max_lateness_s == pytest.approx(0.003)     # worst sample is index 3


def test_analyze_when_every_sample_failed():
    a = analyze(make_result([None, None]))
    assert a.successful == 0 and a.success_rate == 0
    assert a.current is None and a.consistency == CONSISTENCY_UNKNOWN
    assert a.latency_mean_s is None and a.latency_max_s is None


def test_analyze_with_no_samples_does_not_divide_by_zero():
    a = analyze(make_result([]))
    assert a.requested == 0 and a.success_rate == 0.0 and a.current is None


def test_analyze_uses_the_configured_outlier_threshold():
    result = make_result([1.0] * 19 + [100.0])
    assert analyze(result, AnalysisConfig(outlier_z_threshold=3.0)).current.outlier_count == 1
    assert analyze(result, AnalysisConfig(outlier_z_threshold=5.0)).current.outlier_count == 0


def test_analysis_result_is_json_serialisable():
    data = json.loads(json.dumps(analyze(make_result([1.0, 2.0, 3.0])).to_dict()))
    assert data["current"]["median"] == 2.0 and data["consistency"] == "poor"
