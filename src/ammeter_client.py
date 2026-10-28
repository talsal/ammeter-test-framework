"""Unified client for all ammeters.

Every emulator speaks the same protocol: the client connects, sends one command and
reads until the server closes the connection. The reply is either a bare float (the
measurement) or text starting with ``ERROR``. One class therefore serves every ammeter
type; the differences (host, port, command) come from AmmeterConfig.
"""
import logging
import math
import socket
import time

from src.utils.config import AmmeterConfig, ClientConfig

logger = logging.getLogger(__name__)

ERROR_PREFIX = "ERROR"
MAX_RESPONSE_BYTES = 1024  # a measurement is a few dozen bytes; anything larger is garbage


class AmmeterError(Exception):
    """Base class for all client errors. ``retryable`` tells the client whether another attempt may help."""
    retryable = False

    def __init__(self, ammeter: str, message: str):
        super().__init__(f"{ammeter}: {message}")
        self.ammeter = ammeter
        self.attempts = 1  # filled in by AmmeterClient.measure()


class AmmeterConnectionError(AmmeterError):
    """Could not connect, or the connection broke (refused, reset, unreachable)."""
    retryable = True


class AmmeterTimeoutError(AmmeterError):
    """The ammeter did not complete the exchange within the timeout."""
    retryable = True


class AmmeterMalformedResponseError(AmmeterError):
    """The reply was empty, not text, not a number, or too large."""
    retryable = True


class AmmeterRejectedError(AmmeterError):
    """The ammeter answered with an explicit ERROR reply (e.g. unknown command). Retrying will not help."""
    retryable = False


class AmmeterClient:
    """Reads current measurements from one ammeter, with timeout and retries."""

    def __init__(self, ammeter: AmmeterConfig, client: ClientConfig = ClientConfig()):
        self._ammeter = ammeter
        self._timeout = client.timeout_seconds
        self._retries = client.retries
        self._retry_delay = client.retry_delay_seconds
        self.last_attempts = 0  # attempts used by the most recent measure() call

    @property
    def name(self) -> str:
        return self._ammeter.name

    def measure(self) -> float:
        """
        Returns one current measurement in amperes.
        Retryable failures are retried up to ``retries`` extra times; the last error is
        then raised (with ``attempts`` set). Non-retryable errors are raised immediately.
        """
        max_attempts = self._retries + 1
        for attempt in range(1, max_attempts + 1):
            self.last_attempts = attempt
            try:
                return self._measure_once()
            except AmmeterError as exc:
                exc.attempts = attempt
                if not exc.retryable or attempt == max_attempts:
                    raise
                logger.warning("%s (attempt %d/%d), retrying", exc, attempt, max_attempts)
                time.sleep(self._retry_delay)
        raise AssertionError("unreachable")  # the loop always returns or raises

    def _measure_once(self) -> float:
        deadline = time.monotonic() + self._timeout  # one budget for connect + send + receive
        try:
            with socket.create_connection((self._ammeter.host, self._ammeter.port),
                                          timeout=self._timeout) as s:
                s.sendall(self._ammeter.command_bytes)
                raw = self._receive_all(s, deadline)
        except socket.timeout as exc:  # must come before OSError, which it subclasses
            raise AmmeterTimeoutError(self.name, f"no complete reply within {self._timeout}s") from exc
        except OSError as exc:
            raise AmmeterConnectionError(
                self.name, f"cannot talk to {self._ammeter.host}:{self._ammeter.port} ({exc})") from exc
        return self._parse(raw)

    def _receive_all(self, s: socket.socket, deadline: float) -> bytes:
        """Reads until the server closes the connection, within the deadline and size limit."""
        chunks = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout()
            s.settimeout(remaining)
            chunk = s.recv(MAX_RESPONSE_BYTES)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise AmmeterMalformedResponseError(self.name, f"reply larger than {MAX_RESPONSE_BYTES} bytes")
            chunks.append(chunk)

    def _parse(self, raw: bytes) -> float:
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise AmmeterMalformedResponseError(self.name, f"reply is not valid UTF-8: {raw[:40]!r}") from exc
        if not text:
            raise AmmeterMalformedResponseError(self.name, "empty reply")
        if text.startswith(ERROR_PREFIX):
            raise AmmeterRejectedError(self.name, f"ammeter replied {text!r}")
        try:
            value = float(text)
        except ValueError as exc:
            raise AmmeterMalformedResponseError(self.name, f"reply is not a number: {text[:40]!r}") from exc
        if not math.isfinite(value):
            raise AmmeterMalformedResponseError(self.name, f"reply is not a finite number: {text!r}")
        return value
