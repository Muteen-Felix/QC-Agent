"""Second fixture-backed worker used to prove workers can be added without core changes."""

from tests.fixtures.workers.mock_adapter import MockAdapter


class Mock2Adapter(MockAdapter):
    NAME = "mock2"
    ADAPTER_VERSION = "0.1.0"


if __name__ == "__main__":
    raise SystemExit(Mock2Adapter().main())
