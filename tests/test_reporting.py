"""Tests for the text reports."""
from factories import make_record
from src.reporting import format_comparison, format_run, format_run_list, render_table
from src.results_store import ResultsStore


def test_render_table_aligns_columns():
    table = render_table(["Name", "Value"], [["a", "1"], ["long name", "22"]])
    lines = table.splitlines()
    assert len({len(line) for line in lines}) == 1             # every line is the same width
    assert lines[0].startswith("Name") and lines[2].endswith(" 1") and lines[3].endswith("22")


def test_format_run_shows_statistics_for_every_ammeter():
    record = make_record(means=(("entes", [10.0, 12.0, 14.0]), ("greenlee", [1.0, 2.0, 3.0])), label="demo")
    text = format_run(record)
    assert record.run_id in text and "[demo]" in text
    assert "20 samples per ammeter" in text and "10 Hz" in text      # from the shipped config
    lines = {line.split()[0]: line for line in text.splitlines() if line.startswith(("entes", "greenlee"))}
    assert "3/3" in lines["entes"] and " 12 " in lines["entes"]      # mean and median
    assert "No failed samples." in text


def test_format_run_reports_failures_and_retries():
    record = make_record(means=(("greenlee", [1.0, None, 3.0, None]),))
    text = format_run(record)
    assert "2/4" in text
    assert "greenlee: 2 failed sample(s): AmmeterTimeoutError x2" in text
    assert "No failed samples" not in text


def test_format_run_survives_an_ammeter_with_no_data():
    text = format_run(make_record(means=(("greenlee", [None, None]),)))
    assert "0/2" in text and "insufficient data" in text


def test_format_run_includes_the_path_when_given(tmp_path):
    assert f"Saved:   {tmp_path}" in format_run(make_record(), tmp_path)


def test_format_run_list():
    assert format_run_list([]) == "No runs stored."
    store = ResultsStore.__new__(ResultsStore)
    record = make_record(label="x")
    from src.results_store import RunSummary
    text = format_run_list([RunSummary(record.run_id, record.created_at, record.label, ("entes", "greenlee"))])
    assert record.run_id in text and "entes, greenlee" in text and "Label" in text


def test_format_comparison_shows_values_and_change(tmp_path):
    store = ResultsStore(tmp_path)
    a = make_record(means=(("greenlee", [10.0, 10.0]),), minutes=0)
    b = make_record(means=(("greenlee", [12.0, 12.0]),), minutes=1)
    store.save(a)
    store.save(b)
    text = format_comparison(store.compare([a.run_id, b.run_id]))
    assert a.run_id in text and b.run_id in text
    row = next(line for line in text.splitlines() if line.startswith("greenlee mean"))
    assert row.split()[-3:] == ["10", "12", "+20.0%"]
