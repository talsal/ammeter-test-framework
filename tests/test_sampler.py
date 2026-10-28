"""Tests for the Sampler. Timing tests use real time with generous tolerances."""
import json
import time
from datetime import datetime

import pytest

from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.ammeter_client import AmmeterClient, AmmeterTimeoutError
from src.sampler import Sampler
from src.utils.config import AmmeterConfig, ClientConfig, SamplingConfig


class FakeSource:
    """Scripted Measurable: each script entry is a value or an exception to raise."""
    name = "fake"

    def __init__(self, script=None, latency=0.0):
        self._script = list(script or [])
        self._latency = latency
        self.calls = 0
        self.last_attempts = 1

    def measure(self) -> float:
        time.sleep(self._latency)
        item = self._script[self.calls] if self.calls < len(self._script) else 1.0
        self.calls += 1
        if isinstance(item, Exception):
            self.last_attempts = 3
            raise item
        self.last_attempts = 1
        return item


def plan(count, hz):
    return SamplingConfig(measurements_count=count, sampling_frequency_hz=hz)


# ---------------------------------------------------------------- plan handling

def test_takes_the_planned_number_of_samples_in_order():
    result = Sampler(plan(5, 200)).run(FakeSource([1, 2, 3, 4, 5]))
    assert result.values == [1, 2, 3, 4, 5]
    assert [s.index for s in result.samples] == [0, 1, 2, 3, 4]
    assert result.planned_count == 5


def test_duration_and_frequency_define_the_count():
    result = Sampler(SamplingConfig(total_duration_seconds=0.1, sampling_frequency_hz=50)).run(FakeSource())
    assert len(result.samples) == 5
    assert result.interval_seconds == pytest.approx(0.02)


def test_scheduled_times_lie_on_the_exact_grid():
    result = Sampler(plan(4, 100)).run(FakeSource())
    assert [s.scheduled_s for s in result.samples] == pytest.approx([0.0, 0.01, 0.02, 0.03])


# ---------------------------------------------------------------- timing

def test_sampling_frequency_is_accurate():
    result = Sampler(plan(11, 100)).run(FakeSource())
    starts = [s.started_s for s in result.samples]
    mean_interval = (starts[-1] - starts[0]) / 10
    assert mean_interval == pytest.approx(0.01, rel=0.2)


def test_measurement_time_does_not_accumulate_as_drift():
    # Each measurement takes 20 ms of a 50 ms slot. A naive "sleep(interval) after each
    # sample" loop would run at 70 ms per sample and be ~0.4 s late by sample 20.
    result = Sampler(plan(20, 20)).run(FakeSource(latency=0.02))
    assert result.max_lateness_s < 0.025
    assert result.samples[-1].started_s == pytest.approx(19 * 0.05, abs=0.025)


def test_overrunning_measurements_catch_up_without_skipping_samples():
    result = Sampler(plan(5, 100)).run(FakeSource(latency=0.03))  # 30 ms work in a 10 ms slot
    assert len(result.samples) == 5
    starts = [s.started_s for s in result.samples]
    assert starts == sorted(starts)
    assert result.max_lateness_s > 0.05                     # lateness is reported, not hidden
    assert result.samples[0].lateness_s < 0.005             # the first sample is on time


def test_latency_is_measured_per_sample():
    result = Sampler(plan(3, 50)).run(FakeSource(latency=0.02))
    assert all(0.018 <= s.latency_s < 0.05 for s in result.samples)


# ---------------------------------------------------------------- failures

def test_failed_samples_are_recorded_and_sampling_continues():
    timeout = AmmeterTimeoutError("fake", "no reply")
    result = Sampler(plan(5, 200)).run(FakeSource([1.0, timeout, 3.0, timeout, 5.0]))
    assert result.values == [1.0, 3.0, 5.0]
    assert result.failure_count == 2
    assert result.error_summary == {"AmmeterTimeoutError": 2}
    failed = result.samples[1]
    assert (failed.ok, failed.value, failed.attempts) == (False, None, 3)
    assert "no reply" in failed.error


def test_non_ammeter_exceptions_are_not_swallowed():
    with pytest.raises(ValueError):
        Sampler(plan(3, 200)).run(FakeSource([1.0, ValueError("bug")]))


def test_all_samples_failing_still_returns_a_result():
    error = AmmeterTimeoutError("fake", "down")
    result = Sampler(plan(3, 200)).run(FakeSource([error] * 3))
    assert result.values == [] and result.failure_count == 3


# ---------------------------------------------------------------- reporting

def test_to_dict_is_json_serialisable_and_complete():
    result = Sampler(plan(3, 200)).run(FakeSource([1.0, AmmeterTimeoutError("fake", "x"), 3.0]))
    data = json.loads(json.dumps(result.to_dict()))
    assert data["ammeter"] == "fake"
    assert data["failure_count"] == 1
    assert len(data["samples"]) == 3 and "lateness_s" in data["samples"][0]
    datetime.fromisoformat(data["started_at"])  # valid ISO timestamp


# ---------------------------------------------------------------- with the real client + emulator

def test_samples_a_real_emulator_through_the_client():
    with GreenleeAmmeter(0) as emulator:
        config = AmmeterConfig("greenlee", "localhost", emulator.port, emulator.get_current_command.decode())
        client = AmmeterClient(config, ClientConfig(timeout_seconds=1.0))
        result = Sampler(plan(10, 100)).run(client)
    assert result.ammeter == "greenlee"
    assert len(result.values) == 10 and result.failure_count == 0
    assert all(s.attempts == 1 for s in result.samples)
