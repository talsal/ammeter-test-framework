"""Tests for logging setup (the original logger.py never attached a handler, so nothing was logged)."""
import logging

from src.utils.logger import setup_logging


def test_records_reach_the_log_file(tmp_path):
    log_file = tmp_path / "logs" / "test.log"              # directory is created on demand
    setup_logging("INFO", log_file)
    logging.getLogger("src.example").info("hello from src")
    logging.getLogger("Ammeters.example").warning("hello from emulators")
    for handler in logging.getLogger("src").handlers:
        handler.flush()
    text = log_file.read_text()
    assert "hello from src" in text and "hello from emulators" in text and "INFO" in text


def test_level_filters_records(tmp_path):
    log_file = tmp_path / "test.log"
    setup_logging("WARNING", log_file)
    logging.getLogger("src.example").info("quiet")
    logging.getLogger("src.example").error("loud")
    for handler in logging.getLogger("src").handlers:
        handler.flush()
    text = log_file.read_text()
    assert "loud" in text and "quiet" not in text


def test_repeated_setup_does_not_duplicate_handlers(tmp_path):
    for _ in range(3):
        setup_logging("INFO", tmp_path / "test.log")
    assert len(logging.getLogger("src").handlers) == 2     # one console + one file


def test_console_only_without_a_file(capsys):
    setup_logging("INFO")
    logging.getLogger("src.example").info("to stderr")
    captured = capsys.readouterr()
    assert "to stderr" in captured.err and captured.out == ""
