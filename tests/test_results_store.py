"""Tests for the results store: ids, persistence, lookup, listing and comparison."""
import json

import pytest

from factories import make_record
from src.results_store import (
    RUN_ID_PATTERN,
    CorruptRunError,
    ResultsError,
    ResultsStore,
    RunNotFoundError,
    RunRecord,
)


@pytest.fixture
def store(tmp_path):
    return ResultsStore(tmp_path / "runs")


# ---------------------------------------------------------------- run records

def test_run_id_format_and_uniqueness():
    ids = {make_record().run_id for _ in range(500)}
    assert len(ids) == 500                                    # same second, still unique
    assert all(RUN_ID_PATTERN.match(i) for i in ids)
    assert next(iter(ids)).startswith("20261028T101530Z-")


def test_record_has_metadata_and_config_snapshot():
    record = make_record(label="baseline", extra_metadata={"note": "x"})
    assert record.label == "baseline"
    assert record.metadata["note"] == "x" and record.metadata["ammeters"] == ["greenlee"]
    assert record.metadata["python_version"] and record.metadata["platform"]
    assert record.config["ammeters"]["circutor"]["port"] == 5002       # snapshot of config.yaml
    assert record.created_at == "2026-10-28T10:15:30+00:00"


# ---------------------------------------------------------------- save / load

def test_save_load_round_trip(store):
    record = make_record(label="roundtrip")
    path = store.save(record)
    assert path.name == f"{record.run_id}.json"
    assert store.load(record.run_id) == record
    json.loads(path.read_text())                              # valid JSON on disk


def test_save_creates_the_directory_and_leaves_no_temp_files(store):
    store.save(make_record())
    assert [p.suffix for p in store.directory.iterdir()] == [".json"]


def test_save_never_overwrites_an_existing_run(store):
    record = make_record()
    store.save(record)
    with pytest.raises(ResultsError, match="already exists"):
        store.save(record)


# ---------------------------------------------------------------- references

def test_load_by_latest_and_by_prefix(store):
    older, newer = make_record(minutes=0), make_record(minutes=5)
    store.save(older)
    store.save(newer)
    assert store.load("latest").run_id == newer.run_id
    assert store.load(older.run_id[:20]).run_id == older.run_id      # unique prefix


def test_ambiguous_prefix_is_an_error(store):
    store.save(make_record(minutes=0))
    store.save(make_record(minutes=1))
    with pytest.raises(ResultsError, match="ambiguous"):
        store.load("20261028")


def test_unknown_run_and_empty_store(store):
    with pytest.raises(RunNotFoundError):
        store.load("latest")                                  # directory does not even exist yet
    store.save(make_record())
    with pytest.raises(RunNotFoundError):
        store.load("19990101T000000Z-aaaaaa")


@pytest.mark.parametrize("ref", ["../secret", "a/b", "..", "x.json", "", "run id"])
def test_references_cannot_escape_the_directory(store, ref):
    store.save(make_record())
    with pytest.raises(ResultsError):
        store.load(ref)


# ---------------------------------------------------------------- listing

def test_list_runs_newest_first_with_summaries(store):
    a = make_record(minutes=0, label="first")
    b = make_record(means=(("entes", [5.0, 6.0]), ("greenlee", [1.0, 2.0])), minutes=10)
    store.save(a)
    store.save(b)
    runs = store.list_runs()
    assert [r.run_id for r in runs] == [b.run_id, a.run_id]
    assert runs[0].ammeters == ("entes", "greenlee") and runs[1].label == "first"


def test_list_runs_on_missing_directory_is_empty(tmp_path):
    assert ResultsStore(tmp_path / "nope").list_runs() == []


def test_list_skips_corrupt_files_and_ignores_foreign_ones(store):
    good = make_record(minutes=0)
    store.save(good)
    bad_id = "20261028T120000Z-bbbbbb"
    store.path_for(bad_id).write_text("{ not json")
    (store.directory / "notes.json").write_text("{}")         # not a run id: ignored
    assert [r.run_id for r in store.list_runs()] == [good.run_id]


# ---------------------------------------------------------------- corrupt files

@pytest.mark.parametrize("content, fragment", [
    ("{ not json", "cannot read"),
    ("[]", "not a run record"),
    (json.dumps({"run_id": "x"}), "not a run record"),
    (json.dumps({**make_record().to_dict(), "schema_version": 99}), "schema_version"),
])
def test_load_reports_corrupt_files(store, content, fragment):
    run_id = "20261028T120000Z-bbbbbb"
    store.directory.mkdir(parents=True)
    store.path_for(run_id).write_text(content)
    with pytest.raises(CorruptRunError, match=fragment):
        store.load(run_id)


# ---------------------------------------------------------------- compare

def find(comparison, ammeter, metric):
    return next(m for m in comparison.metrics if m.ammeter == ammeter and m.metric == metric)


def test_compare_two_runs(store):
    a = make_record(means=(("greenlee", [10.0, 10.0, 10.0]),), minutes=0)
    b = make_record(means=(("greenlee", [12.0, 12.0, 12.0]),), minutes=1)
    store.save(a)
    store.save(b)
    comparison = store.compare([a.run_id, b.run_id])
    assert comparison.run_ids == (a.run_id, b.run_id)
    mean = find(comparison, "greenlee", "mean")
    assert mean.values == (10.0, 12.0) and mean.change_pct == pytest.approx(20.0)
    assert find(comparison, "greenlee", "success_rate").values == (1.0, 1.0)


def test_compare_handles_missing_ammeters_and_zero_baseline(store):
    a = make_record(means=(("greenlee", [0.0, 0.0]),), minutes=0)
    b = make_record(means=(("greenlee", [1.0, 1.0]), ("entes", [5.0, 5.0])), minutes=1)
    store.save(a)
    store.save(b)
    comparison = store.compare(["latest", a.run_id])           # order requested is kept
    assert comparison.run_ids == (b.run_id, a.run_id)
    assert find(comparison, "entes", "mean").values == (5.0, None)
    assert find(comparison, "entes", "mean").change_pct is None
    assert find(store.compare([a.run_id, b.run_id]), "greenlee", "mean").change_pct is None  # baseline 0


def test_compare_needs_two_runs(store):
    store.save(make_record())
    with pytest.raises(ResultsError, match="at least two"):
        store.compare(["latest"])


def test_compare_run_where_every_sample_failed(store):
    failed = make_record(means=(("greenlee", [None, None]),), minutes=0)
    ok = make_record(minutes=1)
    store.save(failed)
    store.save(ok)
    comparison = store.compare([failed.run_id, ok.run_id])
    assert find(comparison, "greenlee", "mean").values == (None, 2.0)
    assert find(comparison, "greenlee", "success_rate").values == (0.0, 1.0)
