"""Statistical analysis of a sampling run (standard library only).

Pure functions with no I/O: SamplingResult in, typed result objects out.
"""
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence

from src.sampler import SamplingResult
from src.utils.config import AnalysisConfig

# Consistency rating by coefficient of variation (stdev / |mean|), lowest bound first.
CONSISTENCY_BANDS = ((0.01, "excellent"), (0.05, "good"), (0.20, "fair"))
CONSISTENCY_POOR = "poor"
CONSISTENCY_UNKNOWN = "insufficient data"


@dataclass(frozen=True)
class Statistics:
    """Descriptive statistics of the measured currents, in amperes."""
    count: int
    mean: float
    median: float
    stdev: Optional[float]         # sample standard deviation (n-1); None for a single value
    minimum: float
    maximum: float
    cv: Optional[float]            # coefficient of variation = stdev / |mean|; None if undefined
    outlier_count: int             # values with |z-score| above the configured threshold

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisResult:
    """Statistics plus the reliability and timing facts needed to judge a run."""
    ammeter: str
    requested: int
    successful: int
    success_rate: float            # successful / requested, 0..1
    retried_count: int             # samples that needed more than one attempt (success or not)
    current: Optional[Statistics]  # None when no sample succeeded
    consistency: str               # rating of the CV, see CONSISTENCY_BANDS
    latency_mean_s: Optional[float]  # successful samples only
    latency_max_s: Optional[float]
    max_lateness_s: float          # worst deviation from the sampling schedule

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_statistics(values: Sequence[float], outlier_z_threshold: float = 3.0) -> Optional[Statistics]:
    """
    Descriptive statistics for ``values``; None if there are none.
    Raises ValueError for NaN/inf, which would otherwise silently poison every metric.
    """
    if not values:
        return None
    if not all(math.isfinite(v) for v in values):
        raise ValueError("values must be finite numbers")

    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) >= 2 else None
    cv = stdev / abs(mean) if stdev is not None and mean != 0 else None
    # z-score outliers; with no spread (stdev 0) nothing can be an outlier.
    outliers = 0
    if stdev:
        outliers = sum(1 for v in values if abs(v - mean) / stdev > outlier_z_threshold)
    return Statistics(count=len(values), mean=mean, median=statistics.median(values), stdev=stdev,
                      minimum=min(values), maximum=max(values), cv=cv, outlier_count=outliers)


def rate_consistency(cv: Optional[float]) -> str:
    """Maps a coefficient of variation to a label; lower CV means more repeatable readings."""
    if cv is None:
        return CONSISTENCY_UNKNOWN
    for upper_bound, label in CONSISTENCY_BANDS:
        if cv < upper_bound:
            return label
    return CONSISTENCY_POOR


def analyze(result: SamplingResult, config: AnalysisConfig = AnalysisConfig()) -> AnalysisResult:
    """Analyses one ammeter's sampling result."""
    good = [s for s in result.samples if s.ok]
    stats = compute_statistics(result.values, config.outlier_z_threshold)
    requested = len(result.samples)
    return AnalysisResult(
        ammeter=result.ammeter,
        requested=requested,
        successful=len(good),
        success_rate=len(good) / requested if requested else 0.0,
        retried_count=sum(1 for s in result.samples if s.attempts > 1),
        current=stats,
        consistency=rate_consistency(stats.cv if stats else None),
        latency_mean_s=statistics.mean(s.latency_s for s in good) if good else None,
        latency_max_s=max((s.latency_s for s in good), default=None),
        max_lateness_s=result.max_lateness_s,
    )
