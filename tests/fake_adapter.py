"""Adapter giả cho test _base. Hành vi chọn bằng spec["inputs"]["mode"] để cùng một lớp chạy được cả in-process lẫn
qua subprocess (`python tests/fake_adapter.py --spec ...`). Không có tên worker thật nào ở đây."""
import sys
import textwrap
from pathlib import Path

from qc_agent.adapters._base import Adapter, AdapterParseError, ParsedOutput

_CHILD = "import time\nwhile True:\n    open(r'{beat}', 'a').write('x'); time.sleep(0.1)\n"
_SPAWN = textwrap.dedent("""
    import subprocess, sys, time
    subprocess.Popen([sys.executable, '-c', {child!r}])
    time.sleep(60)
""")


class FakeAdapter(Adapter):
    NAME = "fake"
    ADAPTER_VERSION = "0.0.1"

    def build_cmd(self, spec, workdir: Path) -> list[str]:
        mode = spec["inputs"].get("mode", "ok")
        if mode == "sleep":
            return [sys.executable, "-c", "import time; time.sleep(60)"]
        if mode == "spawn_child":  # cha đẻ ra cháu rồi ngủ: kiểm tra giết cả cây
            child = _CHILD.format(beat=spec["inputs"]["beat_file"])
            return [sys.executable, "-c", _SPAWN.format(child=child)]
        return [sys.executable, "-c", "print('ok')"]

    def parse_output(self, proc, workdir: Path, spec) -> ParsedOutput:
        mode = spec["inputs"].get("mode", "ok")
        if mode == "parse_error":
            raise AdapterParseError("không đọc được summary")
        if mode == "keyerror":
            return {}["metrics"]
        if mode == "vn_parse_error":
            raise AdapterParseError("không parse được tệp tóm tắt: định dạng lỗi — Đặng Thị Ánh")

        raw = workdir / "raw.json"
        raw.write_text('{"ok": true}', encoding="utf-8")
        out = ParsedOutput(evidence_paths=[("raw_output", raw)], exit_code=proc.returncode,
                           replay_cmd="fake --replay", adapter_notes=[f"stdout={proc.stdout.strip()}"])
        if mode == "checks_fail":
            out.signals = {"checks": {"a": True, "b": False}}
        elif mode == "llm_no_confidence":
            out.findings = [{"finding_id": "f-1", "title": "có vẻ sai", "detected_by": "agent",
                             "verdict_source": "llm_judgment", "confidence": None}]
        elif mode == "no_evidence":
            out.evidence_paths = []
        elif mode == "ghost_evidence":
            out.evidence_paths = [("raw_output", workdir / "khong_co.json")]
        elif mode == "flow_failed":
            out.flow_failed = True
        elif mode == "tokens":
            out.tokens, out.usd = 1234, 0.02
        return out


if __name__ == "__main__":
    raise SystemExit(FakeAdapter().main())
