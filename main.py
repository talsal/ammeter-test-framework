"""Entry point: starts the ammeter emulators, samples them, analyses and archives the run.

    python main.py                 # run every ammeter in config/config.yaml
    python main.py --help          # all commands and options
"""
import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
