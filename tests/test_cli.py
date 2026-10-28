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


# ---------------------------------------------------------------- ranking, plots, simulated faults

def with_visualization(cfg_path, enabled=True):
    raw = yaml.safe_load(open(cfg_path))
    raw["analysis"] = {"visualization": {"enabled": enabled, "plot_types": ["histogram", "precision"]}}
    open(cfg_path, "w").write(yaml.safe_dump(raw))


def test_run_prints_the_ammeter_ranking(cfg, capsys):
    assert main(["run", "--config", cfg]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Ammeter ranking" in out and "Most reliable:" in out and "not accuracy" in out


def test_ranking_is_skipped_for_a_single_ammeter(cfg, capsys):
    main(["run", "--config", cfg, "-a", "entes"])
    assert "Ammeter ranking" not in capsys.readouterr().out


def test_run_writes_plots_next_to_the_archive(cfg, tmp_path, capsys):
    pytest.importorskip("matplotlib")
    with_visualization(cfg)
    assert main(["run", "--config", cfg]) == EXIT_OK
    run_id = stored_runs(tmp_path)[0].stem
    plots = sorted(p.name for p in (tmp_path / "results" / "plots" / run_id).iterdir())
    assert plots == ["histogram.png", "precision.png"]          # the types listed in the config
    assert "Plots:" in capsys.readouterr().out


def test_no_plots_flag_and_no_save_skip_plotting(cfg, tmp_path):
    with_visualization(cfg)
    main(["run", "--config", cfg, "--no-plots", "-a", "entes"])
    main(["run", "--config", cfg, "--no-save", "-a", "entes"])
    assert not (tmp_path / "results" / "plots").exists()


def test_missing_matplotlib_only_warns_during_a_run(cfg, tmp_path, monkeypatch, capsys):
    with_visualization(cfg)
    monkeypatch.setitem(__import__("sys").modules, "matplotlib", None)
    assert main(["run", "--config", cfg, "-a", "entes"]) == EXIT_OK        # the run itself still succeeds
    assert len(stored_runs(tmp_path)) == 1
    assert "plots skipped" in capsys.readouterr().err


def test_plot_command_regenerates_plots_for_an_archived_run(cfg, tmp_path):
    pytest.importorskip("matplotlib")
    main(["run", "--config", cfg, "-a", "entes", "greenlee"])               # visualization disabled in this config
    assert main(["plot", "latest", "--config", cfg, "-t", "time_series"]) == EXIT_OK
    run_id = stored_runs(tmp_path)[0].stem
    assert [p.name for p in (tmp_path / "results" / "plots" / run_id).iterdir()] == ["time_series.png"]


def test_plot_command_without_matplotlib_is_an_error(cfg, monkeypatch, capsys):
    main(["run", "--config", cfg, "-a", "entes"])
    monkeypatch.setitem(__import__("sys").modules, "matplotlib", None)
    assert main(["plot", "latest", "--config", cfg]) == EXIT_ERROR
    assert "pip install matplotlib" in capsys.readouterr().err


def test_simulated_faults_are_survived_reported_and_recorded(cfg, tmp_path, capsys):
    raw = yaml.safe_load(open(cfg))
    raw["testing"]["sampling"] = {"measurements_count": 30, "sampling_frequency_hz": 200}
    raw["ammeters"]["entes"]["faults"] = {"drop_rate": 0.3, "garbage_rate": 0.1, "seed": 4}
    raw["ammeters"]["circutor"]["faults"] = {"error_rate": 1.0}             # permanently broken
    open(cfg, "w").write(yaml.safe_dump(raw))

    assert main(["run", "--config", cfg]) == EXIT_SAMPLE_FAILURES          # finished, but with failures
    out = capsys.readouterr().out
    assert "Faults:  SIMULATED for circutor (error 100%); entes (drop 30%, garbage 10%)" in out
    assert "circutor: 30 failed sample(s): AmmeterRejectedError x30" in out
    assert "AmmeterMalformedResponseError" in out                            # entes: drops and garbage
    run = json.loads(stored_runs(tmp_path)[0].read_text())
    assert run["ammeters"]["greenlee"]["analysis"]["successful"] == 30       # the healthy one is unaffected
    assert run["ammeters"]["circutor"]["analysis"]["current"] is None
    assert run["config"]["ammeters"]["entes"]["faults"]["drop_rate"] == 0.3  # recorded with the run
    assert "Most reliable: greenlee" in out                                  # broken ammeters rank below


def test_the_shipped_faulty_example_config_runs():
    from src.utils.config import load_config
    config = load_config("config/faulty_example.yaml")
    assert config.ammeters["entes"].faults.drop_rate == 0.20
    assert config.ammeters["greenlee"].faults is None
