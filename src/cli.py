"""Command-line interface: run | list | show | compare.

Exit codes: 0 success, 1 the run completed but some samples failed, 2 usage/config/storage error.
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.comparison import compare_ammeters
from src.plotting import PLOTS, PlottingUnavailableError, plot_run
from src.reporting import format_comparison, format_ranking, format_run, format_run_list
from src.results_store import ResultsError, ResultsStore, RunRecord
from src.runner import RunnerError, apply_sampling_overrides, execute_run, has_failures, select_ammeters
from src.utils.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from src.utils.logger import setup_logging

EXIT_OK, EXIT_SAMPLE_FAILURES, EXIT_ERROR = 0, 1, 2

logger = logging.getLogger(__name__)


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {text}")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {text}")
    return value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to the YAML config (default: %(default)s)")
    common.add_argument("--results-dir", help="where runs are stored (default: result_management.directory from the config)")

    parser = argparse.ArgumentParser(prog="main.py", description="Ammeter test framework.")
    commands = parser.add_subparsers(dest="command", metavar="{run,list,show,plot,compare}")

    run = commands.add_parser("run", parents=[common], help="sample the ammeters, analyse and archive (default command)")
    run.add_argument("-a", "--ammeter", nargs="+", metavar="NAME", help="ammeters to test (default: all in the config)")
    run.add_argument("-n", "--count", type=_positive_int, help="number of measurements per ammeter")
    run.add_argument("-d", "--duration", type=_positive_float, help="total sampling duration in seconds (a single -n/-d/-f keeps the rest of the configured plan)")
    run.add_argument("-f", "--frequency", type=_positive_float, help="sampling frequency in Hz")
    run.add_argument("-l", "--label", help="free-text label stored with the run")
    run.add_argument("--external", action="store_true",
                     help="do not start the emulators; use ammeters that are already running")
    run.add_argument("--no-save", action="store_true", help="do not archive the run (also skips plots)")
    run.add_argument("--no-plots", action="store_true", help="do not write plots even if the config enables them")
    run.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="override logging.level")

    commands.add_parser("list", parents=[common], help="list archived runs, newest first")

    show = commands.add_parser("show", parents=[common], help="print the report of an archived run")
    show.add_argument("run", help="run id, unique prefix, or 'latest'")

    plot = commands.add_parser("plot", parents=[common], help="write plots (PNG) for an archived run; needs matplotlib")
    plot.add_argument("run", help="run id, unique prefix, or 'latest'")
    plot.add_argument("-t", "--types", nargs="+", choices=list(PLOTS), help="plot types (default: those in the config)")

    compare = commands.add_parser("compare", parents=[common], help="compare metrics across archived runs")
    compare.add_argument("runs", nargs="+", metavar="RUN", help="two or more run ids / prefixes / 'latest'")
    return parser


def _results_dir(args: argparse.Namespace, configured: str) -> Path:
    return Path(args.results_dir or configured)


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.count or args.duration or args.frequency:
        config = apply_sampling_overrides(config, args.count, args.duration, args.frequency)
    names = select_ammeters(config, args.ammeter)

    results_dir = _results_dir(args, config.results.directory)
    started = datetime.now(timezone.utc)  # stamps the run id and the log file name alike
    log_file = None
    if config.logging.to_file:
        log_file = results_dir / "logs" / f"run-{started.strftime('%Y%m%dT%H%M%SZ')}.log"
    setup_logging(args.log_level or config.logging.level, log_file)

    store = None if args.no_save else ResultsStore(results_dir)
    record, path = execute_run(config, names, label=args.label, start_emulators=not args.external, store=store,
                                 started=started)
    print(_full_report(record, path))
    if store is not None and config.visualization.enabled and not args.no_plots:
        _write_plots(record, results_dir, config.visualization.plot_types, required=False)
    return EXIT_SAMPLE_FAILURES if has_failures(record) else EXIT_OK


def _full_report(record: RunRecord, path: Optional[Path]) -> str:
    """Run report, plus the ammeter ranking when there is more than one ammeter to rank."""
    report = format_run(record, path)
    if len(record.ammeters) > 1:
        report += "\n\n" + format_ranking(compare_ammeters(record))
    return report


def _write_plots(record: RunRecord, results_dir: Path, plot_types, required: bool) -> None:
    """Plots go to <results dir>/plots/<run id>/. matplotlib is optional: a missing install only
    skips the plots when they were not explicitly requested."""
    try:
        files = plot_run(record, results_dir / "plots" / record.run_id, plot_types)
    except PlottingUnavailableError as exc:
        if required:
            raise
        logger.warning("plots skipped: %s", exc)
        return
    print(f"\nPlots: {files[0].parent} ({', '.join(f.name for f in files)})")


def _store_for(args: argparse.Namespace) -> ResultsStore:
    config = load_config(args.config)
    return ResultsStore(_results_dir(args, config.results.directory))


def _cmd_list(args: argparse.Namespace) -> int:
    print(format_run_list(_store_for(args).list_runs()))
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    store = _store_for(args)
    record = store.load(args.run)
    print(_full_report(record, store.path_for(record.run_id)))
    return EXIT_OK


def _cmd_plot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    results_dir = _results_dir(args, config.results.directory)
    record = ResultsStore(results_dir).load(args.run)
    _write_plots(record, results_dir, args.types or config.visualization.plot_types, required=True)
    return EXIT_OK


def _cmd_compare(args: argparse.Namespace) -> int:
    print(format_comparison(_store_for(args).compare(args.runs)))
    return EXIT_OK


COMMANDS = {"run": _cmd_run, "list": _cmd_list, "show": _cmd_show, "plot": _cmd_plot, "compare": _cmd_compare}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv.insert(0, "run")  # `python main.py` and `python main.py -n 50` mean `run`
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, RunnerError, ResultsError, PlottingUnavailableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
