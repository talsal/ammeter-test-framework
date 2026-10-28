"""Tests for the runner: ammeter selection, sampling overrides, emulator lifecycle, end-to-end runs."""
import socket
import sys

import pytest

from factories import free_port, make_config, raw_config
from src.results_store import ResultsStore
from src.runner import (
    RunnerError,
    apply_sampling_overrides,
    execute_run,
    has_failures,
    running_emulators,
    select_ammeters,
)
from src.utils.config import ConfigError, parse_config


def listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- selection and overrides

def test_select_all_subset_and_duplicates():
    config = make_config()
    assert select_ammeters(config) == ["greenlee", "entes", "circutor"]
    assert select_ammeters(config, ["entes", "greenlee", "entes"]) == ["entes", "greenlee"]


def test_select_unknown_ammeter_lists_the_valid_ones():
    with pytest.raises(RunnerError, match="unknown.*nope.*greenlee"):
        select_ammeters(make_config(), ["nope"])


def test_no_overrides_keeps_the_config_object():
    config = make_config()
    assert apply_sampling_overrides(config) is config


def test_two_overrides_replace_the_whole_sampling_plan():
    config = make_config(count=5, hz=100)                       # config has count + frequency
    changed = apply_sampling_overrides(config, duration=2.0, frequency=10)
    assert (changed.sampling.count, changed.sampling.interval_seconds) == (20, pytest.approx(0.1))
    assert config.sampling.count == 5                           # original untouched (frozen)


@pytest.mark.parametrize("override, count, interval", [
    (dict(count=50), 50, 0.01),            # keeps the configured 100 Hz
    (dict(duration=1.0), 100, 0.01),       # 1 s at 100 Hz
    (dict(frequency=50), 5, 0.02),         # keeps the configured 5 samples
])
def test_a_single_override_keeps_the_other_configured_parameter(override, count, interval):
    changed = apply_sampling_overrides(make_config(count=5, hz=100), **override)
    assert (changed.sampling.count, changed.sampling.interval_seconds) == (count, pytest.approx(interval))


def test_contradictory_overrides_are_rejected():
    with pytest.raises(ConfigError, match="inconsistent"):
        apply_sampling_overrides(make_config(), count=5, duration=10, frequency=2)


# ---------------------------------------------------------------- emulator lifecycle

def test_emulators_run_inside_the_context_and_stop_after():
    config = make_config(names=("greenlee", "entes"))
    ports = [a.port for a in config.ammeters.values()]
    with running_emulators(config, ["greenlee", "entes"]):
        assert all(listening(p) for p in ports)
    assert not any(listening(p) for p in ports)


def test_unknown_emulator_is_reported_with_a_hint():
    config = parse_config(raw_config(names=("greenlee",)) | {"ammeters": {"newdevice": {"port": free_port(), "command": "X"}}})
    with pytest.raises(RunnerError, match="--external"):
        with running_emulators(config, ["newdevice"]):
            pass


def test_port_in_use_is_reported_and_already_started_emulators_are_stopped():
    config = make_config(names=("greenlee", "entes"))
    first, second = config.ammeters["greenlee"].port, config.ammeters["entes"].port
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", second))
        blocker.listen()
        with pytest.raises(RunnerError, match=f"{second}.*--external"):
            with running_emulators(config, ["greenlee", "entes"]):
                pass
    assert not listening(first)                                  # greenlee was cleaned up


def test_emulator_prints_go_to_the_log_not_to_stdout(capsys, caplog):
    caplog.set_level("DEBUG", logger="Ammeters.output")
    execute_run(make_config(names=("greenlee",), count=3), ["greenlee"])
    assert capsys.readouterr().out == ""
    assert any("Greenlee Ammeter - Voltage" in r.message for r in caplog.records)


def test_stdout_is_restored_after_a_failure():
    original = sys.stdout
    with pytest.raises(ZeroDivisionError):
        with running_emulators(make_config(names=("greenlee",)), ["greenlee"]):
            raise ZeroDivisionError
    assert sys.stdout is original


# ---------------------------------------------------------------- end-to-end

def test_full_run_with_all_three_emulators_is_archived(tmp_path):
    config = make_config(count=5)
    store = ResultsStore(tmp_path)
    record, path = execute_run(config, list(config.ammeters), label="e2e", store=store)

    assert path == store.path_for(record.run_id) and path.exists()
    assert set(record.ammeters) == {"greenlee", "entes", "circutor"}
    for entry in record.ammeters.values():
        assert entry["analysis"]["successful"] == 5
        assert len(entry["sampling"]["samples"]) == 5
    assert record.metadata["emulators"] == "in-process" and record.label == "e2e"
    assert not has_failures(record)
    assert store.load(record.run_id) == record


def test_run_is_stamped_with_its_start_time_not_its_end_time():
    from datetime import datetime, timezone
    started = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    record, _ = execute_run(make_config(names=("entes",), count=3), ["entes"], started=started)
    assert record.run_id.startswith("20260102T030405Z-")
    assert record.created_at == "2026-01-02T03:04:05+00:00"


def test_run_without_a_store_is_not_saved():
    record, path = execute_run(make_config(names=("entes",), count=2), ["entes"])
    assert path is None and record.ammeters["entes"]["analysis"]["successful"] == 2


def test_external_mode_with_nothing_running_records_failures_instead_of_crashing():
    config = make_config(names=("greenlee",), count=3)
    record, _ = execute_run(config, ["greenlee"], start_emulators=False)
    analysis = record.ammeters["greenlee"]["analysis"]
    assert analysis["successful"] == 0 and analysis["current"] is None
    assert record.ammeters["greenlee"]["sampling"]["error_summary"] == {"AmmeterConnectionError": 3}
    assert has_failures(record) and record.metadata["emulators"] == "external"
