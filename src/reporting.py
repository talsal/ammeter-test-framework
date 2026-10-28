"""Plain-text reports. They work from stored run records, so a fresh run and a
reloaded one are reported identically."""
from pathlib import Path
from typing import List, Optional, Sequence

from src.results_store import Comparison, RunRecord, RunSummary


def _fmt(value: Optional[float], spec: str = ".4g") -> str:
    return "-" if value is None else format(value, spec)


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _ms(seconds: Optional[float]) -> str:
    return "-" if seconds is None else f"{seconds * 1000:.2f}"


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Left-aligned first column, right-aligned others; no dependencies."""
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]

    def line(cells: Sequence[str]) -> str:
        first = str(cells[0]).ljust(widths[0])
        rest = [str(c).rjust(w) for c, w in zip(cells[1:], widths[1:])]
        return "  ".join([first] + rest)

    return "\n".join([line(headers), "  ".join("-" * w for w in widths)] + [line(r) for r in rows])


def format_run(record: RunRecord, path: Optional[Path] = None) -> str:
    """Full report of one run: header, one statistics row per ammeter, then problems found."""
    plan = record.config["sampling"]
    lines = [f"Run {record.run_id}" + (f"  [{record.label}]" if record.label else ""),
             f"Started: {record.created_at}",
             f"Plan:    {plan['count']} samples per ammeter, one every {plan['interval_seconds'] * 1000:.4g} ms "
             f"({1 / plan['interval_seconds']:.4g} Hz), {plan['duration_seconds']:.4g} s"]
    if path is not None:
        lines.append(f"Saved:   {path}")

    headers = ["Ammeter", "OK", "Mean (A)", "Median (A)", "Stdev (A)", "Min (A)", "Max (A)", "CV",
               "Outliers", "Consistency", "Latency (ms)", "Max late (ms)"]
    rows, problems = [], []
    for name in sorted(record.ammeters):
        entry = record.ammeters[name]
        a, s = entry["analysis"], entry["sampling"]
        c = a["current"] or {}
        rows.append([name, f"{a['successful']}/{a['requested']}", _fmt(c.get("mean")), _fmt(c.get("median")),
                     _fmt(c.get("stdev")), _fmt(c.get("minimum")), _fmt(c.get("maximum")), _pct(c.get("cv")),
                     str(c.get("outlier_count", "-")), a["consistency"], _ms(a["latency_mean_s"]),
                     _ms(a["max_lateness_s"])])
        if s["failure_count"]:
            kinds = ", ".join(f"{kind} x{n}" for kind, n in sorted(s["error_summary"].items()))
            problems.append(f"  {name}: {s['failure_count']} failed sample(s): {kinds}")
        if a["retried_count"]:
            problems.append(f"  {name}: {a['retried_count']} sample(s) needed retries")

    lines += ["", render_table(headers, rows), ""]
    lines += ["Problems:"] + problems if problems else ["No failed samples."]
    return "\n".join(lines)


def format_run_list(summaries: Sequence[RunSummary]) -> str:
    if not summaries:
        return "No runs stored."
    rows = [[s.run_id, s.created_at, s.label or "-", ", ".join(s.ammeters)] for s in summaries]
    return render_table(["Run ID", "Created (UTC)", "Label", "Ammeters"], rows)


def format_comparison(comparison: Comparison) -> str:
    """Side-by-side metrics; 'change' is the last run relative to the first."""
    count = len(comparison.run_ids)
    lines = ["Comparing runs (change = run %d relative to run 1):" % count]
    lines += [f"  run {i}: {run_id}" for i, run_id in enumerate(comparison.run_ids, 1)]
    headers = ["Ammeter / metric"] + [f"run {i}" for i in range(1, count + 1)] + ["change"]
    rows: List[List[str]] = []
    for m in comparison.metrics:
        change = "-" if m.change_pct is None else f"{m.change_pct:+.1f}%"
        rows.append([f"{m.ammeter} {m.metric}"] + [_fmt(v) for v in m.values] + [change])
    return "\n".join(lines + ["", render_table(headers, rows)])
