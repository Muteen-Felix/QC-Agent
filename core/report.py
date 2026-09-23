"""Render human-readable and machine-readable reports without changing the gate verdict."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.verdict import GateVerdict


@dataclass
class RunContext:
    run_id: str
    plan_id: str
    plan_name: str
    plan_path: str
    plan_text: str
    sut_id: str
    run_signature: str
    generated_at: str
    wallclock_s: float
    specs: dict
    results: dict
    gate: GateVerdict
    canary: list = field(default_factory=list)
    tickets_draft: list = field(default_factory=list)


def render(ctx: RunContext) -> tuple[str, dict]:
    gating = sorted(
        (result for result in ctx.results.values() if result["verdict"]["gating"]),
        key=lambda result: result["task_id"],
    )
    lines = [
        f"# QC Gate Report — run {ctx.run_id}",
        f"plan: {ctx.plan_path} ({ctx.plan_id}) · SUT: {ctx.sut_id} · {ctx.generated_at}",
        _cost_line(ctx),
        "",
    ]
    if ctx.gate.banner:
        lines.extend(["## ⚠ SKIPPED / ERROR — ĐỌC TRƯỚC", ""])
        lines.extend(f"- `{task_id}`: {message}" for task_id, message in ctx.gate.banner)
        lines.append("")
    broken_canaries = [item for item in ctx.canary if not item.get("ok")]
    if broken_canaries:
        lines.extend(item["message"] for item in broken_canaries)
        lines.append("")

    symbol = {"PASS": "✅", "YELLOW": "🟡", "FAIL": "❌"}.get(ctx.gate.value, "⚠")
    lines.extend([f"## VERDICT: {symbol} {ctx.gate.value}", ""])
    lines.extend(_deterministic_section(ctx, gating))
    lines.extend(_llm_section(ctx))
    lines.extend(_discovery_section(ctx))
    lines.extend(_skipped_section(ctx))
    lines.extend(_audit_section(ctx, gating))

    deterministic_view = [
        {
            "task_id": result["task_id"],
            "worker": result["worker"]["name"],
            "capability": ctx.specs[result["task_id"]]["capability"],
            "status": result["status"],
            "value": result["verdict"]["value"],
            "verdict_source": result["verdict"]["verdict_source"],
            "gating": result["verdict"]["gating"],
        }
        for result in gating
    ]
    report_json = {
        "run_id": ctx.run_id,
        "plan_id": ctx.plan_id,
        "run_signature": ctx.run_signature,
        "sut_id": ctx.sut_id,
        "gate_verdict": ctx.gate.value,
        "exit_code": ctx.gate.exit_code,
        "deterministic_view": deterministic_view,
        "banner": [list(item) for item in ctx.gate.banner],
        "canary": ctx.canary,
        "tickets_draft": ctx.tickets_draft,
        "details": {
            "generated_at": ctx.generated_at,
            "wallclock_s": ctx.wallclock_s,
            "results": {
                task_id: {
                    "status": result["status"],
                    "cost": result.get("cost", {}),
                    "metrics": result.get("metrics", {}),
                }
                for task_id, result in sorted(ctx.results.items())
            },
        },
    }
    return "\n".join(lines).rstrip() + "\n", report_json


def write(ctx: RunContext, run_dir) -> tuple[str, dict]:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    md, report_json = render(ctx)
    (run_dir / "report.md").write_text(md, encoding="utf-8", newline="\n")
    text = json.dumps(report_json, ensure_ascii=False, indent=2) + "\n"
    (run_dir / "report.json").write_text(text, encoding="utf-8", newline="\n")
    return md, report_json


def _cost_line(ctx: RunContext) -> str:
    token_tasks = []
    tokens = 0
    cost = 0.0
    for task_id, result in sorted(ctx.results.items()):
        item = result.get("cost", {})
        count = item.get("tokens")
        if isinstance(count, int) and count > 0:
            tokens += count
            token_tasks.append(task_id)
        usd = item.get("usd")
        if isinstance(usd, (int, float)):
            cost += usd
    minutes, seconds = divmod(int(round(ctx.wallclock_s)), 60)
    elapsed = f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"
    task_text = ", ".join(token_tasks) if token_tasks else "—"
    return f"wallclock {elapsed} · LLM tokens: {tokens} (tasks: {task_text}) · cost ${cost:.2f}"


def _deterministic_section(ctx: RunContext, gating: list) -> list[str]:
    lines = [
        "## 1. DETERMINISTIC ASSERT (chặn gate)",
        "",
        "| task | worker | capability | status | chi tiết |",
        "|---|---|---|---|---|",
    ]
    for result in gating:
        detail = _gate_detail(result)
        task_id = result["task_id"]
        lines.append(
            f"| {task_id} | {result['worker']['name']} | {ctx.specs[task_id]['capability']} "
            f"| {result['status']} | {detail} |"
        )
    if not gating:
        lines.append("| — | — | — | — | không có result gating |")
    values = " AND ".join(result["verdict"]["value"] for result in gating) or "∅"
    lines.extend(["", f"→ gate_verdict = {values} = **{ctx.gate.value}**", ""])
    return lines


def _gate_detail(result: dict) -> str:
    findings = result.get("findings", [])
    if result["status"] == "fail" and findings:
        return str(findings[0].get("title", "—")).replace("|", "\\|")
    pairs = list(sorted(result.get("metrics", {}).items()))[:2]
    return ", ".join(f"{key}={_number(value)}" for key, value in pairs) or "—"


def _llm_section(ctx: RunContext) -> list[str]:
    lines = [
        "## 2. LLM JUDGMENT (KHÔNG chặn gate — chỉ tham khảo)",
        "",
        "| task | metric | điểm | baseline | delta | confidence |",
        "|---|---|---:|---:|---:|---:|",
    ]
    baselines = _baselines()
    count = 0
    for task_id, result in sorted(ctx.results.items()):
        for finding in result.get("findings", []):
            if finding.get("verdict_source") != "llm_judgment":
                continue
            metric = str(finding.get("detected_by", "—")).removeprefix("metric:")
            score = result.get("metrics", {}).get(f"{metric}.score")
            baseline = baselines.get(metric)
            delta = score - baseline if isinstance(score, (int, float)) and isinstance(baseline, (int, float)) else None
            lines.append(
                f"| {task_id} | {metric} | {_number(score)} | {_number(baseline)} "
                f"| {_signed(delta)} | {_number(finding.get('confidence'))} |"
            )
            count += 1
    if count == 0:
        lines.append("| — | — | — | — | — | — |")
    lines.extend(["", "Các số này không cộng vào 'pass rate' và không chặn gate.", ""])
    return lines


def _discovery_section(ctx: RunContext) -> list[str]:
    lines = ["## 3. HEURISTIC / DISCOVERY (KHÔNG chặn gate)", ""]
    count = 0
    for task_id, result in sorted(ctx.results.items()):
        spec = ctx.specs.get(task_id, {})
        if result["verdict"]["gating"] or spec.get("lane") != "discovery":
            continue
        cost = result.get("cost", {})
        steps = result.get("metrics", {}).get("steps_executed", "—")
        lines.append(
            f"- `{task_id}` · {result['worker']['name']} · bước {steps} · "
            f"${_number(cost.get('usd'))} · token {_number(cost.get('tokens'))}"
        )
        for finding in result.get("findings", []):
            source = finding.get("verdict_source")
            if source == "deterministic_assert":
                label = "TẤT ĐỊNH"
            elif source == "llm_judgment":
                label = f"LLM, confidence {_number(finding.get('confidence'))}"
            else:
                label = "HEURISTIC"
            lines.append(f"  - [{finding.get('finding_id', '—')}] {finding.get('title', '—')}")
            lines.append(f"    detected_by: {finding.get('detected_by', '—')} ← {label}")
            promote = finding.get("promote_candidate")
            if promote:
                lines.append(f"    → ứng viên promote: {promote.get('suggested_capability', '—')}")
        count += 1
    for item in ctx.canary:
        if not item.get("ok"):
            continue
        lines.append(f"- {item['message']}")
        count += 1
    if count == 0:
        lines.append("- Không có result discovery.")
    lines.append("")
    return lines


def _skipped_section(ctx: RunContext) -> list[str]:
    lines = ["## 4. SKIPPED / ERROR", ""]
    if ctx.gate.banner:
        lines.extend(f"- `{task_id}`: {message}" for task_id, message in ctx.gate.banner)
    else:
        lines.append("- Không có.")
    lines.append("")
    return lines


def _audit_section(ctx: RunContext, gating: list) -> list[str]:
    lines = ["## 5. AUDIT", ""]
    for result in gating:
        task_id = result["task_id"]
        lines.append(f"### {task_id}")
        lines.append(f"- oracle: {ctx.plan_path}:L{_task_line(ctx.plan_text, task_id)}")
        evidence = result.get("evidence", [])
        if evidence:
            for item in evidence:
                lines.append(f"- evidence: {item['uri']} (sha256 {item['sha256'][:8]}…)")
        else:
            lines.append("- evidence: —")
        replay = result.get("determinism", {}).get("replay_cmd") or "—"
        lines.extend([f"- replay: {replay}", ""])
    if not gating:
        lines.extend(["- Không có task gating.", ""])
    return lines


def _task_line(plan_text: str, task_id: str) -> int:
    pattern = re.compile(rf"^\s*-?\s*task_id:\s*{re.escape(task_id)}\s*(?:#.*)?$")
    return next((number for number, line in enumerate(plan_text.splitlines(), 1) if pattern.match(line)), 0)


def _baselines() -> dict:
    path = Path(__file__).resolve().parents[1] / "baselines" / "geval.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _number(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _signed(value) -> str:
    return "—" if value is None else f"{value:+g}"
