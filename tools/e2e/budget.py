"""Số lượt gọi LLM, dữ liệu gửi đi và chi phí ước tính của S4-06, TÍNH OFFLINE từ cấu hình và bảng giá có sẵn (`llm/prices.py`, `eval/selector-real.json`).

Đây là ước tính để xin duyệt ngân sách, KHÔNG phải số đo và KHÔNG phải hoá đơn. Mọi giả định nằm trong `ASSUMPTIONS` và được in kèm. Không có lời gọi mạng nào.

  python -m tools.e2e budget [--iterations 10] [--cap USD]      bảng lượt gọi + chi phí + trần đề xuất
  python -m tools.e2e ledger DIR --cap USD                      cộng llm_usage.json đã thu; exit 1 = DỪNG
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from qc_agent import settings
from qc_agent.groundtruth import generate as gt_generate
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.llm import prices
from qc_agent.llm.client import Usage, estimate_input_tokens

from tools.e2e.common import ROOT

REAL_SELECT = ROOT / "eval" / "selector-real.json"
GT_FIXTURE_RESPONSE = ROOT / "tests" / "fixtures" / "llm" / "gt_noteboard_response.json"
FIXTURE_PRD = ROOT / "tests" / "fixtures" / "prd" / "noteboard-prd.md"
STOP_FRACTION = 0.8   # chạm 80% trần thì dừng và hỏi lại, không chạy tiếp cho tới khi được duyệt thêm

ASSUMPTIONS = (
    "Token mỗi lời gọi Select lấy từ eval/selector-real.json (đo THẬT bằng Haiku trên golden set noteboard ở S2, trước khi S4-03/S4-04 đổi prompt và pruner): có thể lệch; ước tính lại sau khi có số đo mới.",
    "Lời gọi sinh GT: đầu vào = ceil(byte/3) của system prompt + PRD + schema (cùng quy ước client.estimate_input_tokens, KHÔNG phải count_tokens); đầu ra tỉ lệ theo kích thước PRD so với PRD noteboard, chặn trên ở MAX_TOKENS.",
    "Mỗi PR mở trên nhánh mới KHÔNG thấy cache Select của lần chạy trước (Actions cache tách theo ref), nên 10 lượt C+D được tính là 20 lời gọi Select, không có cache hit.",
    "Chạy lại cùng commit trên cùng PR dự kiến cache hit (0 lời gọi); mức trần vẫn tính 1 lời gọi nếu cache hụt.",
    "Claude không tự retry; Select tối đa 1 lời gọi/PR. Sinh GT tối đa 2 lời gọi (một lần sửa theo lỗi schema).",
    "count_tokens: [EXTERNAL GAP] ghi $0 theo hiểu biết ngoài khoá học là endpoint không tính phí; CHƯA kiểm chứng, và vẫn gửi toàn bộ diff ra ngoài.",
    "Giá: llm/prices.py (cached " + prices.PRICES_DATE + "), không gọi API để lấy giá. Model lạ (mọi gemini-*) không có giá => không ước tính.",
)


@dataclass(frozen=True)
class Row:
    phase: str
    purpose: str
    model: str | None
    calls: tuple[int, int, int]        # min, dự kiến, tối đa
    in_tok: tuple[int, int]            # mỗi lời gọi: dự kiến, tối đa
    out_tok: tuple[int, int]
    data: str
    command: str
    note: str = ""

    def usd(self, which: int) -> float | None:
        """which 1 = dự kiến, 2 = tối đa. None = không ước tính được (model không có giá)."""
        if self.model is None:
            return 0.0
        count = self.calls[which]
        tokens = Usage(input_tokens=self.in_tok[which - 1] * count, output_tokens=self.out_tok[which - 1] * count)
        return prices.estimate_cost(self.model, tokens)


def select_reference(path: Path = REAL_SELECT) -> dict:
    """Token mỗi lời gọi Select đã đo thật (các lần chạy có LLM, không cache)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    ins, outs = [], []
    for item in doc.get("diffs", []):
        for run in item.get("runs", []):
            if run.get("source") == "llm" and run.get("input_tokens"):
                ins.append(run["input_tokens"])
                outs.append(run.get("output_tokens") or 0)
    if not ins:
        raise ValueError(f"{path.name}: không có lần chạy LLM nào để lấy token tham chiếu")
    p90 = lambda xs: sorted(xs)[max(0, math.ceil(0.9 * len(xs)) - 1)]   # noqa: E731
    return {"calls": len(ins), "in_median": int(statistics.median(ins)), "in_p90": p90(ins), "out_median": int(statistics.median(outs)), "out_p90": p90(outs),
            "model": (doc.get("model") or [None])[0], "usage": doc.get("usage"), "runs": doc.get("runs")}


def gt_reference(prd: Path = FIXTURE_PRD) -> dict:
    """Ước tính một lời gọi sinh GT cho `prd`."""
    _, system = gt_generate.load_prompt()
    text = prd.read_text(encoding="utf-8")
    in_tokens = estimate_input_tokens(system, text, gt_schema.emit_schema())
    fixture_out = _fixture_output_tokens()
    scale = max(prd.stat().st_size, 1) / FIXTURE_PRD.stat().st_size
    out_tokens = min(gt_generate.MAX_TOKENS, max(1, int(fixture_out * scale)))
    return {"in": in_tokens, "out": out_tokens, "out_cap": gt_generate.MAX_TOKENS, "prd_bytes": prd.stat().st_size}


def _fixture_output_tokens() -> int:
    body = json.loads(GT_FIXTURE_RESPONSE.read_text(encoding="utf-8"))
    for block in body.get("content", []) if isinstance(body, dict) else []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return math.ceil(len(json.dumps(block["input"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) / 3)
    return gt_generate.MAX_TOKENS   # không đọc được fixture: lấy cận trên, không đoán thấp


def dataset_case_counts() -> dict[str, int]:
    from tools import selector_datasets
    return {name: len(selector_datasets.load(name).cases(split="tune", include_injections=False)) for name in selector_datasets.names()}


def plan(iterations: int = 10, *, prd: Path = FIXTURE_PRD, selector_model: str | None = None, gt_model: str | None = None, with_other_channels: bool = False) -> list[Row]:
    cfg = settings.get()
    selector_model, gt_model = selector_model or cfg.selector_model, gt_model or cfg.gt_model
    sel, gt = select_reference(), gt_reference(prd)
    counts = dataset_case_counts()
    default_ds = counts.get("noteboard", 0)
    select_in, select_out = (sel["in_median"], sel["in_p90"]), (sel["out_median"], sel["out_p90"])
    rows = [
        Row("A", "gt-generate", gt_model, (1, 1, 2), (gt["in"], gt["in"]), (gt["out"], gt["out_cap"]),
            f"PRD ({gt['prd_bytes']} B) + system prompt + schema; không có mã nguồn (chỉ có khi bật agent)",
            "workflow qc-groundtruth (job generate) trên GitHub", "tối đa 2 = một lần sửa theo lỗi schema; chạy lại cùng PRD: cache theo prd_sha256 (0 lời gọi)"),
        Row("C+D (1 lượt A–E)", "diff-select", selector_model, (0, 2, 2), select_in, select_out,
            "diff đã prune của 2 PR (C, D) trong khung <untrusted_diff> + module-map + catalog worker", "2 PR trên sandbox", "0 nếu Select rơi vào FULL SET/cache; E (workflow_dispatch) và B không gọi LLM"),
        Row("D chạy lại", "diff-select", selector_model, (0, 0, 1), select_in, select_out,
            "như trên", "re-run job của PR D", "dự kiến cache hit = 0 lời gọi (đây chính là điều DoD cần chứng minh)"),
        Row(f"{iterations}× (C+D)", "diff-select", selector_model, (0, 2 * iterations, 2 * iterations), select_in, select_out,
            "như trên, mỗi lượt 2 PR trên nhánh mới", "python -m tools.e2e stability plan", "nhánh mới => không thấy cache Select của lượt trước (giả định)"),
        Row("đo baseline thời gian", "—", None, (0, 0, 0), (0, 0), (0, 0), "không gửi gì", "workflow_dispatch không workers (FULL SET, không Select)", "0 lời gọi LLM"),
        Row("đo token (count_tokens)", "count_tokens", None, (0, 4 * default_ds + 1, 4 * default_ds + 1), (0, 0), (0, 0),
            "toàn bộ request Select (kể cả diff thô) của golden set noteboard", "python tools/eval_cost.py --llm-tokens count --yes", "giá $0 là giả định [EXTERNAL GAP]; số lời gọi = 4/ca + 1 tiền tố"),
        Row("đo recall (eval_selector)", "diff-select", selector_model, (0, int(sel["calls"]), int(sel["calls"])), select_in, select_out,
            "diff của golden set noteboard (fixture của repo, không phải dữ liệu SUT thật)", "python tools/eval_selector.py --llm real --runs 3 --yes",
            f"tham chiếu: lần đo thật trước dùng {sel['usage']}; công cụ tự dừng ở --max-usd (mặc định 0.6)"),
    ]
    if with_other_channels:
        rows.append(Row("kênh LLM khác Anthropic", "geval/midscene", None, (0, 0, 0), (0, 0), (0, 0), "đầu vào/đầu ra của 5 ca golden (G-Eval); ảnh chụp UI (Midscene)",
                        "tạo secret OPENAI_API_KEY/GEMINI_API_KEY/MIDSCENE_*", "KHÔNG ước tính được: không có giá trong llm/prices.py; chỉ phát sinh nếu bạn tạo các secret này"))
    return rows


def totals(rows: list[Row]) -> dict:
    out = {"min_calls": sum(r.calls[0] for r in rows), "exp_calls": sum(r.calls[1] for r in rows), "max_calls": sum(r.calls[2] for r in rows), "exp_usd": 0.0, "max_usd": 0.0, "unpriced": []}
    for row in rows:
        for key, which in (("exp_usd", 1), ("max_usd", 2)):
            value = row.usd(which)
            if value is None:
                out["unpriced"].append(row.phase)
            else:
                out[key] += value
    out["unpriced"] = sorted(set(out["unpriced"]))
    return out


def recommended_cap(rows: list[Row]) -> float:
    """Trần đề xuất = 1,5 × trường hợp xấu nhất, làm tròn lên $0,5, tối thiểu $1: đủ dư cho một lần chạy lại mà vẫn là con số nhỏ để duyệt."""
    worst = totals(rows)["max_usd"]
    return max(1.0, math.ceil(worst * 1.5 / 0.5) * 0.5)


def render(rows: list[Row], *, cap: float | None = None) -> str:
    money = lambda v: "không có giá" if v is None else f"${v:.4f}"   # noqa: E731
    lines = ["# Ngân sách LLM S4-06 (ƯỚC TÍNH OFFLINE, không gọi API)", "",
             "| Phần | Mục đích | Model | Lượt gọi (min/dự kiến/max) | Dữ liệu gửi đi | USD dự kiến | USD tối đa |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row.phase} | {row.purpose} | {row.model or '—'} | {row.calls[0]}/{row.calls[1]}/{row.calls[2]} | {row.data} | {money(row.usd(1))} | {money(row.usd(2))} |")
    t = totals(rows)
    lines += ["", f"Tổng: {t['min_calls']}/{t['exp_calls']}/{t['max_calls']} lượt · dự kiến ${t['exp_usd']:.4f} · tối đa ${t['max_usd']:.4f}"
              + (f" (chưa gồm giá của: {', '.join(t['unpriced'])})" if t["unpriced"] else "")]
    suggested = recommended_cap(rows)
    chosen = cap if cap is not None else suggested
    lines += [f"Trần đề xuất: ${suggested:.2f} (1,5 × tối đa, làm tròn lên $0,5; tối thiểu $1)." + (f" Trần đã chọn: ${chosen:.2f}." if cap is not None else ""),
              f"Điều kiện dừng: cộng `llm_usage.json` đã thu >= {int(STOP_FRACTION * 100)}% trần (${chosen * STOP_FRACTION:.2f}) thì DỪNG và hỏi lại; có lời gọi chưa rõ chi phí thì DỪNG; "
              "lời gọi vượt số tối đa của phần đang chạy thì DỪNG; chưa có xác nhận câu hỏi #3 thì KHÔNG bắt đầu.", "", "Giả định:"]
    lines += [f"- {text}" for text in ASSUMPTIONS]
    return "\n".join(lines) + "\n"


def ledger(directory: Path, cap: float) -> dict:
    """Cộng mọi `llm_usage.json` dưới `directory` (mảng JSON do core/report.py và gt generate ghi). Dòng cache_hit không tính tiền."""
    spent, calls, hits, unknown, bad, files = 0.0, 0, 0, 0, 0, 0
    for path in sorted(Path(directory).rglob("llm_usage.json")):
        files += 1
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("cache_hit"):
                hits += 1
                continue
            calls += 1
            unknown += int(item.get("unknown_calls", 0) or 0) + (1 if item.get("usage_known") is False else 0)
            bad += 1 if item.get("status", "ok") != "ok" else 0
            if isinstance(item.get("est_usd"), (int, float)):
                spent += item["est_usd"]
    reasons = []
    if spent >= cap:
        reasons.append(f"đã chi ${spent:.4f} >= trần ${cap:.2f}")
    elif spent >= cap * STOP_FRACTION:
        reasons.append(f"đã chi ${spent:.4f} >= {int(STOP_FRACTION * 100)}% trần ${cap:.2f}")
    if unknown:
        reasons.append(f"{unknown} lời gọi chưa rõ chi phí: không thể bảo đảm trần")
    return {"files": files, "calls": calls, "cache_hits": hits, "unknown_calls": unknown, "non_ok_calls": bad, "spent_usd": round(spent, 6), "cap_usd": cap,
            "decision": "STOP" if reasons else "CONTINUE", "reasons": reasons}
