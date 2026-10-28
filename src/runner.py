"""Orchestrates a test run: start emulators, sample each ammeter, analyse, archive."""
import dataclasses
import io
import logging
import threading
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from Ammeters import EMULATORS
from Ammeters.faulty import FaultyEmulator
from src.ammeter_client import AmmeterClient
from src.analysis import AnalysisResult, analyze
from src.results_store import ResultsStore, RunRecord
from src.sampler import Sampler, SamplingResult
from src.utils.config import Config, SamplingConfig

logger = logging.getLogger(__name__)
emulator_output = logging.getLogger("Ammeters.output")  # where the emulators' print() output ends up


class RunnerError(Exception):
    """The run cannot be set up as requested (unknown ammeter, emulator cannot start, ...)."""


class _LogWriter(io.TextIOBase):
    """File-like object that turns each printed line into a DEBUG log record."""

    def __init__(self, target: logging.Logger):
        self._target = target
        self._buffer = ""
        self._lock = threading.Lock()  # emulator threads print concurrently

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            if line.strip():
                self._target.debug(line)
        return len(text)


def select_ammeters(config: Config, requested: Optional[Sequence[str]] = None) -> List[str]:
    """Names to run: the requested ones (validated against the config), or all configured."""
    if not requested:
        return list(config.ammeters)
    unknown = [name for name in requested if name not in config.ammeters]
    if unknown:
        raise RunnerError(f"unknown ammeter(s) {unknown}; configured: {list(config.ammeters)}")
    return list(dict.fromkeys(requested))  # de-duplicate, keep order


def apply_sampling_overrides(config: Config, count: Optional[int] = None, duration: Optional[float] = None,
                             frequency: Optional[float] = None) -> Config:
    """
    Overrides the configured sampling plan. Two or more values replace the plan outright.
    A single value keeps the configured frequency (for count / duration) or the configured
    count (for frequency), so `-n 50` means "50 samples at the usual rate".
    """
    given = [v for v in (count, duration, frequency) if v is not None]
    if not given:
        return config
    if len(given) == 1:
        if frequency is not None:
            count = config.sampling.count
        else:
            frequency = 1.0 / config.sampling.interval_seconds
    plan = SamplingConfig(measurements_count=count, total_duration_seconds=duration,
                          sampling_frequency_hz=frequency)
    return dataclasses.replace(config, sampling=plan)


@contextmanager
def running_emulators(config: Config, names: Sequence[str]) -> Iterator[None]:
    """
    Starts an in-process emulator per ammeter and stops them on exit, even if one
    fails to start. start() returns only when the socket is listening, so there is no
    sleep-and-hope. The emulators' print() output is redirected to DEBUG logs.
    """
    with ExitStack() as stack:
        # Entered first, so it is restored last: after every emulator has stopped printing.
        stack.enter_context(redirect_stdout(_LogWriter(emulator_output)))
        for name in names:
            emulator_class = EMULATORS.get(name)
            if emulator_class is None:
                raise RunnerError(f"no emulator available for {name!r}; run with --external if it "
                                  f"is already running")
            ammeter = config.ammeters[name]
            emulator = emulator_class(ammeter.port, host=ammeter.host)
            if ammeter.faults and ammeter.faults.total_rate > 0:
                logger.warning("%s: simulating faults %s", name, ammeter.faults)
                emulator = FaultyEmulator(emulator, ammeter.faults, ammeter.port, host=ammeter.host)
            try:
                stack.enter_context(emulator)
            except OSError as exc:
                raise RunnerError(f"cannot start the {name} emulator on {ammeter.host}:{ammeter.port} "
                                  f"({exc}). If it is already running, use --external") from exc
            logger.info("started %s emulator on port %d", name, ammeter.port)
        yield


def run_ammeters(config: Config, names: Sequence[str]) -> Tuple[Dict[str, SamplingResult],
                                                                 Dict[str, AnalysisResult]]:
    """Samples and analyses each ammeter in turn (one after another, so they do not disturb each other)."""
    sampler = Sampler(config.sampling)
    sampling: Dict[str, SamplingResult] = {}
    analysis: Dict[str, AnalysisResult] = {}
    for name in names:
        logger.info("sampling %s: %d samples every %.0f ms", name, config.sampling.count,
                    config.sampling.interval_seconds * 1000)
        sampling[name] = sampler.run(AmmeterClient(config.ammeters[name], config.client))
        analysis[name] = analyze(sampling[name], config.analysis)
    return sampling, analysis


def execute_run(config: Config, names: Sequence[str], label: Optional[str] = None,
                start_emulators: bool = True, store: Optional[ResultsStore] = None,
                started: Optional[datetime] = None) -> Tuple[RunRecord, Optional[Path]]:
    """
    Performs a complete run and returns its record (and where it was saved, if a store is given).
    With start_emulators=False the ammeters must already be running (e.g. real devices).
    ``started`` (UTC) stamps the run; by default it is taken now, before anything is sampled.
    """
    started = started or datetime.now(timezone.utc)
    with ExitStack() as stack:
        if start_emulators:
            stack.enter_context(running_emulators(config, names))
        sampling, analysis = run_ammeters(config, names)

    record = RunRecord.create(config, sampling, analysis, label=label, now=started,
                              extra_metadata={"emulators": "in-process" if start_emulators else "external"})
    path = store.save(record) if store is not None else None
    logger.info("run %s finished", record.run_id)
    return record, path


def has_failures(record: RunRecord) -> bool:
    """True if any ammeter in the run had a failed sample."""
    return any(entry["analysis"]["successful"] < entry["analysis"]["requested"]
               for entry in record.ammeters.values())
