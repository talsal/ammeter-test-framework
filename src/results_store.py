"""Archiving of test runs: one self-contained JSON file per run.

Layout: ``<directory>/<run_id>.json``. Run IDs look like ``20261028T101530Z-a1b2c3``
(UTC timestamp + short random suffix), so they are unique, readable and sort
chronologically by name; listing needs no separate index.
"""
import json
import logging
import os
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.analysis import AnalysisResult
from src.sampler import SamplingResult
from src.utils.config import Config

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
REF_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")  # what a user may type: full id, prefix or "latest"
LATEST = "latest"

# Metrics shown by compare(): taken from analysis["current"] ...
STAT_METRICS = ("mean", "median", "stdev", "cv", "minimum", "maximum")
# ... or from the top level of the analysis.
RELIABILITY_METRICS = ("success_rate", "latency_mean_s")


class ResultsError(Exception):
    """Base class for result-store errors."""


class RunNotFoundError(ResultsError):
    """No stored run matches the given reference."""


class CorruptRunError(ResultsError):
    """A run file exists but cannot be read as a run record."""


@dataclass(frozen=True)
class RunRecord:
    """Everything about one run: when, how it was configured, what was measured and concluded."""
    run_id: str
    created_at: str                          # ISO 8601, UTC
    label: Optional[str]
    metadata: Dict[str, Any]
    config: Dict[str, Any]                   # snapshot of the configuration used
    ammeters: Dict[str, Dict[str, Any]]      # name -> {"sampling": ..., "analysis": ...}
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def create(cls, config: Config, sampling: Mapping[str, SamplingResult],
               analysis: Mapping[str, AnalysisResult], label: Optional[str] = None,
               extra_metadata: Optional[Mapping[str, Any]] = None,
               now: Optional[datetime] = None) -> "RunRecord":
        """Builds a new record with a fresh run ID from a finished run."""
        now = now or datetime.now(timezone.utc)
        metadata = {"python_version": platform.python_version(), "platform": platform.platform(),
                    "ammeters": sorted(sampling)}
        metadata.update(extra_metadata or {})
        return cls(
            run_id=now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6],
            created_at=now.isoformat(timespec="seconds"),
            label=label,
            metadata=metadata,
            config=config.to_dict(),
            ammeters={name: {"sampling": sampling[name].to_dict(), "analysis": analysis[name].to_dict()}
                      for name in sampling},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "created_at": self.created_at,
                "label": self.label, "metadata": self.metadata, "config": self.config,
                "ammeters": self.ammeters}

    @classmethod
    def from_dict(cls, data: Any) -> "RunRecord":
        required = ("schema_version", "run_id", "created_at", "metadata", "config", "ammeters")
        if not isinstance(data, dict) or any(key not in data for key in required):
            raise CorruptRunError("not a run record (missing required fields)")
        if data["schema_version"] != SCHEMA_VERSION:
            raise CorruptRunError(f"unsupported schema_version {data['schema_version']!r} "
                                  f"(this version reads {SCHEMA_VERSION})")
        return cls(run_id=data["run_id"], created_at=data["created_at"], label=data.get("label"),
                   metadata=data["metadata"], config=data["config"], ammeters=data["ammeters"],
                   schema_version=data["schema_version"])


@dataclass(frozen=True)
class RunSummary:
    """One line of the run listing."""
    run_id: str
    created_at: str
    label: Optional[str]
    ammeters: Tuple[str, ...]


@dataclass(frozen=True)
class MetricComparison:
    """One metric of one ammeter across the compared runs (None where a run has no value)."""
    ammeter: str
    metric: str
    values: Tuple[Optional[float], ...]       # one per compared run, in the order requested
    change_pct: Optional[float]               # last run vs first run; None if not computable


@dataclass(frozen=True)
class Comparison:
    run_ids: Tuple[str, ...]
    metrics: Tuple[MetricComparison, ...]


class ResultsStore:
    """Saves, loads, lists and compares run records in a directory."""

    def __init__(self, directory: Any):
        self.directory = Path(directory)

    def path_for(self, run_id: str) -> Path:
        return self.directory / f"{run_id}.json"

    def save(self, record: RunRecord) -> Path:
        """Writes the record atomically (temp file + rename). Never overwrites an existing run."""
        path = self.path_for(record.run_id)
        if path.exists():
            raise ResultsError(f"run {record.run_id} already exists")
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, indent=2, allow_nan=False)
                f.write("\n")
            os.replace(tmp, path)  # atomic on POSIX and Windows: readers never see a half-written file
        finally:
            tmp.unlink(missing_ok=True)
        logger.info("saved run %s to %s", record.run_id, path)
        return path

    def resolve(self, ref: str) -> str:
        """Turns a reference (full run id, unique prefix, or 'latest') into a stored run id."""
        if not isinstance(ref, str) or not REF_PATTERN.match(ref):
            raise ResultsError(f"invalid run reference {ref!r}")
        ids = self._run_ids()
        if ref == LATEST:
            if not ids:
                raise RunNotFoundError(f"no runs stored in {self.directory}")
            return ids[-1]
        matches = [i for i in ids if i == ref] or [i for i in ids if i.startswith(ref)]
        if not matches:
            raise RunNotFoundError(f"no run matches {ref!r} in {self.directory}")
        if len(matches) > 1:
            raise ResultsError(f"{ref!r} is ambiguous: matches {len(matches)} runs, give more characters")
        return matches[0]

    def load(self, ref: str) -> RunRecord:
        """Loads a run by full id, unique prefix, or 'latest'."""
        run_id = self.resolve(ref)
        try:
            with open(self.path_for(run_id), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise CorruptRunError(f"cannot read run {run_id}: {exc}") from exc
        return RunRecord.from_dict(data)

    def list_runs(self) -> List[RunSummary]:
        """All stored runs, newest first. Unreadable files are skipped with a warning."""
        summaries = []
        for run_id in reversed(self._run_ids()):
            try:
                record = self.load(run_id)
            except CorruptRunError as exc:
                logger.warning("skipping %s: %s", run_id, exc)
                continue
            summaries.append(RunSummary(record.run_id, record.created_at, record.label,
                                        tuple(sorted(record.ammeters))))
        return summaries

    def compare(self, refs: Sequence[str]) -> Comparison:
        """Compares the same metrics of every ammeter across two or more runs."""
        if len(refs) < 2:
            raise ResultsError("compare needs at least two runs")
        records = [self.load(ref) for ref in refs]
        names = sorted({name for record in records for name in record.ammeters})
        rows = []
        for name in names:
            for metric in STAT_METRICS + RELIABILITY_METRICS:
                values = tuple(_metric(record, name, metric) for record in records)
                rows.append(MetricComparison(name, metric, values, _change_pct(values[0], values[-1])))
        return Comparison(run_ids=tuple(r.run_id for r in records), metrics=tuple(rows))

    def _run_ids(self) -> List[str]:
        """Stored run ids in chronological order (by file name)."""
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json") if RUN_ID_PATTERN.match(p.stem))


def _metric(record: RunRecord, ammeter: str, metric: str) -> Optional[float]:
    entry = record.ammeters.get(ammeter)
    if entry is None:
        return None
    analysis = entry.get("analysis", {})
    if metric in STAT_METRICS:
        return (analysis.get("current") or {}).get(metric)
    return analysis.get(metric)


def _change_pct(first: Optional[float], last: Optional[float]) -> Optional[float]:
    if first is None or last is None or first == 0:
        return None
    return (last - first) / abs(first) * 100
