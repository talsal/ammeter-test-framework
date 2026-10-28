"""Optional visualisation (needs matplotlib, which is not a hard dependency).

matplotlib is imported lazily and with the non-interactive "Agg" backend, so importing
this module never fails and plots can be written on machines without a display.
Plots are drawn from stored run records, so any archived run can be plotted later.
"""
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from src.comparison import compare_ammeters
from src.results_store import RunRecord

DPI = 110
COLOR = "#3b6ea5"
MEAN_COLOR = "#c0392b"
FAIL_COLOR = "#e67e22"


class PlottingUnavailableError(Exception):
    """matplotlib is not installed."""


def _load_pyplot() -> Any:
    try:
        import matplotlib
        matplotlib.use("Agg")  # file output only; no GUI toolkit needed
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise PlottingUnavailableError(
            "matplotlib is not installed (it is an optional dependency): pip install matplotlib") from exc
    return plt


def _columns(plt: Any, record: RunRecord, title: str):
    """One axes per ammeter side by side (currents differ by orders of magnitude, so no shared axis)."""
    names = sorted(record.ammeters)
    fig, axes = plt.subplots(1, len(names), figsize=(4.6 * len(names), 3.8), squeeze=False)
    fig.suptitle(f"{title} - run {record.run_id}", fontsize=10)
    return fig, names, axes[0]


def _values(record: RunRecord, name: str) -> List[float]:
    return [s["value"] for s in record.ammeters[name]["sampling"]["samples"] if s["value"] is not None]


def _no_data(ax: Any, name: str) -> None:
    ax.set_title(name)
    ax.text(0.5, 0.5, "no successful samples", ha="center", va="center", transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])


def _histogram(plt: Any, record: RunRecord):
    fig, names, axes = _columns(plt, record, "Distribution of readings")
    for ax, name in zip(axes, names):
        values = _values(record, name)
        if not values:
            _no_data(ax, name)
            continue
        stats = record.ammeters[name]["analysis"]["current"]
        ax.hist(values, bins=max(5, min(20, round(math.sqrt(len(values))))), color=COLOR, edgecolor="white")
        ax.axvline(stats["mean"], color=MEAN_COLOR, label=f"mean {stats['mean']:.3g}")
        ax.axvline(stats["median"], color=MEAN_COLOR, linestyle="--", label=f"median {stats['median']:.3g}")
        ax.set_title(f"{name} (n={len(values)})")
        ax.set_xlabel("Current (A)")
        ax.set_ylabel("Samples")
        ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def _time_series(plt: Any, record: RunRecord):
    fig, names, axes = _columns(plt, record, "Readings over time")
    for ax, name in zip(axes, names):
        samples = record.ammeters[name]["sampling"]["samples"]
        good = [s for s in samples if s["value"] is not None]
        failed = [s for s in samples if s["value"] is None]
        if not good:
            _no_data(ax, name)
            continue
        stats = record.ammeters[name]["analysis"]["current"]
        ax.plot([s["started_s"] for s in good], [s["value"] for s in good], marker="o", markersize=3,
                linewidth=1, color=COLOR)
        ax.axhline(stats["mean"], color=MEAN_COLOR, label="mean")
        if stats["stdev"] is not None:
            ax.axhspan(stats["mean"] - stats["stdev"], stats["mean"] + stats["stdev"], color=MEAN_COLOR,
                       alpha=0.12, label="mean ± 1 stdev")
        if failed:  # drawn on the bottom edge of the axes so they never distort the value scale
            ax.plot([s["started_s"] for s in failed], [0] * len(failed), "x", color=FAIL_COLOR,
                    transform=ax.get_xaxis_transform(), clip_on=False, label=f"failed ({len(failed)})")
        ax.set_title(name)
        ax.set_xlabel("Time since start (s)")
        ax.set_ylabel("Current (A)")
        ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def _precision(plt: Any, record: RunRecord):
    ranking = compare_ammeters(record)
    scored = [s for s in ranking.scores if s.cv is not None]
    fig, ax = plt.subplots(figsize=(6.5, 1.2 + 0.7 * max(1, len(scored))))
    fig.suptitle(f"Measurement precision - run {record.run_id}", fontsize=10)
    if not scored:
        _no_data(ax, "precision")
        return fig
    y = list(range(len(scored)))
    cvs = [s.cv * 100 for s in scored]
    errors = [[(s.cv - s.cv_low) * 100 if s.cv_low is not None else 0 for s in scored],
              [(s.cv_high - s.cv) * 100 if s.cv_high is not None else 0 for s in scored]]
    ax.barh(y, cvs, xerr=[[max(0, e) for e in errors[0]], [max(0, e) for e in errors[1]]], color=COLOR,
            capsize=3, error_kw={"ecolor": "#333333", "linewidth": 1})
    ax.set_yticks(y)
    ax.set_yticklabels([f"{s.rank}. {s.ammeter}" for s in scored])
    ax.invert_yaxis()  # rank 1 on top
    ax.set_xlabel("Coefficient of variation, % (lower = more repeatable; bars show the 95% interval)")
    fig.tight_layout()
    return fig


PLOTS: Dict[str, Callable[[Any, RunRecord], Any]] = {
    "histogram": _histogram,
    "time_series": _time_series,
    "precision": _precision,
}


def plot_run(record: RunRecord, directory: Path, plot_types: Sequence[str]) -> List[Path]:
    """Writes one PNG per requested plot type into ``directory``; returns the files written."""
    unknown = [t for t in plot_types if t not in PLOTS]
    if unknown:
        raise ValueError(f"unknown plot type(s) {unknown}; available: {list(PLOTS)}")
    plt = _load_pyplot()
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for plot_type in plot_types:
        fig = PLOTS[plot_type](plt, record)
        path = directory / f"{plot_type}.png"
        fig.savefig(path, dpi=DPI)
        plt.close(fig)  # figures are not garbage-collected while pyplot tracks them
        written.append(path)
    return written
