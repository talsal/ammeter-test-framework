"""Tests for the emulator server base class (socket handling, lifecycle, error replies)."""
import socket
import time

import pytest

from Ammeters.base_ammeter import (
    AmmeterEmulatorBase,
    INTERNAL_ERROR_RESPONSE,
    UNKNOWN_COMMAND_RESPONSE,
)
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter

COMMAND = b"MEASURE_FAKE"


class FakeAmmeter(AmmeterEmulatorBase):
    """Deterministic emulator used to test the server logic in isolation."""
    fail = False

    @property
    def get_current_command(self) -> bytes:
        return COMMAND

    def measure_current(self) -> float:
        if self.fail:
            raise RuntimeError("boom")
        return 1.5


def request(port: int, payload: bytes, timeout: float = 2.0) -> bytes:
    with socket.create_connection(("localhost", port), timeout=timeout) as s:
        s.sendall(payload)
        return s.recv(1024)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake():
    with FakeAmmeter(0, client_timeout=0.5) as emulator:
        yield emulator


def test_port_zero_resolves_to_real_port(fake):
    assert fake.port > 0


def test_valid_command_returns_measurement(fake):
    assert float(request(fake.port, COMMAND)) == 1.5


def test_unknown_command_gets_error_reply_not_silence(fake):
    assert request(fake.port, b"MEASURE_GREENLEE") == UNKNOWN_COMMAND_RESPONSE


def test_failing_measurement_gets_internal_error_and_server_survives(fake):
    fake.fail = True
    assert request(fake.port, COMMAND) == INTERNAL_ERROR_RESPONSE
    fake.fail = False
    assert float(request(fake.port, COMMAND)) == 1.5


def test_client_that_connects_and_closes_does_not_kill_server(fake):
    socket.create_connection(("localhost", fake.port)).close()
    assert float(request(fake.port, COMMAND)) == 1.5


def test_stalled_client_does_not_block_other_clients(fake):
    with socket.create_connection(("localhost", fake.port)):  # connects, never sends
        start = time.perf_counter()
        assert float(request(fake.port, COMMAND)) == 1.5
        assert time.perf_counter() - start < 0.4  # well below the 0.5s client_timeout


def test_restart_on_same_port_works():
    port = free_port()
    for _ in range(2):
        with FakeAmmeter(port):
            assert float(request(port, COMMAND)) == 1.5


def test_start_raises_when_port_in_use():
    with socket.socket() as blocker:
        blocker.bind(("localhost", 0))
        blocker.listen()
        with pytest.raises(OSError):
            FakeAmmeter(blocker.getsockname()[1]).start()


@pytest.mark.parametrize("cls, low, high", [
    (GreenleeAmmeter, 0.01, 100.0),      # 1-10 V / 0.1-100 ohm
    (EntesAmmeter, 5.0, 200.0),          # 0.01-0.1 T * 500-2000
    (CircutorAmmeter, 0.001, 0.1),       # 10 samples * 0.1-1.0 V * 0.001-0.01 s
])
def test_real_emulators_answer_their_own_command(cls, low, high):
    with cls(0) as emulator:
        value = float(request(emulator.port, emulator.get_current_command))
    assert low <= value <= high
