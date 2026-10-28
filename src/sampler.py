"""Timed sampling of one ammeter.

Timing model: sample k is due at ``t0 + k * interval`` on a monotonic clock. Each
wait targets that absolute deadline (not "interval after the previous sample"), so the
time a measurement takes never accumulates into drift. If a measurement overruns its
slot, the following samples run immediately to catch up and their lateness is recorded;
samples are never skipped, so the requested count is always honoured.
"""
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple

from src.ammeter_client import AmmeterError
from src.utils.config import SamplingConfig

logger = logging.getLogger(__name__)

# time.sleep() can overshoot by milliseconds (measured 2-3 ms on macOS, up to ~15 ms on
# Windows). So we sleep until this long before the deadline and busy-wait the rest on
# perf_counter. Measured at 20 Hz on macOS: 2 ms spin -> 1.6 ms mean lateness, 5 ms -> 0.01 ms.
# Cost: up to SPIN_SECONDS of CPU per sample. Raise it if lateness shows up on your platform.
SPIN_SECONDS = 0.005


class Measurable(Protocol):
    """Anything that can be sampled; AmmeterClient satisfies this."""
    name: str
    last_attempts: int  # attempts used by the most recent measure() call

    def measure(self) -> float: ...


@dataclass(frozen=True)
class Sample:
    """One attempted measurement. Times are seconds since the run started."""
    index: int
    scheduled_s: float           # when the sample was due
    started_s: float             # when the measurement actually began
    latency_s: float             # how long measure() took, retries included
    value: Optional[float]       # amperes; None if the sample failed
    error_type: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 1            # > 1 means the client had to retry (even if it then succeeded)

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def lateness_s(self) -> float:
        return self.started_s - self.scheduled_s


@dataclass(frozen=True)
class SamplingResult:
    """Everything recorded while sampling one ammeter."""
    ammeter: str
    started_at: str              # wall-clock UTC, ISO 8601
    planned_count: int
    interval_seconds: float
    elapsed_seconds: float
    samples: Tuple[Sample, ...]

    @property
    def values(self) -> List[float]:
        return [s.value for s in self.samples if s.value is not None]

    @property
    def failure_count(self) -> int:
        return sum(1 for s in self.samples if not s.ok)

    @property
    def error_summary(self) -> Dict[str, int]:
        """Failures grouped by exception type."""
        summary: Dict[str, int] = {}
        for s in self.samples:
            if s.error_type:
                summary[s.error_type] = summary.get(s.error_type, 0) + 1
        return summary

    @property
    def max_lateness_s(self) -> float:
        return max((s.lateness_s for s in self.samples), default=0.0)

    @property
    def mean_lateness_s(self) -> float:
        return sum(s.lateness_s for s in self.samples) / len(self.samples) if self.samples else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form, including derived timing/failure summaries."""
        data = asdict(self)
        data["samples"] = [dict(asdict(s), lateness_s=s.lateness_s) for s in self.samples]
        data.update(failure_count=self.failure_count, error_summary=self.error_summary,
                    max_lateness_s=self.max_lateness_s, mean_lateness_s=self.mean_lateness_s)
        return data


def _wait_until(deadline: float) -> None:
    """Block until perf_counter() reaches ``deadline``: coarse sleep first, then a short spin."""
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > SPIN_SECONDS:
            time.sleep(remaining - SPIN_SECONDS)


class Sampler:
    """Runs a sampling plan against a Measurable."""

    def __init__(self, plan: SamplingConfig):
        self._plan = plan

    def run(self, source: Measurable) -> SamplingResult:
        interval = self._plan.interval_seconds
        count = self._plan.count
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        samples: List[Sample] = []

        t0 = time.perf_counter()
        for index in range(count):
            scheduled = index * interval
            _wait_until(t0 + scheduled)
            samples.append(self._take_sample(source, index, scheduled, t0))
        elapsed = time.perf_counter() - t0

        result = SamplingResult(ammeter=source.name, started_at=started_at, planned_count=count,
                                interval_seconds=interval, elapsed_seconds=elapsed, samples=tuple(samples))
        logger.info("%s: %d/%d samples ok in %.3fs (max lateness %.1f ms)", source.name,
                    count - result.failure_count, count, elapsed, result.max_lateness_s * 1000)
        return result

    @staticmethod
    def _take_sample(source: Measurable, index: int, scheduled: float, t0: float) -> Sample:
        start = time.perf_counter()
        try:
            value = source.measure()
            error_type = error = None
        except AmmeterError as exc:  # a failed sample is data; other exceptions are bugs and propagate
            value, error_type, error = None, type(exc).__name__, str(exc)
            logger.warning("sample %d failed: %s", index, exc)
        latency = time.perf_counter() - start
        return Sample(index=index, scheduled_s=scheduled, started_s=start - t0, latency_s=latency,
                      value=value, error_type=error_type, error=error, attempts=source.last_attempts)
