import logging

import pytest

from src.utils.logger import PROJECT_LOGGERS


@pytest.fixture(autouse=True)
def reset_project_logging():
    """setup_logging() installs handlers on the project loggers (as the CLI does); remove them after each test."""
    yield
    for name in PROJECT_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)
