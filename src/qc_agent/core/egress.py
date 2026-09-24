"""Hook `data_egress` (quyết định D8): ghi lại DỮ LIỆU NÀO rời máy theo từng lần worker được gọi, KHÔNG chặn (mặc định).

Trung thực về giới hạn: core không quan sát mạng. Bản ghi dựa trên cái worker KHAI TRƯỚC trong manifest (`data_egress`) và đích là
host của `target.base_url` => `basis: "declared"`. Đích thứ hai (nhà cung cấp LLM/judge) do worker tự chọn nên KHÔNG có ở đây.
Một worker nói dối manifest sẽ không bị hook này phát hiện.

Chừa chỗ cho mask/deny: thay `LogOnlyPolicy` bằng lớp con của `EgressPolicy` (runner nhận `egress_policy`). Runner thực thi được `deny`
(task không chạy, thành `skipped` nên tuân theo `on_skipped_gate_task`); `mask` CHƯA có cách thực thi nên bị coi là lỗi cấu hình
(task `error`), không bao giờ im lặng cho dữ liệu đi qua.
Bản ghi KHÔNG chứa nội dung, đường dẫn, query hay thông tin đăng nhập của URL: chỉ host:port.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

LOG_NAME = "egress.jsonl"
ACTIONS = ("allow", "deny", "mask")


@dataclass(frozen=True)
class Decision:
    action: str = "allow"
    reason: str = ""

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"egress action phải thuộc {ACTIONS}, nhận {self.action!r}")


class EgressPolicy:
    """Điểm mở rộng: nhận bản ghi (dict, chỉ đọc) và trả Decision."""

    def decide(self, event: dict) -> Decision:
        raise NotImplementedError


class LogOnlyPolicy(EgressPolicy):
    def decide(self, event: dict) -> Decision:
        return Decision("allow", "log-only")


def _host(spec: dict) -> str | None:
    base_url = (spec.get("target") or {}).get("base_url")
    try:
        parsed = urlsplit(base_url) if isinstance(base_url, str) else None
        return parsed.netloc.rpartition("@")[2] or None if parsed else None  # bỏ user:pass@
    except ValueError:
        return None


def record(policy: EgressPolicy, run_dir: Path, spec: dict, worker, attempt: int) -> Decision:
    """Ghi một dòng vào run_dir/egress.jsonl rồi trả quyết định của policy. Lỗi ghi log không được làm hỏng run (chỉ mất bản ghi)."""
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": spec.get("run_id"),
        "task_id": spec.get("task_id"),
        "attempt": attempt,
        "worker": worker.name,
        "capability": spec.get("capability"),
        "categories": sorted(getattr(worker, "data_egress", None) or []),  # thiếu khai báo = không khai gì
        "target_host": _host(spec),
        "basis": "declared",
    }
    decision = policy.decide(dict(event))
    event["decision"] = {"action": decision.action, "reason": decision.reason}
    try:
        with (Path(run_dir) / LOG_NAME).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return decision
