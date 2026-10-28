"""Tests for the optional plotting module."""
import pytest

from factories import make_record
from src.plotting import PLOTS, PlottingUnavailableError, plot_run

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RECORD_KWARGS = dict(means=(("greenlee", [1.0, 2.0, None, 4.0, 3.0] * 4), ("entes", [50.0, 60.0, 55.0] * 6)))


def test_every_plot_type_writes_a_valid_png(tmp_path):
    pytest.importorskip("matplotlib")
    files = plot_run(make_record(**RECORD_KWARGS), tmp_path / "plots", list(PLOTS))
    assert sorted(f.name for f in files) == ["histogram.png", "precision.png", "time_series.png"]
    for f in files:
        assert f.read_bytes()[:8] == PNG_SIGNATURE and f.stat().st_size > 5_000


@pytest.mark.parametrize("means", [
    (("a", [None, None]),),                     # nothing measured
    (("a", [2.5]),),                            # a single value: no stdev, no interval
    (("a", [1.0] * 6), ("b", [None] * 6)),      # constant data next to a dead ammeter
])
def test_degenerate_data_does_not_crash_the_plots(tmp_path, means):
    pytest.importorskip("matplotlib")
    files = plot_run(make_record(means=means), tmp_path, list(PLOTS))
    assert len(files) == 3


def test_unknown_plot_type_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="pie"):
        plot_run(make_record(), tmp_path, ["pie"])


def test_missing_matplotlib_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "matplotlib", None)      # makes `import matplotlib` fail
    with pytest.raises(PlottingUnavailableError, match="pip install matplotlib"):
        plot_run(make_record(), tmp_path, ["histogram"])
