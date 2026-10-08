"""Điểm vào `python -m tools.e2e <lệnh>`. Mọi lệnh chạy offline; lệnh ra ngoài chỉ được IN."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.e2e import budget, collect, intake, plan, preflight, sandbox, scenarios, stability, timing
from tools.e2e.common import ROOT, render_steps, say


def _intake(path: str | None) -> dict:
    return intake.load(Path(path)) if path else intake.template_data()


def _cmd_intake(args) -> int:
    if args.action == "template":
        if args.out:
            wrote = intake.write_template(Path(args.out), force=args.force)
            say(f"đã ghi {args.out}" if wrote else f"{args.out} đã có: không ghi đè (dùng --force)")
            return 0 if wrote else 1
        say(intake.TEMPLATE)
        return 0
    gaps = intake.validate(intake.load(Path(args.file)))
    for gap in gaps:
        say(f"{'CHẶN' if gap.blocker else 'lưu ý'}  {gap.key:28} [{gap.blocks}] {gap.reason}")
    say(f"{sum(g.blocker for g in gaps)} mục chặn, {sum(not g.blocker for g in gaps)} lưu ý. Intake đủ KHÔNG có nghĩa S4-06 đạt.")
    return 1 if any(g.blocker for g in gaps) else 0


def _cmd_preflight(args) -> int:
    data = _intake(args.intake)
    text = preflight.render(preflight.run(data, run_sut_tests=args.run_sut_tests))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    say(text)
    return 0


def _cmd_sandbox(args) -> int:
    data = _intake(args.intake)
    if args.plan:
        say(render_steps(sandbox.plan(data, args.apply_local or "<sandbox-dir>")))
        return 0
    report = sandbox.build_local(Path(args.apply_local), data) if args.apply_local else sandbox.dry_run(data)
    say(f"sandbox: {report['status']} · {report['files']} file" + (" (dry-run: thư mục tạm đã xoá)" if not args.apply_local else ""))
    for note in report["notes"]:
        say(f"· {note}")
    if report.get("generated"):
        say("sinh ra: " + ", ".join(report["generated"]))
        say(f"ground-truth có sẵn: {report['has_ground_truth']} (phải là False: A sinh ra nó)")
    say("Secret cần tạo (chỉ tên): " + ", ".join(f"{n} [{need}]" for n, _, need in sandbox.secrets(data)))
    say("Việc tiếp theo trên GitHub: `python -m tools.e2e sandbox --plan` (chỉ in).")
    return 0


def _cmd_prepare(args) -> int:
    branch, sha = scenarios.prepare(args.scenario, Path(args.repo), args.tag)
    say(f"nhánh cục bộ {branch} @ {sha[:10]} (chưa đẩy đi đâu)")
    return 0


def _cmd_commands(args) -> int:
    say(render_steps(plan.full_plan(_intake(args.intake), args.iterations)))
    return 0


def _cmd_budget(args) -> int:
    say(budget.render(budget.plan(args.iterations, with_other_channels=args.other_channels), cap=args.cap))
    return 0


def _cmd_ledger(args) -> int:
    result = budget.ledger(Path(args.dir), args.cap)
    say(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] == "CONTINUE" else 1


def _cmd_meta(args) -> int:
    say(json.dumps(collect.write_meta(Path(args.dir), repo=args.repo, source=args.source, image=args.image, qc_ref=args.qc_ref), ensure_ascii=False, indent=2))
    return 0


def _cmd_collect(args) -> int:
    data = _intake(args.intake) if args.intake else {}
    user_map = {**((data.get("jira") or {}).get("user_map") or {}), **dict(item.split("=", 1) for item in args.user_map)}
    report = collect.evaluate(Path(args.dir), args.scenario or None, project=args.project or (data.get("sut") or {}).get("project_slug") or "noteboard",
                              dev=args.dev or (data.get("github") or {}).get("dev_account"), user_map=user_map,
                              non_qa=args.non_qa or (data.get("github") or {}).get("non_qa_account"), workers=args.workers)
    text = report.render()
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    say(text)
    if args.record:
        say("ghi lượt " + str(args.iteration) + ": " + json.dumps(stability.record(Path(args.record), args.iteration, report, force=args.force), ensure_ascii=False))
    return 1 if report.counts()[collect.FAIL] else 0


def _cmd_stability(args) -> int:
    if args.action == "summary":
        result = stability.summarize(Path(args.file))
        say(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["verdict"].startswith("ĐỦ") else 1
    data = _intake(args.intake)
    gh, sut, jira, llm = data.get("github") or {}, data.get("sut") or {}, data.get("jira") or {}, data.get("llm") or {}
    cap = args.cap if args.cap is not None else llm.get("budget_usd_max")
    if not cap:
        say("LỖI: thiếu trần ngân sách (--cap hoặc intake.llm.budget_usd_max): không sinh script chạy LLM thật khi chưa có trần", )
        return 3
    text = stability.emit_script(repo=gh.get("sandbox_repo") or "<OWNER/REPO>", sandbox=args.sandbox, evidence=args.evidence, project=sut.get("project_slug") or "noteboard",
                                 jira_key=jira.get("project_key") or "<JIRA_KEY>", cap=float(cap), iterations=args.iterations, dev=gh.get("dev_account") or "<dev>")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        say(f"đã ghi {args.out} (chưa chạy). Chạy tay: bash {args.out}")
    else:
        say(text)
    return 0


def _cmd_timing(args) -> int:
    result = timing.compare(timing.collect([Path(p) for p in args.with_select], args.project), timing.collect([Path(p) for p in args.baseline], args.project))
    say(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.e2e", description="Công cụ S4-06 (offline; lệnh ra ngoài chỉ được in).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake"); p.add_argument("action", choices=("template", "check")); p.add_argument("file", nargs="?"); p.add_argument("--out"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=_cmd_intake)
    p = sub.add_parser("preflight"); p.add_argument("--intake"); p.add_argument("--run-sut-tests", action="store_true"); p.add_argument("--out"); p.set_defaults(fn=_cmd_preflight)
    p = sub.add_parser("sandbox"); p.add_argument("--intake"); p.add_argument("--apply-local", metavar="DIR"); p.add_argument("--plan", action="store_true"); p.set_defaults(fn=_cmd_sandbox)
    p = sub.add_parser("prepare"); p.add_argument("scenario", choices=("A", "C", "D")); p.add_argument("--repo", required=True); p.add_argument("--tag", required=True); p.set_defaults(fn=_cmd_prepare)
    p = sub.add_parser("commands"); p.add_argument("--intake"); p.add_argument("--iterations", type=int, default=10); p.set_defaults(fn=_cmd_commands)
    p = sub.add_parser("budget"); p.add_argument("--iterations", type=int, default=10); p.add_argument("--cap", type=float); p.add_argument("--other-channels", action="store_true")
    p.set_defaults(fn=_cmd_budget)
    p = sub.add_parser("ledger"); p.add_argument("dir"); p.add_argument("--cap", type=float, required=True); p.set_defaults(fn=_cmd_ledger)
    p = sub.add_parser("meta"); p.add_argument("dir"); p.add_argument("--repo", required=True); p.add_argument("--source", required=True, choices=("github", "local", "fake"))
    p.add_argument("--image"); p.add_argument("--qc-ref"); p.set_defaults(fn=_cmd_meta)
    p = sub.add_parser("collect"); p.add_argument("dir"); p.add_argument("--scenario", action="append", choices=tuple(scenarios.SCENARIOS)); p.add_argument("--intake"); p.add_argument("--project")
    p.add_argument("--dev"); p.add_argument("--non-qa"); p.add_argument("--workers", default="semgrep"); p.add_argument("--user-map", action="append", default=[], metavar="LOGIN=ACCOUNT_ID")
    p.add_argument("--out"); p.add_argument("--record", metavar="RESULTS.json"); p.add_argument("--iteration", type=int, default=0); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=_cmd_collect)
    p = sub.add_parser("stability"); p.add_argument("action", choices=("script", "summary")); p.add_argument("file", nargs="?"); p.add_argument("--intake"); p.add_argument("--out")
    p.add_argument("--iterations", type=int, default=10); p.add_argument("--cap", type=float); p.add_argument("--sandbox", default="<sandbox-dir>"); p.add_argument("--evidence", default="runs/e2e/evidence")
    p.set_defaults(fn=_cmd_stability)
    p = sub.add_parser("timing"); p.add_argument("--project", default="noteboard"); p.add_argument("--with-select", nargs="+", required=True); p.add_argument("--baseline", nargs="+", required=True)
    p.set_defaults(fn=_cmd_timing)
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.cmd == "intake" and args.action == "check" and not args.file:
        print("LỖI: intake check cần FILE", file=sys.stderr)
        return 3
    if args.cmd == "stability" and args.action == "summary" and not args.file:
        print("LỖI: stability summary cần FILE kết quả", file=sys.stderr)
        return 3
    if args.cmd == "collect" and args.record and args.iteration < 1:
        print("LỖI: --record cần --iteration >= 1", file=sys.stderr)
        return 3
    try:
        return args.fn(args)
    except (ValueError, RuntimeError, FileExistsError, OSError, KeyError) as error:
        print(f"LỖI: {type(error).__name__}: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
