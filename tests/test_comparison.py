"""Tests for the cross-ammeter ranking (reliability, precision, bootstrap interval)."""
import random

import pytest

from factories import make_record
from src.comparison import NO_REFERENCE_NOTE, bootstrap_cv_interval, compare_ammeters, relative_iqr
from src.analysis import compute_statistics

STEADY = [10.0, 10.1, 9.9, 10.05, 9.95] * 4       # CV about 1%
NOISY = [5.0, 15.0, 8.0, 12.0, 10.0] * 4           # CV about 36%


def ranking(*ammeters, **kwargs):
    return compare_ammeters(make_record(means=ammeters), **kwargs)


def order(r):
    return [s.ammeter for s in r.scores]


# ---------------------------------------------------------------- bootstrap interval

def test_interval_contains_the_point_estimate():
    values = [9, 10, 11, 10, 10, 9, 11, 10, 12, 8]
    low, high = bootstrap_cv_interval(values, random.Random(0))
    assert low < compute_statistics(values).cv < high


def test_interval_narrows_with_more_data():
    base = [9, 10, 11, 10, 10, 9, 11, 10, 12, 8]
    small = bootstrap_cv_interval(base, random.Random(0))
    large = bootstrap_cv_interval(base * 8, random.Random(0))
    assert (large[1] - large[0]) < (small[1] - small[0]) / 2


def test_interval_is_reproducible_for_a_seed():
    values = [9, 10, 11, 10, 10, 9, 11]
    assert bootstrap_cv_interval(values, random.Random(3)) == bootstrap_cv_interval(values, random.Random(3))


def test_too_few_values_give_no_interval():
    assert bootstrap_cv_interval([1.0, 2.0, 3.0], random.Random(0)) is None


def test_relative_iqr():
    assert relative_iqr([1, 2, 3, 4, 5]) == pytest.approx((4 - 2) / 3)   # Q1=2, Q3=4, median=3
    assert relative_iqr([7.0]) is None
    assert relative_iqr([-1, 0, 1]) is None                             # median 0: undefined


# ---------------------------------------------------------------- ranking

def test_lower_cv_ranks_first_when_reliability_is_equal():
    r = ranking(("noisy", NOISY), ("steady", STEADY))
    assert order(r) == ["steady", "noisy"] and [s.rank for s in r.scores] == [1, 2]
    assert r.most_reliable == "steady"
    assert r.scores[0].cv < 0.02 < 0.3 < r.scores[1].cv


def test_reliability_beats_precision():
    flaky_but_precise = [v if i % 5 else None for i, v in enumerate(STEADY)]      # 80% success
    r = ranking(("flaky", flaky_but_precise), ("noisy", NOISY))
    assert order(r) == ["noisy", "flaky"] and r.most_reliable == "noisy"
    assert r.scores[1].success_rate == pytest.approx(0.8)


def test_ammeter_without_readings_is_ranked_last_and_never_most_reliable():
    r = ranking(("dead", [None] * 10), ("noisy", NOISY))
    assert order(r) == ["noisy", "dead"]
    assert r.scores[1].cv is None and r.scores[1].cv_low is None


def test_nothing_measured_at_all():
    r = ranking(("a", [None, None]), ("b", [None, None]))
    assert r.most_reliable is None and r.tied_with_best == ()
    assert NO_REFERENCE_NOTE in r.notes


def test_equivalent_ammeters_are_reported_as_statistically_tied():
    r = ranking(("a", NOISY), ("b", list(reversed(NOISY))), ("steady", STEADY))
    assert r.most_reliable == "steady" and r.tied_with_best == ()           # clearly better than both
    r = ranking(("a", NOISY), ("b", NOISY[1:] + NOISY[:1]))
    assert r.tied_with_best == (r.scores[1].ammeter,)
    assert any("not statistically distinguishable" in note for note in r.notes)


def test_clearly_different_ammeters_are_not_tied():
    r = ranking(("steady", STEADY), ("noisy", NOISY))
    assert r.tied_with_best == ()
    assert r.scores[0].cv_high < r.scores[1].cv_low                          # intervals do not overlap


def test_heavy_tail_makes_cv_and_iqr_disagree_and_says_so():
    spike = [10.0] * 19 + [100.0]                    # almost constant, one extreme value: huge CV, zero IQR
    spread = [8.0, 9.0, 10.0, 11.0, 12.0] * 4         # moderate CV, wider IQR
    r = ranking(("spike", spike), ("spread", spread))
    assert r.most_reliable == "spread"
    assert any("by IQR/median it is spike" in note for note in r.notes)


def test_no_heavy_tail_note_when_cv_and_iqr_agree():
    r = ranking(("steady", STEADY), ("noisy", NOISY))
    assert not any("IQR/median" in note for note in r.notes)


def test_the_note_compares_precision_only_not_the_overall_rank():
    # "flaky" is outranked on reliability, but it is still the most repeatable by both measures
    r = ranking(("flaky", [v if i % 2 else None for i, v in enumerate(STEADY)]), ("noisy", NOISY))
    assert r.most_reliable == "noisy"
    assert not any("IQR/median" in note for note in r.notes)


def test_ranking_is_deterministic_and_always_carries_the_no_reference_caveat():
    a = ranking(("x", NOISY), ("y", STEADY))
    b = ranking(("x", NOISY), ("y", STEADY))
    assert a == b and NO_REFERENCE_NOTE in a.notes


def test_a_single_ammeter_can_be_ranked():
    r = ranking(("only", STEADY))
    assert order(r) == ["only"] and r.tied_with_best == ()
