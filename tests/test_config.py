"""Tests for configuration loading and validation."""
import pytest

from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.utils.config import ConfigError, SamplingConfig, load_config, parse_config

AMMETERS = {"greenlee": {"port": 5000, "command": "A"}}


def make(**overrides):
    """Minimal valid raw config, with top-level sections replaced by ``overrides``."""
    raw = {"testing": {"sampling": {"measurements_count": 5, "sampling_frequency_hz": 10}},
           "ammeters": AMMETERS}
    raw.update(overrides)
    return raw


# ---------------------------------------------------------------- the shipped config.yaml

def test_default_config_loads():
    config = load_config()
    assert set(config.ammeters) == {"greenlee", "entes", "circutor"}
    assert config.sampling.count == 20
    assert config.sampling.interval_seconds == pytest.approx(0.1)


def test_shipped_config_matches_emulators():
    """Regression guard for the original port/command mismatches between README, main.py and emulators."""
    ammeters = load_config().ammeters
    emulators = {"greenlee": GreenleeAmmeter, "entes": EntesAmmeter, "circutor": CircutorAmmeter}
    for name, cls in emulators.items():
        assert ammeters[name].command_bytes == cls(0).get_current_command, name
    assert [ammeters[n].port for n in emulators] == [5000, 5001, 5002]


def test_to_dict_is_json_serialisable_and_has_derived_sampling():
    import json
    data = load_config().to_dict()
    json.dumps(data)
    assert data["sampling"]["duration_seconds"] == pytest.approx(2.0)


# ---------------------------------------------------------------- sampling rules

@pytest.mark.parametrize("kwargs, count, interval", [
    (dict(measurements_count=20, sampling_frequency_hz=10), 20, 0.1),
    (dict(total_duration_seconds=10, sampling_frequency_hz=2), 20, 0.5),
    (dict(measurements_count=5, total_duration_seconds=10), 5, 2.0),
    (dict(measurements_count=20, total_duration_seconds=10, sampling_frequency_hz=2), 20, 0.5),
])
def test_sampling_derives_the_missing_value(kwargs, count, interval):
    s = SamplingConfig(**kwargs)
    assert (s.count, s.interval_seconds) == (count, pytest.approx(interval))
    assert s.duration_seconds == pytest.approx(count * interval)


@pytest.mark.parametrize("kwargs", [
    dict(),
    dict(measurements_count=5),
    dict(measurements_count=5, total_duration_seconds=10, sampling_frequency_hz=2),   # 10s*2Hz = 20
    dict(total_duration_seconds=0.1, sampling_frequency_hz=1),                        # rounds to 0 samples
])
def test_sampling_rejects_bad_combinations(kwargs):
    with pytest.raises(ConfigError):
        SamplingConfig(**kwargs)


# ---------------------------------------------------------------- schema validation

@pytest.mark.parametrize("raw, fragment", [
    ("just a string", "mapping"),
    (make(typo_section={}), "unknown key"),
    (make(ammeters={}), "at least one ammeter"),
    (make(ammeters={"x": {"command": "A"}}), "ammeters.x.port"),
    (make(ammeters={"x": {"port": 70000, "command": "A"}}), "1..65535"),
    (make(ammeters={"x": {"port": "5000", "command": "A"}}), "ammeters.x.port"),
    (make(ammeters={"x": {"port": True, "command": "A"}}), "ammeters.x.port"),
    (make(ammeters={"x": {"port": 5000}}), "ammeters.x.command"),
    (make(ammeters={"x": {"port": 5000, "command": "A", "prot": 1}}), "unknown key"),
    (make(testing={"sampling": {"measurements_count": -1, "sampling_frequency_hz": 1}}), "measurements_count"),
    (make(testing={"sampling": {"measurements_count": 2.5, "sampling_frequency_hz": 1}}), "integer"),
    (make(client={"timeout_seconds": 0}), "client.timeout_seconds"),
    (make(client={"retries": -1}), "client.retries"),
    (make(logging={"level": "LOUD"}), "logging.level"),
    (make(analysis={"visualization": {"plot_types": ["pie"]}}), "plot_types"),
    (make(analysis={"visualization": {"enabled": "yes"}}), "enabled"),
])
def test_invalid_config_raises_with_a_helpful_message(raw, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_config(raw)


def test_defaults_are_applied_for_optional_sections():
    config = parse_config(make())
    assert config.ammeters["greenlee"].host == "localhost"
    assert config.client.retries == 2
    assert config.visualization.plot_types == ("histogram", "time_series")


# ---------------------------------------------------------------- file handling

def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.yaml")


def test_malformed_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("ammeters: [unclosed")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)


def test_empty_file_raises_config_error(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(empty)
