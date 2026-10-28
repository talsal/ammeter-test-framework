"""Typed, validated configuration loaded from YAML.

The loader is strict on purpose: unknown keys, wrong types and impossible
combinations raise ConfigError with the path of the offending key, so a typo in
config.yaml fails immediately instead of silently running a different test.
"""
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
PLOT_TYPES = ("histogram", "time_series")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """The configuration file is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class AmmeterConfig:
    """How to reach one ammeter. All ammeters share one text protocol, so this is all a client needs."""
    name: str
    host: str
    port: int
    command: str

    @property
    def command_bytes(self) -> bytes:
        return self.command.encode("utf-8")


@dataclass(frozen=True)
class SamplingConfig:
    """
    Sampling plan. Give any two of the three values; the third is derived.
    If all three are given they must agree (count == duration * frequency).
    Sample k is taken at k * interval, so the plan covers [0, duration).
    """
    measurements_count: Optional[int] = None
    total_duration_seconds: Optional[float] = None
    sampling_frequency_hz: Optional[float] = None

    def __post_init__(self) -> None:
        given = [v for v in (self.measurements_count, self.total_duration_seconds,
                             self.sampling_frequency_hz) if v is not None]
        if len(given) < 2:
            raise ConfigError(
                "sampling: set at least two of measurements_count, total_duration_seconds, "
                "sampling_frequency_hz (the third is derived)")
        if self.count < 1:
            raise ConfigError("sampling: the plan yields no samples (duration * frequency < 0.5)")
        if len(given) == 3:
            implied = round(self.total_duration_seconds * self.sampling_frequency_hz)
            if implied != self.measurements_count:
                raise ConfigError(
                    f"sampling: inconsistent values: {self.total_duration_seconds}s at "
                    f"{self.sampling_frequency_hz}Hz is {implied} samples, not {self.measurements_count}")

    @property
    def count(self) -> int:
        if self.measurements_count is not None:
            return self.measurements_count
        return round(self.total_duration_seconds * self.sampling_frequency_hz)

    @property
    def interval_seconds(self) -> float:
        if self.sampling_frequency_hz is not None:
            return 1.0 / self.sampling_frequency_hz
        return self.total_duration_seconds / self.measurements_count

    @property
    def duration_seconds(self) -> float:
        return self.count * self.interval_seconds


@dataclass(frozen=True)
class ClientConfig:
    timeout_seconds: float = 2.0       # per connection attempt (connect + receive)
    retries: int = 2                   # extra attempts after the first failure
    retry_delay_seconds: float = 0.1


@dataclass(frozen=True)
class AnalysisConfig:
    outlier_z_threshold: float = 3.0   # |z-score| above this counts as an outlier


@dataclass(frozen=True)
class VisualizationConfig:
    enabled: bool = True               # needs matplotlib; skipped with a warning if missing
    plot_types: Tuple[str, ...] = PLOT_TYPES


@dataclass(frozen=True)
class ResultsConfig:
    directory: str = "results"


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    to_file: bool = True               # also write <results directory>/logs/<run>.log


@dataclass(frozen=True)
class Config:
    ammeters: Dict[str, AmmeterConfig]
    sampling: SamplingConfig
    client: ClientConfig = field(default_factory=ClientConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
    results: ResultsConfig = field(default_factory=ResultsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict snapshot, including the derived sampling values. JSON-native types only
        (tuples become lists), so it compares equal after a save/load round trip."""
        data = dataclasses.asdict(self)
        data["sampling"].update(count=self.sampling.count,
                                interval_seconds=self.sampling.interval_seconds,
                                duration_seconds=self.sampling.duration_seconds)
        return json.loads(json.dumps(data))


# ---------------------------------------------------------------------- parsing helpers

def _section(raw: Mapping, key: str, where: str = "") -> Mapping:
    """Return raw[key] as a mapping; a missing or empty section counts as {}."""
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where}{key}: expected a mapping, got {type(value).__name__}")
    return value


def _reject_unknown(section: Mapping, allowed: Tuple[str, ...], where: str) -> None:
    unknown = sorted(set(section) - set(allowed))
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}; allowed: {list(allowed)}")


def _number(section: Mapping, key: str, where: str, default: Any = None, *,
            integer: bool = False, minimum: float = 0.0, inclusive: bool = False) -> Any:
    """Read an optional number; None (or missing) gives ``default``. Rejects bool, enforces a bound."""
    value = section.get(key)
    if value is None:
        return default
    kinds = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds):
        raise ConfigError(f"{where}.{key}: expected {'an integer' if integer else 'a number'}, got {value!r}")
    if value < minimum or (value == minimum and not inclusive):
        raise ConfigError(f"{where}.{key}: must be {'>=' if inclusive else '>'} {minimum}, got {value!r}")
    return value


def _string(section: Mapping, key: str, where: str, default: Optional[str] = None) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key}: expected a non-empty string, got {value!r}")
    return value


def _parse_ammeters(raw: Mapping) -> Dict[str, AmmeterConfig]:
    section = _section(raw, "ammeters")
    if not section:
        raise ConfigError("ammeters: at least one ammeter must be configured")
    ammeters = {}
    for name, entry in section.items():
        where = f"ammeters.{name}"
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{where}: expected a mapping with host/port/command")
        _reject_unknown(entry, ("host", "port", "command"), where)
        port = _number(entry, "port", where, integer=True)
        if port is None or port > 65535:
            raise ConfigError(f"{where}.port: required, an integer in 1..65535, got {port!r}")
        ammeters[str(name)] = AmmeterConfig(
            name=str(name),
            host=_string(entry, "host", where, default="localhost"),
            port=port,
            command=_string(entry, "command", where),
        )
    return ammeters


def _parse_sampling(raw: Mapping) -> SamplingConfig:
    where = "testing.sampling"
    section = _section(_section(raw, "testing"), "sampling", "testing.")
    _reject_unknown(section, ("measurements_count", "total_duration_seconds", "sampling_frequency_hz"), where)
    return SamplingConfig(
        measurements_count=_number(section, "measurements_count", where, integer=True),
        total_duration_seconds=_number(section, "total_duration_seconds", where),
        sampling_frequency_hz=_number(section, "sampling_frequency_hz", where),
    )


def _parse_client(raw: Mapping) -> ClientConfig:
    section = _section(raw, "client")
    _reject_unknown(section, ("timeout_seconds", "retries", "retry_delay_seconds"), "client")
    d = ClientConfig()
    return ClientConfig(
        timeout_seconds=_number(section, "timeout_seconds", "client", d.timeout_seconds),
        retries=_number(section, "retries", "client", d.retries, integer=True, inclusive=True),
        retry_delay_seconds=_number(section, "retry_delay_seconds", "client", d.retry_delay_seconds,
                                    inclusive=True),
    )


def _parse_analysis(raw: Mapping) -> AnalysisConfig:
    section = _section(raw, "analysis")
    _reject_unknown(section, ("outlier_z_threshold", "visualization"), "analysis")
    return AnalysisConfig(
        outlier_z_threshold=_number(section, "outlier_z_threshold", "analysis",
                                    AnalysisConfig().outlier_z_threshold),
    )


def _parse_visualization(raw: Mapping) -> VisualizationConfig:
    section = _section(_section(raw, "analysis"), "visualization", "analysis.")
    where = "analysis.visualization"
    _reject_unknown(section, ("enabled", "plot_types"), where)
    d = VisualizationConfig()
    enabled = section.get("enabled", d.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled: expected true/false, got {enabled!r}")
    plot_types = section.get("plot_types") or list(d.plot_types)
    if not isinstance(plot_types, (list, tuple)) or not set(plot_types) <= set(PLOT_TYPES):
        raise ConfigError(f"{where}.plot_types: expected a list drawn from {list(PLOT_TYPES)}, got {plot_types!r}")
    return VisualizationConfig(enabled=enabled, plot_types=tuple(plot_types))


def _parse_results(raw: Mapping) -> ResultsConfig:
    section = _section(raw, "result_management")
    _reject_unknown(section, ("directory",), "result_management")
    return ResultsConfig(directory=_string(section, "directory", "result_management",
                                           ResultsConfig().directory))


def _parse_logging(raw: Mapping) -> LoggingConfig:
    section = _section(raw, "logging")
    _reject_unknown(section, ("level", "to_file"), "logging")
    d = LoggingConfig()
    level = str(section.get("level", d.level)).upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"logging.level: expected one of {list(LOG_LEVELS)}, got {section.get('level')!r}")
    to_file = section.get("to_file", d.to_file)
    if not isinstance(to_file, bool):
        raise ConfigError(f"logging.to_file: expected true/false, got {to_file!r}")
    return LoggingConfig(level=level, to_file=to_file)


def parse_config(raw: Any) -> Config:
    """Validate an already-loaded YAML document and build a Config."""
    if not isinstance(raw, Mapping):
        raise ConfigError("the configuration must be a mapping at the top level")
    _reject_unknown(raw, ("testing", "ammeters", "analysis", "client", "result_management", "logging"),
                    "top level")
    _reject_unknown(_section(raw, "testing"), ("sampling",), "testing")
    return Config(
        ammeters=_parse_ammeters(raw),
        sampling=_parse_sampling(raw),
        client=_parse_client(raw),
        analysis=_parse_analysis(raw),
        visualization=_parse_visualization(raw),
        results=_parse_results(raw),
        logging=_parse_logging(raw),
    )


def load_config(config_path: Union[str, Path] = DEFAULT_CONFIG_PATH) -> Config:
    """Load and validate the YAML configuration file. Raises ConfigError on any problem."""
    path = Path(config_path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    try:
        return parse_config(raw)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
