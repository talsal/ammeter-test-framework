"""Using the framework as a library, without the CLI: client -> sampler -> analysis.

Run from the repository root:  python -m examples.programmatic_usage
"""
from Ammeters import GreenleeAmmeter
from src.ammeter_client import AmmeterClient, AmmeterError
from src.analysis import analyze
from src.sampler import Sampler
from src.utils.config import AmmeterConfig, ClientConfig, SamplingConfig


def main() -> None:
    with GreenleeAmmeter(port=0) as emulator:  # port 0: let the OS pick a free port
        ammeter = AmmeterConfig("greenlee", "127.0.0.1", emulator.port, emulator.get_current_command.decode())
        client = AmmeterClient(ammeter, ClientConfig(timeout_seconds=1.0, retries=1))

        try:
            print(f"single reading: {client.measure():.4f} A")
        except AmmeterError as exc:
            print(f"measurement failed: {exc}")
            return

        plan = SamplingConfig(total_duration_seconds=1.0, sampling_frequency_hz=20)  # 20 samples
        result = Sampler(plan).run(client)
        analysis = analyze(result)

    stats = analysis.current
    print(f"{analysis.successful}/{analysis.requested} samples ok, "
          f"mean {stats.mean:.4f} A, median {stats.median:.4f} A, stdev {stats.stdev:.4f} A, "
          f"CV {stats.cv:.1%} ({analysis.consistency})")


if __name__ == "__main__":
    main()
