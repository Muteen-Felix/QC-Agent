"""Adapter giả cho test_runner: đọc spec ở stdin, in result theo spec["inputs"]["mode"], và ghi 1 byte vào
$FAKE_COUNT_DIR/<task_id> mỗi lần bị spawn (để test đếm số lần chạy). Chạy bằng `python -m fake_worker`.
Không có tên worker thật nào ở đây."""
import json
import os
import sys
import time
from pathlib import Path

from qc_agent.core import schema

spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
mode, budget = spec["inputs"]["mode"], spec["budget"]

counter = Path(os.environ["FAKE_COUNT_DIR"]) / spec["task_id"]
with counter.open("a") as f:
    f.write("x")
spawn_no = counter.stat().st_size  # lần spawn thứ mấy của task này


def result(status, gating=True, tokens=None, usd=None):
    return {
        "task_id": spec["task_id"], "run_id": spec["run_id"],
        "worker": {"name": "fake", "version": None, "adapter_version": "0.0.1"},
        "status": status,
        "verdict": {"value": status if gating else "non_gating",
                    "verdict_source": "deterministic_assert" if gating else "heuristic",
                    "gating": gating, "confidence": None, "rationale": None},
        "findings": [], "metrics": {}, "evidence": [],
        "cost": {"wallclock_s": 0.1, "tokens": tokens, "usd": usd},
        "sut_identity_ref": spec["sut_identity_ref"], "determinism": {}, "adapter_notes": [],
    }


if mode == "crash":
    sys.stderr.write("boom\n")
    sys.exit(3)
if mode == "hang":  # phớt lờ budget của chính mình: chỉ runner mới cứu được
    time.sleep(60)
if mode == "garbage":
    print("đây không phải JSON")
    sys.exit(0)

if mode == "pass":  # cost đúng bằng budget: biên, không được bị cắt nhầm
    out = result("pass", tokens=budget["tokens"], usd=budget["usd"])
elif mode == "fail":
    out = result("fail", tokens=0, usd=0.0)
elif mode == "error":
    out = schema.make_result(spec, "error", "worker chết", "fake", "0.0.1")
elif mode == "flaky":  # error lần đầu, pass từ lần sau
    out = schema.make_result(spec, "error", "lỗi thoáng qua", "fake", "0.0.1") if spawn_no == 1 else result("pass")
elif mode == "tokens":
    out = result("pass", tokens=budget["tokens"] + 1, usd=0.0)
elif mode == "usd":
    out = result("pass", tokens=0, usd=budget["usd"] + 0.5)
elif mode == "bad_gate":  # đúng schema nhưng task gate mà gating=false: chỉ luật liên-trường bắt được
    out = result("pass", gating=False)
else:
    raise SystemExit(f"mode lạ: {mode}")

print(json.dumps(out))
