from src.ammeter_client import AmmeterClient
from src.utils.config import AmmeterConfig, ClientConfig


def request_current_from_ammeter(port: int, command: bytes, host: str = 'localhost',
                                 timeout: float = 2.0) -> float:
    """
    Request one measurement and print it (kept for backwards compatibility).
    Now a thin wrapper over src.ammeter_client.AmmeterClient: returns the value, and
    raises an AmmeterError subclass instead of silently printing "No data received."
    """
    config = AmmeterConfig(name=f"{host}:{port}", host=host, port=port, command=command.decode('utf-8'))
    current = AmmeterClient(config, ClientConfig(timeout_seconds=timeout)).measure()
    print(f"Received current measurement from port {port}: {current} A")
    return current
