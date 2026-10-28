import logging
import socket
import threading
import time
import random
from abc import ABC, abstractmethod
from typing import Optional

NotImplementedErrorMsg = "Subclasses must implement this property."

# Replies sent when a request cannot be served. They share the "ERROR" prefix so a
# client can tell an error reply from a measurement (a measurement is a bare float).
UNKNOWN_COMMAND_RESPONSE = b"ERROR UNKNOWN_COMMAND"
INTERNAL_ERROR_RESPONSE = b"ERROR INTERNAL"

# How often the accept loop wakes up to check whether stop() was requested.
ACCEPT_POLL_INTERVAL_SECONDS = 0.2

logger = logging.getLogger(__name__)


class AmmeterEmulatorBase(ABC):
    def __init__(self, port: int, host: str = "localhost", client_timeout: float = 5.0):
        """
        :param port: TCP port to listen on. Use 0 to let the OS pick a free port;
                     the real port is then available as ``self.port`` once started.
        :param host: Interface to bind to.
        :param client_timeout: Max seconds to wait for a connected client to send its request.
        """
        self.port = port
        self.host = host
        self._client_timeout = client_timeout
        self._stop_event = threading.Event()
        self._started_event = threading.Event()  # set when the server is listening OR failed to start
        self._startup_error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        random.seed(time.time())  # Seed the random number generator for each instance

    # ------------------------------------------------------------------ lifecycle

    def start_server(self):
        """
        Starts the server to listen for client requests (blocking).
        Each client connection is served in its own thread, so a slow or stalled
        client cannot block the others. Returns only after stop() is called.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # Allow an immediate restart on the same port (avoids "Address already in use"
            # while the previous run's connections are in TIME_WAIT).
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            self.port = s.getsockname()[1]  # resolves port 0 to the port the OS chose
            s.listen()
            s.settimeout(ACCEPT_POLL_INTERVAL_SECONDS)  # lets the loop notice stop()
            logger.info("%s is running on port %s", self.__class__.__name__, self.port)
            self._started_event.set()
            while not self._stop_event.is_set():
                try:
                    conn, addr = s.accept()
                except socket.timeout:
                    continue
                except OSError:
                    logger.warning("accept() failed", exc_info=True)
                    continue
                threading.Thread(target=self._serve_client, args=(conn, addr), daemon=True).start()

    def start(self, timeout: float = 5.0) -> None:
        """
        Starts the server in a background thread and returns once it is accepting
        connections. Raises the original error (e.g. OSError: port in use) if it
        could not start, or TimeoutError if it did not come up within ``timeout``.
        """
        self._stop_event.clear()
        self._started_event.clear()
        self._startup_error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{type(self).__name__}-{self.port}")
        self._thread.start()
        if not self._started_event.wait(timeout):
            raise TimeoutError(f"{type(self).__name__} did not start within {timeout}s")
        if self._startup_error is not None:
            raise self._startup_error

    def stop(self, timeout: float = 5.0) -> None:
        """Stops a server started with start() and waits for it to release the port."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def __enter__(self) -> "AmmeterEmulatorBase":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def _run(self) -> None:
        try:
            self.start_server()
        except BaseException as exc:  # reported to the thread that called start()
            self._startup_error = exc
            self._started_event.set()

    # ------------------------------------------------------------------ request handling

    def _serve_client(self, conn: socket.socket, addr) -> None:
        """Serves one connection: read one request, send one response, close."""
        with conn:
            logger.debug("Connected by %s", addr)
            conn.settimeout(self._client_timeout)
            try:
                data = conn.recv(1024)
                conn.sendall(self.handle_request(data))
            except socket.timeout:
                logger.warning("Client %s sent no request within %ss", addr, self._client_timeout)
            except OSError:
                logger.warning("Connection error with client %s", addr, exc_info=True)

    def handle_request(self, data: bytes) -> bytes:
        """
        Maps a raw request to the raw response. Kept separate from the socket code
        so it is easy to test and to override (e.g. to inject faults).
        """
        if data != self.get_current_command:
            logger.warning("Unknown command received: %r", data)
            return UNKNOWN_COMMAND_RESPONSE
        try:
            # Call the specific measure_current() method defined in subclasses
            return str(self.measure_current()).encode('utf-8')
        except Exception:
            logger.exception("measure_current() failed")
            return INTERNAL_ERROR_RESPONSE

    @property
    @abstractmethod
    def get_current_command(self) -> bytes:
        """
        This property must be implemented by each subclass to provide the specific
        command to get the current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)

    @abstractmethod
    def measure_current(self) -> float:
        """
        This method must be implemented by each subclass to provide the specific
        logic for current measurement.
        """
        raise NotImplementedError(NotImplementedErrorMsg)
