"""Logging setup: console (stderr) plus an optional log file."""
import logging
import sys
from pathlib import Path
from typing import Optional, Union

# Only the project's own loggers are configured. Configuring the root logger would also
# switch on the (very chatty) debug output of third-party libraries such as matplotlib.
PROJECT_LOGGERS = ("src", "Ammeters")
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_OWN_HANDLER = "_ammeter_framework_handler"  # marks handlers added here, so repeat calls replace them


class _CurrentStderrHandler(logging.StreamHandler):
    """Writes to whatever sys.stderr is *now*, so redirecting or capturing stderr later is safe."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:  # StreamHandler.__init__ assigns it; the property ignores that
        pass


def setup_logging(level: str = "INFO", log_file: Optional[Union[str, Path]] = None) -> None:
    """
    Sends project log records to stderr (stdout is reserved for reports) and, if
    ``log_file`` is given, to that file too. Safe to call repeatedly: earlier handlers
    installed by this function are replaced, never duplicated.
    """
    formatter = logging.Formatter(LOG_FORMAT)
    handlers = [_CurrentStderrHandler()]
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, mode="w", encoding="utf-8"))

    for name in PROJECT_LOGGERS:
        logger = logging.getLogger(name)
        for old in [h for h in logger.handlers if getattr(h, _OWN_HANDLER, False)]:
            logger.removeHandler(old)
            old.close()
        logger.setLevel(level)
        for handler in handlers:
            handler.setFormatter(formatter)
            setattr(handler, _OWN_HANDLER, True)
            logger.addHandler(handler)
