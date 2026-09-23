"""Fixture-backed worker used to exercise the real adapter subprocess path."""
import json
import sys
from pathlib import Path

from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput


class MockAdapter(Adapter):
    NAME = "mock"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        return [sys.executable, "-c", "pass"]

    def parse_output(self, proc, workdir: Path, spec: dict) -> ParsedOutput:
        fixture = Path(spec["inputs"]["fixture"])
        if not fixture.is_file():
            raise AdapterParseError(f"fixture không tồn tại: {fixture}")
        try:
            data = json.loads(fixture.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise AdapterParseError(f"không đọc được fixture {fixture}: {exc}") from exc

        raw = workdir / "mock-output.json"
        stdout = workdir / "stdout.log"
        raw.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stdout.write_text(proc.stdout, encoding="utf-8")
        return ParsedOutput(
            metrics=data.get("metrics", {}),
            signals=data.get("signals", {}),
            findings=data.get("findings", []),
            flow_failed=data.get("flow_failed", False),
            evidence_paths=[("raw_output", raw), ("stdout", stdout)],
            tokens=0,
            usd=0.0,
            exit_code=proc.returncode,
        )


if __name__ == "__main__":
    raise SystemExit(MockAdapter().main())
