"""Khuôn adapter (Template Method): _base lo toàn bộ vòng đời; adapter con chỉ điền build_cmd + parse_output.

Quy tắc vàng: adapter được phép MẤT thông tin, KHÔNG được BỊA thông tin. Worker chết/quá giờ/output không đọc
được là `error` (gate đỏ nhãn hạ tầng), không phải `fail` (assert không thoả) — không có nhánh nào đổi ngoại lệ thành fail.
KHÔNG được có tên worker cụ thể nào trong file này.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import oracle
from core import evidence as evidence_lib
from core import schema
from oracle import OracleError


class AdapterParseError(Exception):
    """Output của worker thiếu/hỏng/mâu thuẫn -> status=error (`parse: ...`). Adapter ném cái này thay vì đoán."""


@dataclass
class ParsedOutput:
    metrics: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)  # ĐÃ đúng dạng findings[] của result.json
    signals: dict = field(default_factory=dict)  # đầu vào cho oracle: {"checks": {...}} | {"detected": [...]}
    evidence_paths: list = field(default_factory=list)  # [(kind, Path)]
    tokens: int | None = None  # None = worker không báo; TUYỆT ĐỐI không ước lượng
    usd: float | None = None
    exit_code: int | None = None
    flow_failed: bool = False  # CHỈ discovery: luồng tự hành thất bại (vd canary)
    replay_cmd: str | None = None
    adapter_notes: list = field(default_factory=list)


class Adapter(ABC):
    NAME: str  # -> result.worker.name
    ADAPTER_VERSION: str  # -> result.worker.adapter_version
    env: dict = {}  # biến môi trường thêm cho tiến trình worker (chỉ đọc, đừng sửa tại chỗ)

    def __init__(self):
        for attr in ("NAME", "ADAPTER_VERSION"):
            if not getattr(type(self), attr, None):
                raise TypeError(f"{type(self).__name__} phải khai báo {attr}")

    @abstractmethod
    def build_cmd(self, spec: dict, workdir: Path) -> list[str]:
        """Dịch spec.inputs -> dòng lệnh của worker. KHÔNG quyết định oracle, KHÔNG đọc spec.intent."""

    @abstractmethod
    def parse_output(self, proc: subprocess.CompletedProcess, workdir: Path, spec: dict) -> ParsedOutput:
        """Đọc output THÔ của worker -> metrics/findings/signals/evidence. KHÔNG so ngưỡng (việc của oracle/).
        Không parse được -> raise AdapterParseError."""

    # ───────── từ đây trở xuống adapter con KHÔNG override ─────────

    def run(self, spec: dict) -> dict:
        """Tiền điều kiện: spec đã qua schema.validate_task (main() lo). Không bao giờ ném ngoại lệ ra ngoài."""
        t0 = time.perf_counter()
        try:
            return self._run(spec, t0)
        except subprocess.TimeoutExpired:
            return self._error(spec, f"timeout: vượt budget.wallclock_s={spec['budget']['wallclock_s']}", t0)
        except OracleError as e:
            return self._error(spec, f"parse: oracle: {e}", t0)
        except (AdapterParseError, FileNotFoundError) as e:
            return self._error(spec, f"parse: {e}", t0)
        except Exception as e:  # noqa: BLE001 — chủ đích: mọi thứ còn lại là crash, không bao giờ là fail
            return self._error(spec, f"crash: {type(e).__name__}: {e}", t0)

    def _run(self, spec: dict, t0: float) -> dict:
        runs_root = Path(os.environ.get("QC_RUNS_DIR", "runs")).resolve()
        workdir = (runs_root / spec["run_id"] / spec["task_id"]).resolve()
        if runs_root not in workdir.parents:  # run_id/task_id là chuỗi tự do: không cho thoát khỏi thư mục runs
            raise ValueError(f"run_id/task_id trỏ ra ngoài thư mục runs: {workdir}")
        workdir.mkdir(parents=True, exist_ok=True)

        cmd = self.build_cmd(spec, workdir)
        proc = self._exec(cmd, spec["budget"]["wallclock_s"])  # timeout = budget, không có con số thứ hai
        out = self.parse_output(proc, workdir, spec)

        outcome = oracle.evaluate(spec["oracle"], out.metrics, out.signals)
        findings = list(out.findings) + list(outcome.findings)
        status, verdict = self._verdict(spec, out, outcome)

        # uri tương đối so với cwd (vd runs/r-1/t-1/x.json); nếu QC_RUNS_DIR nằm ngoài cwd thì so với cha của nó
        base = Path.cwd() if Path.cwd().resolve() in runs_root.parents else runs_root.parent
        evidence = evidence_lib.collect(out.evidence_paths, base=base)

        result = {
            "task_id": spec["task_id"],
            "run_id": spec["run_id"],
            "worker": {"name": self.NAME, "version": None, "adapter_version": self.ADAPTER_VERSION},
            "status": status,
            "verdict": verdict,
            "findings": findings,
            "metrics": out.metrics,
            "evidence": evidence,
            "cost": {"wallclock_s": round(time.perf_counter() - t0, 3), "tokens": out.tokens, "usd": out.usd},
            "sut_identity_ref": spec.get("sut_identity_ref"),
            "determinism": {"seed": spec["determinism"].get("seed"), "replay_cmd": out.replay_cmd},
            "adapter_notes": [str(n) for n in list(out.adapter_notes) + list(outcome.notes)],
        }

        violations = schema.validate_result(result) + schema.check_result_against_spec(spec, result)
        if violations:  # không sửa cho vừa: result sai contract là lỗi, báo error
            return self._error(spec, "contract: " + "; ".join(violations), t0)
        return result

    @staticmethod
    def _verdict(spec: dict, out: ParsedOutput, outcome: oracle.OracleOutcome) -> tuple[str, dict]:
        """Verdict theo spec.expected_result_kind, không theo ý worker."""
        if spec["expected_result_kind"] == "verdict":
            if outcome.value not in ("pass", "fail"):
                raise OracleError("task gate cần oracle phán pass/fail, oracle không phán (value=None)")
            return outcome.value, {"value": outcome.value, "verdict_source": "deterministic_assert",
                                   "gating": True, "confidence": None, "rationale": None}
        # candidate_finding (discovery): không bao giờ gating
        if out.flow_failed or outcome.value == "fail":
            return "fail", {"value": "fail", "verdict_source": "deterministic_assert",
                            "gating": False, "confidence": None, "rationale": None}
        return "pass", {"value": "non_gating", "verdict_source": "heuristic",
                        "gating": False, "confidence": None, "rationale": None}

    def _error(self, spec: dict, rationale: str, t0: float) -> dict:
        res = schema.make_result(spec, "error", rationale, self.NAME, self.ADAPTER_VERSION)
        res["cost"]["wallclock_s"] = round(time.perf_counter() - t0, 3)  # thời gian thật đã tiêu, kể cả khi lỗi
        return res

    def _exec(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        # Popen thay vì run(timeout=): run() chỉ giết tiến trình con trực tiếp, cây npx -> node -> chrome mồ côi.
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, **self.env, "PYTHONUTF8": "1"},
            start_new_session=(os.name != "nt"),  # POSIX: để killpg diệt được cả nhóm
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            raise
        return subprocess.CompletedProcess(
            cmd, proc.returncode,
            stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"))

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass  # cây đã chết sẵn
        try:
            proc.kill()  # dự phòng cho tiến trình gốc nếu lệnh trên không tới được
        except OSError:
            pass
        try:
            proc.communicate(timeout=5)  # gom nốt output, tránh zombie
        except subprocess.TimeoutExpired:
            pass  # cháu còn giữ pipe: bỏ qua. KHÔNG đóng pipe ở đây — luồng đọc (daemon) đang giữ nó, close() sẽ treo

    def main(self, argv=None) -> int:
        """Luôn exit 0 kể cả khi result là fail/error (verdict nằm trong JSON). Exit 2 nếu spec không đọc/parse/hợp lệ."""
        ap = argparse.ArgumentParser(description=f"{self.NAME} adapter: Task Spec JSON -> Result JSON")
        ap.add_argument("--spec", help="file Task Spec JSON (mặc định: đọc stdin)")
        ap.add_argument("--out", help="file Result JSON (mặc định: ghi stdout)")
        args = ap.parse_args(argv)

        try:
            raw = Path(args.spec).read_bytes() if args.spec else sys.stdin.buffer.read()
            spec = json.loads(raw.decode("utf-8-sig"))  # utf-8-sig: chịu được BOM của PowerShell 5.1
            errors = schema.validate_task(spec)
        except (OSError, ValueError) as e:  # UnicodeDecodeError và JSONDecodeError đều là ValueError
            errors = [f"không đọc/parse được spec: {e}"]
        if errors:
            _eprint("spec không hợp lệ:\n  - " + "\n  - ".join(errors[:10]))
            return 2

        text = json.dumps(self.run(spec), ensure_ascii=True)  # ASCII thuần: không vỡ ở stdout cp1252 của Windows
        try:
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(text + "\n", encoding="utf-8")
            else:
                sys.stdout.write(text + "\n")
                sys.stdout.flush()
        except OSError as e:  # không ghi được result: không thể im lặng exit 0 như thể đã có kết quả
            _eprint(f"không ghi được result: {e}")
            return 1
        return 0


def _eprint(msg: str) -> None:
    enc = sys.stderr.encoding or "utf-8"
    sys.stderr.write(msg.encode(enc, errors="backslashreplace").decode(enc) + "\n")
