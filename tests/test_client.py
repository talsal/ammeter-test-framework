"""Tests for AmmeterClient, using real local sockets and a scripted server that misbehaves on demand."""
import socket
import threading
import time

import pytest

from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from Ammeters.client import request_current_from_ammeter
from src.ammeter_client import (
    AmmeterClient,
    AmmeterConnectionError,
    AmmeterError,
    AmmeterMalformedResponseError,
    AmmeterRejectedError,
    AmmeterTimeoutError,
)
from src.utils.config import AmmeterConfig, ClientConfig

COMMAND = "MEASURE_TEST"
FAST = ClientConfig(timeout_seconds=0.3, retries=2, retry_delay_seconds=0.0)


class ScriptedServer:
    """
    Accepts connections and applies one behaviour per connection, in order (the last
    behaviour repeats). A behaviour is a function(conn). Records what clients sent.
    """

    def __init__(self, *behaviours):
        self._behaviours = list(behaviours)
        self.received = []
        self.connections = 0
        self._release = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("localhost", 0))
        self._sock.listen()
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            index = min(self.connections, len(self._behaviours) - 1)
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn, self._behaviours[index]), daemon=True).start()

    def _serve(self, conn, behaviour):
        with conn:
            self.received.append(conn.recv(1024))
            behaviour(conn, self._release)

    def close(self):
        self._release.set()
        self._sock.close()


# Behaviours -------------------------------------------------------------------

def reply(data: bytes):
    return lambda conn, release: conn.sendall(data)

def close_silently(conn, release):
    pass

def hang(conn, release):
    release.wait(5)

def drip_forever(conn, release):
    """Sends one byte every 50 ms and never closes: only an overall deadline stops the client."""
    while not release.is_set():
        conn.sendall(b"1")
        release.wait(0.05)

def reply_in_two_chunks(conn, release):
    conn.sendall(b"1.2")
    time.sleep(0.05)
    conn.sendall(b"5")


@pytest.fixture
def serve():
    servers = []
    def factory(*behaviours):
        server = ScriptedServer(*behaviours)
        servers.append(server)
        return server
    yield factory
    for server in servers:
        server.close()


def client_for(server, config=FAST, port=None):
    return AmmeterClient(AmmeterConfig("fake", "localhost", port or server.port, COMMAND), config)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


# Happy path -------------------------------------------------------------------

def test_returns_the_measurement_and_sends_the_exact_command(serve):
    server = serve(reply(b"1.25"))
    assert client_for(server).measure() == 1.25
    assert server.received == [COMMAND.encode()]


def test_reply_arriving_in_several_chunks_is_reassembled(serve):
    server = serve(reply_in_two_chunks)
    assert client_for(server).measure() == 1.25


def test_surrounding_whitespace_is_ignored(serve):
    assert client_for(serve(reply(b" 0.5\n"))).measure() == 0.5


def test_works_against_a_real_emulator():
    with GreenleeAmmeter(0) as emulator:
        config = AmmeterConfig("greenlee", "localhost", emulator.port, emulator.get_current_command.decode())
        assert 0.01 <= AmmeterClient(config, FAST).measure() <= 100.0


# Error classification ---------------------------------------------------------

@pytest.mark.parametrize("payload", [b"", b"   ", b"abc", b"1.2.3", b"nan", b"inf", b"\xff\xfe", b"1" * 2000])
def test_malformed_replies_are_reported(serve, payload):
    server = serve(reply(payload))
    with pytest.raises(AmmeterMalformedResponseError):
        client_for(server).measure()


def test_server_error_reply_is_rejected_and_not_retried(serve):
    server = serve(reply(b"ERROR UNKNOWN_COMMAND"))
    with pytest.raises(AmmeterRejectedError, match="UNKNOWN_COMMAND") as info:
        client_for(server).measure()
    assert server.connections == 1
    assert info.value.attempts == 1


def test_real_emulator_rejects_a_wrong_command():
    with GreenleeAmmeter(0) as emulator:
        config = AmmeterConfig("greenlee", "localhost", emulator.port, "MEASURE_GREENLEE")  # old, wrong command
        with pytest.raises(AmmeterRejectedError, match="UNKNOWN_COMMAND"):
            AmmeterClient(config, FAST).measure()


def test_unresponsive_server_times_out(serve):
    server = serve(hang)
    start = time.perf_counter()
    with pytest.raises(AmmeterTimeoutError):
        client_for(server, ClientConfig(timeout_seconds=0.2, retries=0)).measure()
    assert time.perf_counter() - start < 1.0


def test_timeout_is_a_deadline_for_the_whole_exchange_not_per_recv(serve):
    server = serve(drip_forever)
    start = time.perf_counter()
    with pytest.raises(AmmeterError):  # timeout, or oversized reply: either way it must stop
        client_for(server, ClientConfig(timeout_seconds=0.3, retries=0)).measure()
    assert time.perf_counter() - start < 1.0


def test_connection_refused_is_reported():
    config = ClientConfig(timeout_seconds=0.3, retries=0)
    client = AmmeterClient(AmmeterConfig("fake", "localhost", free_port(), COMMAND), config)
    with pytest.raises(AmmeterConnectionError):
        client.measure()


# Retries ----------------------------------------------------------------------

def test_transient_failure_is_retried_and_succeeds(serve):
    server = serve(close_silently, reply(b"2.0"))
    assert client_for(server).measure() == 2.0
    assert server.connections == 2


def test_gives_up_after_retries_and_reports_attempts(serve):
    server = serve(close_silently)
    with pytest.raises(AmmeterMalformedResponseError) as info:
        client_for(server).measure()  # FAST: retries=2 -> 3 attempts
    assert server.connections == 3
    assert info.value.attempts == 3


def test_zero_retries_means_a_single_attempt(serve):
    server = serve(close_silently)
    with pytest.raises(AmmeterError):
        client_for(server, ClientConfig(timeout_seconds=0.3, retries=0)).measure()
    assert server.connections == 1


def test_error_message_names_the_ammeter(serve):
    with pytest.raises(AmmeterError, match="^fake:"):
        client_for(serve(reply(b"junk")), ClientConfig(timeout_seconds=0.3, retries=0)).measure()


# Legacy helper ----------------------------------------------------------------

def test_legacy_helper_returns_value_and_raises_on_failure(capsys):
    with GreenleeAmmeter(0) as emulator:
        value = request_current_from_ammeter(emulator.port, emulator.get_current_command)
        assert value > 0
        with pytest.raises(AmmeterRejectedError):
            request_current_from_ammeter(emulator.port, b"MEASURE_GREENLEE")
