"""Compares the ammeters of one run: which is the most reliable and the most precise?

What this can and cannot say: there is no reference ("true") current, and the ammeters
measure unrelated quantities in very different ranges, so *accuracy* cannot be assessed.
What can be quantified is
  * reliability: the share of requests that produced a valid reading, and
  * precision (repeatability): the spread of readings relative to their size, as the
    coefficient of variation (CV), with a bootstrap confidence interval so that two
    ammeters are only called different when the data support it.
Ranking: highest success rate first; ties are broken by the lowest CV.
"""
import random
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.results_store import RunRecord

BOOTSTRAP_RESAMPLES = 1000
CONFIDENCE = 0.95
MIN_SAMPLES_FOR_INTERVAL = 5
NO_REFERENCE_NOTE = ("Precision (CV) measures repeatability, not accuracy: there is no reference value, and "
                     "the ammeters measure different quantities and ranges.")


@dataclass(frozen=True)
class AmmeterScore:
    ammeter: str
    rank: int
    successful: int
    requested: int
    success_rate: float
    cv: Optional[float]
    cv_low: Optional[float]            # bootstrap interval around the CV
    cv_high: Optional[float]
    relative_iqr: Optional[float]      # (Q3 - Q1) / |median|: a spread measure robust to outliers
    latency_mean_s: Optional[float]


@dataclass(frozen=True)
class AmmeterRanking:
    scores: Tuple[AmmeterScore, ...]           # best first
    most_reliable: Optional[str]               # None when no ammeter produced any reading
    tied_with_best: Tuple[str, ...]            # ammeters whose CV interval overlaps the best one's
    notes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def bootstrap_cv_interval(values: Sequence[float], rng: random.Random, resamples: int = BOOTSTRAP_RESAMPLES,
                          confidence: float = CONFIDENCE) -> Optional[Tuple[float, float]]:
    """
    Percentile-bootstrap interval for the coefficient of variation: resample the readings
    with replacement many times, compute the CV of each resample, take the middle
    ``confidence`` share. None if there are too few readings to say anything.
    """
    if len(values) < MIN_SAMPLES_FOR_INTERVAL:
        return None
    cvs = []
    for _ in range(resamples):
        sample = rng.choices(values, k=len(values))
        mean = statistics.mean(sample)
        if mean != 0:
            cvs.append(statistics.stdev(sample) / abs(mean))
    if not cvs:
        return None
    cvs.sort()
    low = cvs[int((1 - confidence) / 2 * len(cvs))]
    high = cvs[min(len(cvs) - 1, int((1 + confidence) / 2 * len(cvs)))]
    return low, high


def relative_iqr(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return (q3 - q1) / abs(median) if median != 0 else None


def _intervals_overlap(a: AmmeterScore, b: AmmeterScore) -> bool:
    return a.cv_low <= b.cv_high and b.cv_low <= a.cv_high


def compare_ammeters(record: RunRecord, seed: int = 0, resamples: int = BOOTSTRAP_RESAMPLES) -> AmmeterRanking:
    """
    Ranks the ammeters of a run. Deterministic for a given record: the bootstrap uses a
    seeded random generator, so reloading a stored run always gives the same intervals.
    """
    rng = random.Random(seed)
    scores: List[AmmeterScore] = []
    for name in sorted(record.ammeters):
        analysis = record.ammeters[name]["analysis"]
        samples = record.ammeters[name]["sampling"]["samples"]
        values = [s["value"] for s in samples if s["value"] is not None]
        interval = bootstrap_cv_interval(values, rng, resamples)
        scores.append(AmmeterScore(
            ammeter=name, rank=0, successful=analysis["successful"], requested=analysis["requested"],
            success_rate=analysis["success_rate"], cv=(analysis["current"] or {}).get("cv"),
            cv_low=interval[0] if interval else None, cv_high=interval[1] if interval else None,
            relative_iqr=relative_iqr(values), latency_mean_s=analysis["latency_mean_s"]))

    # best first: reliability, then precision; ammeters without a CV go last
    scores.sort(key=lambda s: (-s.success_rate, s.cv if s.cv is not None else float("inf"), s.ammeter))
    scores = [AmmeterScore(**{**asdict(s), "rank": i}) for i, s in enumerate(scores, 1)]

    best = scores[0] if scores and scores[0].successful > 0 else None
    tied: Tuple[str, ...] = ()
    notes = [NO_REFERENCE_NOTE]
    if best is not None and best.cv_low is not None:
        tied = tuple(s.ammeter for s in scores[1:]
                     if s.success_rate == best.success_rate and s.cv_low is not None
                     and _intervals_overlap(best, s))
        if tied:
            notes.append(f"{best.ammeter} is not statistically distinguishable from {', '.join(tied)}: "
                         f"the {CONFIDENCE:.0%} intervals of their CV overlap.")
    by_cv = sorted((s for s in scores if s.cv is not None), key=lambda s: s.cv)
    by_iqr = sorted((s for s in scores if s.relative_iqr is not None), key=lambda s: s.relative_iqr)
    if by_cv and by_iqr and by_cv[0].ammeter != by_iqr[0].ammeter:
        notes.append(f"By CV, {by_cv[0].ammeter} is the most repeatable, but by IQR/median it is "
                     f"{by_iqr[0].ammeter}: extreme values (a heavy-tailed distribution) dominate the CV.")
    return AmmeterRanking(scores=tuple(scores), most_reliable=best.ammeter if best else None,
                          tied_with_best=tied, notes=tuple(notes))
