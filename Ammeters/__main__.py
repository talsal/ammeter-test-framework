"""Run the ammeter emulators on their own, until Ctrl-C:  python -m Ammeters [--config PATH] [NAME ...]

Use this when you want the emulators in a separate process, then run the tests with
`python main.py run --external`.
"""
import argparse
import logging
import sys
import threading

from src.runner import RunnerError, running_emulators, select_ammeters
from src.utils.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from src.utils.logger import setup_logging

logger = logging.getLogger(__name__)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m Ammeters", description="Run the ammeter emulators.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to the YAML config")
    parser.add_argument("names", nargs="*", metavar="NAME", help="ammeters to emulate (default: all in the config)")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        names = select_ammeters(config, args.names)
        setup_logging(config.logging.level)
        with running_emulators(config, names):
            logger.info("emulators running: %s. Press Ctrl-C to stop.", ", ".join(names))
            threading.Event().wait()
    except (ConfigError, RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
