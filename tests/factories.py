"""Builders for test data shared by several test modules."""
import socket
from datetime import datetime, timedelta, timezone

from src.analysis import analyze
from src.results_store import RunRecord
from src.sampler import Sample, SamplingResult
from src.utils.config import Config, load_config, parse_config

BASE_TIME = datetime(2026, 10, 28, 10, 15, 30, tzinfo=timezone.utc)


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


def make_record(means=(("greenlee", [1.0, 2.0, 3.0]),), minutes=0, label=None, **kwargs):
    """A RunRecord built through the normal path: results -> analysis -> record."""
    sampling = {name: make_result(values, name=name) for name, values in means}
    analysis = {name: analyze(result) for name, result in sampling.items()}
    return RunRecord.create(load_config(), sampling, analysis, label=label,
                            now=BASE_TIME + timedelta(minutes=minutes), **kwargs)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def raw_config(names=("greenlee", "entes", "circutor"), count=5, hz=100, retries=0, timeout=0.5) -> dict:
    """A raw (YAML-shaped) config whose ammeters sit on free ports, for end-to-end tests."""
    from Ammeters import EMULATORS
    ammeters = {}
    for name in names:
        command = EMULATORS[name](0).get_current_command.decode() if name in EMULATORS else "X"
        ammeters[name] = {"host": "127.0.0.1", "port": free_port(), "command": command}
    return {"testing": {"sampling": {"measurements_count": count, "sampling_frequency_hz": hz}},
            "ammeters": ammeters,
            "client": {"timeout_seconds": timeout, "retries": retries, "retry_delay_seconds": 0.0},
            "analysis": {"visualization": {"enabled": False}},   # plots are slow; tests that need them enable them
            "logging": {"level": "WARNING", "to_file": False}}


def make_config(**kwargs) -> Config:
    return parse_config(raw_config(**kwargs))
