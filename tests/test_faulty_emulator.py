"""Error simulation: FaultyEmulator injects faults, and the client/sampler/runner cope and report them."""
import socket

import pytest

from Ammeters.faulty import FaultyEmulator
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.ammeter_client import (
    AmmeterClient,
    AmmeterMalformedResponseError,
    AmmeterRejectedError,
    AmmeterTimeoutError,
)
from src.analysis import analyze
from src.sampler import Sampler
from src.utils.config import AmmeterConfig, ClientConfig, FaultProfile, SamplingConfig

NO_RETRY = ClientConfig(timeout_seconds=0.3, retries=0, retry_delay_seconds=0.0)


def faulty(profile: FaultProfile) -> FaultyEmulator:
    return FaultyEmulator(GreenleeAmmeter(0), profile, port=0)


def client_for(emulator, config=NO_RETRY) -> AmmeterClient:
    ammeter = AmmeterConfig("greenlee", "127.0.0.1", emulator.port, emulator.get_current_command.decode())
    return AmmeterClient(ammeter, config)


# ---------------------------------------------------------------- each fault, in isolation

def test_without_faults_it_behaves_like_the_real_emulator():
    with faulty(FaultProfile()) as emulator:
        assert 0.01 <= client_for(emulator).measure() <= 100
    assert not emulator.faults_injected


def test_dropped_connection_is_seen_as_an_empty_reply():
    with faulty(FaultProfile(drop_rate=1.0)) as emulator:
        with pytest.raises(AmmeterMalformedResponseError, match="empty reply"):
            client_for(emulator).measure()
    assert emulator.faults_injected["drop"] == 1


def test_garbage_reply_is_rejected_as_malformed():
    with faulty(FaultProfile(garbage_rate=1.0)) as emulator:
        with pytest.raises(AmmeterMalformedResponseError, match="not a number"):
            client_for(emulator).measure()


def test_error_reply_is_reported_as_rejected_and_not_retried():
    with faulty(FaultProfile(error_rate=1.0)) as emulator:
        with pytest.raises(AmmeterRejectedError, match="SIMULATED_FAULT") as info:
            client_for(emulator, ClientConfig(timeout_seconds=0.3, retries=3)).measure()
    assert info.value.attempts == 1 and emulator.faults_injected["error"] == 1


def test_slow_reply_beyond_the_timeout_is_a_timeout():
    with faulty(FaultProfile(delay_rate=1.0, delay_seconds=1.0)) as emulator:
        with pytest.raises(AmmeterTimeoutError):
            client_for(emulator).measure()


def test_slow_reply_within_the_timeout_still_succeeds_but_shows_in_the_latency():
    with faulty(FaultProfile(delay_rate=1.0, delay_seconds=0.15)) as emulator:
        result = Sampler(SamplingConfig(measurements_count=2, sampling_frequency_hz=50)).run(
            client_for(emulator, ClientConfig(timeout_seconds=1.0, retries=0)))
    assert result.failure_count == 0 and all(s.latency_s >= 0.15 for s in result.samples)


def test_a_wrong_command_is_still_a_protocol_error_not_a_simulated_fault():
    with faulty(FaultProfile(drop_rate=1.0)) as emulator:
        with socket.create_connection(("127.0.0.1", emulator.port), timeout=1) as s:
            s.sendall(b"WRONG")
            assert s.recv(1024) == b"ERROR UNKNOWN_COMMAND"
    assert not emulator.faults_injected


def test_a_delayed_request_does_no_work_after_the_emulator_is_stopped(capsys):
    """Regression: a handler sleeping in a delay fault used to wake up after stop() and print a
    measurement into whatever stdout was current by then (it leaked into a CLI report)."""
    import threading
    import time
    emulator = faulty(FaultProfile(delay_rate=1.0, delay_seconds=0.5))
    emulator.start()
    requester = threading.Thread(target=lambda: _ignore_errors(client_for(emulator).measure), daemon=True)
    requester.start()
    time.sleep(0.1)                                      # the handler is now inside its delay
    emulator.stop()
    time.sleep(0.8)                                      # long enough for the 0.5 s delay to have expired
    assert capsys.readouterr().out == ""                 # the woken handler printed no measurement


def _ignore_errors(call):
    try:
        call()
    except Exception:
        pass


# ---------------------------------------------------------------- reproducibility

def fault_sequence(seed, requests=40):
    emulator = faulty(FaultProfile(drop_rate=0.2, garbage_rate=0.2, error_rate=0.2, seed=seed))
    return [emulator._pick_fault() for _ in range(requests)]


def test_same_seed_gives_the_same_fault_sequence():
    assert fault_sequence(5) == fault_sequence(5)
    assert fault_sequence(5) != fault_sequence(6)


def test_fault_frequencies_follow_the_configured_rates():
    sequence = fault_sequence(seed=1, requests=2000)
    for kind in ("drop", "garbage", "error"):
        assert sequence.count(kind) / 2000 == pytest.approx(0.2, abs=0.04)
    assert sequence.count("delay") == 0
    assert sequence.count(None) / 2000 == pytest.approx(0.4, abs=0.04)


# ---------------------------------------------------------------- the framework copes

def sample_with_faults(profile, client_config, count=40):
    with faulty(profile) as emulator:
        result = Sampler(SamplingConfig(measurements_count=count, sampling_frequency_hz=200)).run(
            client_for(emulator, client_config))
    return result, emulator


def test_sampler_records_every_fault_and_keeps_going():
    profile = FaultProfile(drop_rate=0.2, garbage_rate=0.15, error_rate=0.15, seed=3)
    result, emulator = sample_with_faults(profile, NO_RETRY)

    assert len(result.samples) == 40                                  # nothing aborted the run
    injected = sum(emulator.faults_injected.values())
    assert injected > 0 and result.failure_count == injected          # every fault is accounted for
    assert set(result.error_summary) <= {"AmmeterMalformedResponseError", "AmmeterRejectedError"}
    assert len(result.values) == 40 - injected                        # failed samples carry no value

    analysis = analyze(result)
    assert analysis.successful == len(result.values)
    assert analysis.success_rate == pytest.approx(1 - injected / 40)
    assert analysis.current.count == analysis.successful               # statistics use good samples only


def test_retries_recover_transient_faults():
    profile = FaultProfile(drop_rate=0.4, seed=11)
    without, _ = sample_with_faults(profile, NO_RETRY)
    with_retries, _ = sample_with_faults(profile, ClientConfig(timeout_seconds=0.3, retries=4,
                                                               retry_delay_seconds=0.0))
    assert without.failure_count > 5
    assert with_retries.failure_count < without.failure_count
    assert any(s.ok and s.attempts > 1 for s in with_retries.samples)  # succeeded after a retry
    assert analyze(with_retries).retried_count > 0


def test_a_completely_broken_ammeter_yields_a_result_not_an_exception():
    result, _ = sample_with_faults(FaultProfile(garbage_rate=1.0), NO_RETRY, count=5)
    analysis = analyze(result)
    assert analysis.successful == 0 and analysis.current is None and analysis.consistency == "insufficient data"
