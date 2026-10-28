"""Error simulation: wraps any emulator and makes it misbehave on purpose.

Used to test that the framework survives real-world failures (dropped connections,
slow replies, corrupted or error replies) and reports them correctly.
"""
import random
import threading
from collections import Counter
from typing import Optional

from Ammeters.base_ammeter import AmmeterEmulatorBase
from src.utils.config import FaultProfile

GARBAGE_RESPONSE = b"3.14.15-not-a-number"
SIMULATED_ERROR_RESPONSE = b"ERROR SIMULATED_FAULT"


class FaultyEmulator(AmmeterEmulatorBase):
    """
    Decorator around a real emulator: same command, same measurements, but each valid
    request may be hit by one fault drawn from the FaultProfile. Requests with a wrong
    command are still answered normally, so protocol errors stay distinguishable from
    injected faults. ``faults_injected`` counts what was actually injected, by kind.
    """

    def __init__(self, inner: AmmeterEmulatorBase, profile: FaultProfile, port: int,
                 host: str = "127.0.0.1", client_timeout: float = 5.0):
        super().__init__(port, host=host, client_timeout=client_timeout)
        self._inner = inner
        self._profile = profile
        self._rng = random.Random(profile.seed)  # private generator: reproducible, and does not touch the global one
        self._rng_lock = threading.Lock()        # requests are handled in parallel threads
        self.faults_injected: Counter = Counter()

    @property
    def get_current_command(self) -> bytes:
        return self._inner.get_current_command

    def measure_current(self) -> float:
        return self._inner.measure_current()

    def _pick_fault(self) -> Optional[str]:
        p = self._profile
        with self._rng_lock:
            roll = self._rng.random()
        for kind, rate in (("drop", p.drop_rate), ("delay", p.delay_rate),
                           ("garbage", p.garbage_rate), ("error", p.error_rate)):
            if roll < rate:
                return kind
            roll -= rate
        return None

    def handle_request(self, data: bytes) -> Optional[bytes]:
        if data != self.get_current_command:
            return super().handle_request(data)
        fault = self._pick_fault()
        if fault:
            self.faults_injected[fault] += 1
        if fault == "drop":
            return None
        if fault == "garbage":
            return GARBAGE_RESPONSE
        if fault == "error":
            return SIMULATED_ERROR_RESPONSE
        if fault == "delay" and self._stop_event.wait(self._profile.delay_seconds):
            return None  # the emulator was stopped during the delay: do not do any more work
        return super().handle_request(data)
