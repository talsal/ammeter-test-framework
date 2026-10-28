"""Builders for test data shared by several test modules."""
from src.sampler import Sample, SamplingResult


def make_result(values, latency=0.01, attempts=None, name="fake"):
    """SamplingResult from a list of values; None entries become failed samples."""
    attempts = attempts or [1] * len(values)
    samples = tuple(
        Sample(index=i, scheduled_s=i * 0.1, started_s=i * 0.1 + 0.001 * i, latency_s=latency,
               value=v, error_type=None if v is not None else "AmmeterTimeoutError",
               error=None if v is not None else "x", attempts=attempts[i])
        for i, v in enumerate(values))
    return SamplingResult(ammeter=name, started_at="2026-01-01T00:00:00+00:00", planned_count=len(values),
                          interval_seconds=0.1, elapsed_seconds=1.0, samples=samples)
