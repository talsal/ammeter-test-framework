# Design decisions

> Work in progress: this file is extended step by step. Sections still to come:
> architecture, trade-offs, libraries used, extension ideas.

## Fixes to the supplied code

Every change to the provided code, with what / why / where. Emulator *measurement
logic* (the physics formulas, value ranges, console prints in the subclasses) is
deliberately unchanged.

| # | What | Why | Where |
|---|------|-----|-------|
| 1 | Added `__init__.py` to `Ammeters/`, `src/`, `src/utils/`, `src/testing/` | Imports only worked as implicit namespace packages; explicit packages are reliable with pytest and tooling | those directories |
| 2 | Translated Hebrew comments/docstrings to English | Consistency; no logic changed | `src/utils/config.py`, `src/utils/logger.py`, `examples/run_tests.py` |
| 3 | Server socket sets `SO_REUSEADDR` | Without it, restarting right after a run fails with "Address already in use" (connections in TIME_WAIT). This is why the original `main.py` suggested "increase sleep time" | `Ammeters/base_ammeter.py` `start_server` |
| 4 | Unknown/empty request now gets an explicit reply `ERROR UNKNOWN_COMMAND` | The server used to send nothing, so the client could only say "No data received" and the cause (wrong command) was invisible | `base_ammeter.py` `handle_request` |
| 5 | Each connection is served in its own thread, with a per-connection timeout (`client_timeout`, default 5 s) | The server handled one client at a time and had no timeout: one client that connected and sent nothing blocked everyone forever | `base_ammeter.py` `_serve_client` |
| 6 | I/O errors on a connection (reset, broken pipe, timeout) are caught and logged; an exception inside `measure_current()` becomes an `ERROR INTERNAL` reply | A single bad client or measurement used to be able to kill the accept loop / thread | `base_ammeter.py` `_serve_client`, `handle_request` |
| 7 | Added `start()` / `stop()` / context manager, with a readiness event; `start()` re-raises bind errors (e.g. port in use) | `main.py` started daemon threads and slept 5 s hoping the servers were up; a bind failure was only a traceback in a background thread. `start()` returns exactly when the socket is listening | `base_ammeter.py` |
| 8 | `port=0` is supported; the real port is exposed as `self.port` after start | Lets integration tests use a free port instead of a hard-coded one | `base_ammeter.py` |
| 9 | `print()` in the base server replaced by `logging` (subclasses' measurement prints are untouched) | Library code should not write to stdout; log level is controlled by the caller | `base_ammeter.py` |
| 10 | `config/config.yaml` filled in and the `ammeters` section un-commented, with ports **5000 / 5001 / 5002** | The file was mostly empty. The ports follow the README and the config (the original `main.py` used 5001-5003, which contradicted both; `main.py` itself is fixed when the runner replaces it) | `config/config.yaml` |
| 11 | CIRCUTOR command in the config is `MEASURE_CIRCUTOR -get_measurement -current` | The emulator compares the request byte-for-byte against this string. The README and the commented config omitted `-current`, and `main.py`'s commented call sent just `MEASURE_CIRCUTOR`, so every one of these would have been rejected. `tests/test_config.py::test_shipped_config_matches_emulators` guards against the config drifting from the emulators again | `config/config.yaml` |
| 12 | `load_config` now returns a validated, typed `Config` (raises `ConfigError`) and no longer depends on the current directory for its default path | The original returned a raw dict, with no validation and a CWD-relative path | `src/utils/config.py` |
| 13 | `Ammeters/client.py` `request_current_from_ammeter` is now a thin wrapper over `AmmeterClient`: it has a timeout, reads until the server closes, returns the value and raises a specific exception | Original did one `recv(1024)` with no timeout, printed "No data received." for every failure (wrong command, dead server, empty reply all looked identical) and returned nothing | `Ammeters/client.py` |

| 14 | `main.py` rewritten as a thin entry point to the CLI (`src/cli.py`). Fixes: ports 5001-5003 -> 5000-5002 (from the config), the commented-out calls sent `MEASURE_GREENLEE` / `MEASURE_ENTES` / `MEASURE_CIRCUTOR` instead of the real commands, and `time.sleep(5)` replaced by `start()`, which returns exactly when each socket is listening | The original never produced any data: wrong ports, wrong commands, and a blind sleep instead of a readiness check | `main.py`, `src/cli.py`, `src/runner.py` |
| 15 | Removed `src/testing/test_framework.py` (and the package) | It could not even be imported (`Dict` never imported), `run_test()` was an empty stub and took an argument its caller did not pass. The README also referred to a non-existent `AmmeterTester.py`. Replaced by `src/runner.py` | `src/testing/` (deleted) |
| 16 | Removed `examples/run_tests.py`, added `examples/programmatic_usage.py` | The old script called `run_test()` with no argument (the README itself said "don't use it"). The new one shows the library layers (client, sampler, analysis) used without the CLI | `examples/` |
| 17 | `logger.py` finished: `setup_logging()` attaches a stderr handler and an optional file handler | The original built a log path but never attached a handler, so nothing was ever logged. The old `TestLogger` wrapper class was dropped because nothing needs it; modules use standard `logging.getLogger(__name__)`. The stderr handler looks up `sys.stderr` at emit time so captured/redirected streams cannot leave it writing to a closed file | `src/utils/logger.py` |
| 18 | Ammeter hosts are explicit `127.0.0.1` (config and defaults) instead of `localhost` | The emulators listen on IPv4 only, but `socket.create_connection("localhost")` tries `::1` first. On macOS the AirPlay Receiver (Control Center) holds port 5000 on IPv6, so the client connected to *it* and timed out. Found by running the CLI for real; covered by `test_shipped_hosts_are_explicit_ipv4`. Note: on macOS port 5000 is also taken on IPv4 by that process, but binding the specific address 127.0.0.1 still wins, so the emulator works on 5000 | `config/config.yaml`, `src/utils/config.py`, `Ammeters/base_ammeter.py`, `Ammeters/client.py` |

Error-reply convention: any reply starting with `ERROR` is an error; a measurement
is a bare float. The client layer (next steps) relies on this.
