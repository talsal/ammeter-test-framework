"""Tests for the command-line interface, driven through main() with a throw-away config."""
import json

import pytest
import yaml

from factories import raw_config
from src.cli import EXIT_ERROR, EXIT_OK, EXIT_SAMPLE_FAILURES, main


@pytest.fixture
def cfg(tmp_path):
    """Path of a config file whose ammeters use free ports; results go to tmp_path/results."""
    raw = raw_config(count=4)
    raw["result_management"] = {"directory": str(tmp_path / "results")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return str(path)


def stored_runs(tmp_path):
    return sorted((tmp_path / "results").glob("*.json"))


# ---------------------------------------------------------------- run

def test_run_reports_archives_and_logs(cfg, tmp_path, capsys):
    assert main(["run", "--config", cfg, "--label", "smoke"]) == EXIT_OK
    out = capsys.readouterr().out
    for expected in ("Run ", "[smoke]", "greenlee", "entes", "circutor", "4/4", "No failed samples", "Saved:"):
        assert expected in out
    assert len(stored_runs(tmp_path)) == 1


def test_no_arguments_other_than_options_defaults_to_run(cfg, tmp_path):
    assert main(["-n", "3", "--config", cfg]) == EXIT_OK
    assert len(stored_runs(tmp_path)) == 1


def test_run_subset_and_sampling_overrides(cfg, tmp_path, capsys):
    assert main(["run", "--config", cfg, "-a", "entes", "-d", "0.1", "-f", "50"]) == EXIT_OK
    run = json.loads(stored_runs(tmp_path)[0].read_text())
    assert list(run["ammeters"]) == ["entes"]
    assert run["config"]["sampling"]["count"] == 5            # 0.1 s * 50 Hz


def test_no_save_writes_nothing(cfg, tmp_path):
    assert main(["run", "--config", cfg, "--no-save", "-a", "entes"]) == EXIT_OK
    assert stored_runs(tmp_path) == []


def test_log_file_is_written_when_enabled(cfg, tmp_path):
    raw = yaml.safe_load(open(cfg))
    raw["logging"] = {"level": "INFO", "to_file": True}
    open(cfg, "w").write(yaml.safe_dump(raw))
    assert main(["run", "--config", cfg, "-a", "entes"]) == EXIT_OK
    logs = list((tmp_path / "results" / "logs").glob("run-*.log"))
    assert len(logs) == 1 and "sampling entes" in logs[0].read_text()
    run_id = stored_runs(tmp_path)[0].stem
    assert logs[0].stem == "run-" + run_id.split("-")[0]       # same UTC start stamp as the run id


def test_external_mode_without_ammeters_exits_1_and_still_reports(cfg, tmp_path, capsys):
    assert main(["run", "--config", cfg, "--external", "-a", "greenlee"]) == EXIT_SAMPLE_FAILURES
    out = capsys.readouterr().out
    assert "0/4" in out and "AmmeterConnectionError x4" in out
    assert len(stored_runs(tmp_path)) == 1                    # failed runs are archived too


# ---------------------------------------------------------------- list / show / compare

def test_list_show_compare_round_trip(cfg, tmp_path, capsys):
    main(["run", "--config", cfg, "-l", "first"])
    main(["run", "--config", cfg, "-l", "second"])
    capsys.readouterr()

    assert main(["list", "--config", cfg]) == EXIT_OK
    listing = capsys.readouterr().out
    assert "first" in listing and "second" in listing

    run_ids = [p.stem for p in stored_runs(tmp_path)]
    assert main(["show", run_ids[0], "--config", cfg]) == EXIT_OK
    assert run_ids[0] in capsys.readouterr().out

    assert main(["compare", run_ids[0], "latest", "--config", cfg]) == EXIT_OK
    table = capsys.readouterr().out
    assert "greenlee mean" in table and "success_rate" in table and run_ids[0] in table


def test_list_on_empty_store(cfg, capsys):
    assert main(["list", "--config", cfg]) == EXIT_OK
    assert "No runs stored" in capsys.readouterr().out


# ---------------------------------------------------------------- errors

@pytest.mark.parametrize("argv, fragment", [
    (["run", "--config", "/nonexistent.yaml"], "cannot read config"),
    (["run", "-a", "nope"], "unknown ammeter"),
    (["run", "-n", "5", "-d", "10", "-f", "2"], "inconsistent"),
    (["show", "latest"], "no runs stored"),
    (["show", "19990101T000000Z-aaaaaa"], "no run matches"),
    (["compare", "latest"], "at least two"),
])
def test_errors_exit_2_with_a_readable_message(cfg, argv, fragment, capsys):
    if "--config" not in argv:
        argv = argv + ["--config", cfg]
    assert main(argv) == EXIT_ERROR
    err = capsys.readouterr().err
    assert err.startswith("error:") and fragment in err
    assert "Traceback" not in err


@pytest.mark.parametrize("argv", [["run", "-n", "0"], ["run", "-f", "-1"], ["run", "-d", "abc"], ["bogus"]])
def test_invalid_arguments_are_rejected_by_argparse(argv):
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0 and "compare" in capsys.readouterr().out


def test_port_conflict_gives_an_actionable_error(cfg, capsys):
    import socket
    port = yaml.safe_load(open(cfg))["ammeters"]["entes"]["port"]
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", port))
        blocker.listen()
        assert main(["run", "--config", cfg, "-a", "entes"]) == EXIT_ERROR
    assert "--external" in capsys.readouterr().err


# ---------------------------------------------------------------- separate-process emulators

def test_external_mode_against_emulators_running_in_another_process(cfg, tmp_path, capsys):
    """The two-terminal workflow: `python -m Ammeters` in one, `main.py run --external` in the other."""
    import socket
    import subprocess
    import sys
    import time

    ports = [a["port"] for a in yaml.safe_load(open(cfg))["ammeters"].values()]
    proc = subprocess.Popen([sys.executable, "-m", "Ammeters", "--config", cfg],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        for port in ports:                                       # wait until every emulator accepts connections
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    assert time.monotonic() < deadline and proc.poll() is None, "emulators did not start"
                    time.sleep(0.05)
        assert main(["run", "--config", cfg, "--external"]) == EXIT_OK
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    out = capsys.readouterr().out
    assert out.count("4/4") == 3 and "No failed samples" in out
