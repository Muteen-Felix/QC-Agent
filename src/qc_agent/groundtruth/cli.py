"""`qc-agent gt generate | validate | regen` (S1-06): nối parse -> generate -> render, merge khi PRD đổi, và cổng HITL.

Ranh giới: `core/cli.py` chỉ import module này LÚC được gọi (import lười) nên luồng gate/manual không kéo LLM vào tiến trình.
Mọi thứ được tính TRONG BỘ NHỚ trước; chỉ khi generate/merge/render đều xong mới ghi file, nên lỗi LLM/egress/schema không để lại file GT ghi dở.
Exit code: 0 xong · 1 (chỉ `validate`) còn việc cho người · 3 lỗi input, LLM, egress, schema hoặc hệ thống. Thông điệp lỗi không chứa nội dung PRD/prompt/response.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from qc_agent import costing, settings
from qc_agent.groundtruth import auth as gt_auth
from qc_agent.groundtruth import check as gt_check
from qc_agent.groundtruth import coverage as gt_coverage
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.generate import GenerateResult, GTError, generate, usage_rows
from qc_agent.groundtruth.merge import MergeResult, merge
from qc_agent.groundtruth.prd import GTInputError, ParsedPRD, parse_prd
from qc_agent.llm.client import LLMError
from qc_agent.logging_setup import event
from qc_agent.scaffold import init as scaffold_init
from qc_agent.scaffold import openapi

SYSTEM_ERROR = 3
GENERATED_MARK = "# qc-agent:generated gt"
log = logging.getLogger("qc_agent.groundtruth")


class GTCliError(ValueError):
    """Lỗi input/cấu hình của lệnh `gt`. Thông điệp an toàn để in (không chứa nội dung PRD)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise GTCliError(f"tham số sai: {message}")


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="qc-agent gt", description="Ground-Truth: PRD -> test case (LLM) -> file trong repo SUT -> QA duyệt.")
    sub = ap.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def common(p, *, prd: bool):
        p.add_argument("--sut-root", metavar="DIR", help="thư mục gốc repo SUT (mặc định như `init`)")
        if prd:
            p.add_argument("--prd", required=True, metavar="FILE", help="PRD (.md, .txt hoặc OpenAPI .json/.yaml), tối đa 256 KB")
            p.add_argument("--openapi", metavar="FILE|URL", help="OpenAPI của SUT: danh sách endpoint cho LLM, kiểm endpoint từng TC, và suite api-contract")
            p.add_argument("--egress-dir", metavar="DIR", help="nơi ghi egress.jsonl (mặc định $QC_RUNS_DIR/gt); KHÔNG được nằm trong <sut>/.qc-agent")
            p.add_argument("--summary-json", metavar="FILE", help="ghi tóm tắt máy đọc được (số story/AC/TC, orphan, warning, prd_sha256, model, prompt_version, usage)")
            p.add_argument("--agent", action="store_true", help="bộ sinh AGENT: đọc PRD + mã nguồn + OpenAPI đầy đủ qua nhiều lượt (chỉ Claude; mã nguồn rời máy: egress `source_code`). "
                                                                 "Mặc định theo QC_GT_GENERATOR (single|agent)")
            p.add_argument("--no-agent", action="store_true", help="ép bộ sinh một lời gọi dù QC_GT_GENERATOR=agent")
            p.add_argument("--no-xlsx", action="store_true", help="không ghi test-cases.xlsx (mặc định có, trừ khi QC_GT_XLSX=false)")
            p.add_argument("--source-root", metavar="DIR", help="thư mục mã nguồn mà agent được ĐỌC (mặc định --sut-root; CI truyền bản origin/<base> mount :ro vì nhánh bot có thể cũ)")

    gen = sub.add_parser("generate", help="sinh catalog + test + suite từ PRD (lần đầu)")
    common(gen, prd=True)
    gen.add_argument("--force", action="store_true", help="ghi đè cả khi đã có test-cases.yaml (MẤT các TC QA đã duyệt; dùng `regen` để giữ chúng)")
    reg = sub.add_parser("regen", help="sinh lại khi PRD đổi và merge theo tc_id: giữ nguyên TC approved/rejected/qa, thay TC draft của LLM")
    common(reg, prd=True)
    info = sub.add_parser("info", help="in JSON định danh PRD (prd_id, sha256, số story/AC): offline, không LLM; workflow dùng để đặt tên nhánh")
    info.add_argument("--prd", required=True, metavar="FILE", help="PRD cần đọc")
    info.add_argument("--openapi", metavar="FILE|URL", help="OpenAPI kèm theo (tuỳ chọn)")
    val = sub.add_parser("validate", help="cổng HITL: exit 1 khi còn draft, drift, rejected thiếu lý do, module-map chưa duyệt, coverage thiếu, xlsx lệch YAML…")
    common(val, prd=False)
    imp = sub.add_parser("import-xlsx", help="ghi các sửa của QA trong test-cases.xlsx ngược vào test-cases.yaml (gộp ba chiều; exit 1 khi có xung đột/lỗi)")
    common(imp, prd=False)
    imp.add_argument("--xlsx", metavar="FILE", help="file xlsx cần nhập (mặc định .qc-agent/ground-truth/test-cases.xlsx)")
    imp.add_argument("--dry-run", action="store_true", help="chỉ báo sẽ đổi gì, không ghi")
    exp = sub.add_parser("export-xlsx", help="xuất test-cases.yaml ra test-cases.xlsx (từ chối nếu xlsx đang có sửa chưa import, trừ --force)")
    common(exp, prd=False)
    exp.add_argument("--force", action="store_true", help="ghi đè xlsx kể cả khi nó có sửa chưa import (MẤT các sửa đó)")
    return ap


# ---------------- dùng chung ----------------

def _root(args) -> Path:
    root = Path(args.sut_root) if args.sut_root else scaffold_init.default_sut_root()
    if not root.is_dir():
        raise GTCliError(f"--sut-root không phải thư mục: {root}")
    return root.resolve()


def _egress_dir(args, root: Path) -> Path:
    directory = Path(args.egress_dir) if args.egress_dir else settings.get().runs_dir / "gt"
    resolved = directory.resolve()
    locked = (root / ".qc-agent").resolve()
    if resolved == locked or locked in resolved.parents:
        raise GTCliError("--egress-dir không được nằm trong .qc-agent/ (file egress không được lọt vào PR sinh GT)")
    return directory


def _source(prd_path: Path, root: Path) -> str:
    """Chuỗi ghi vào `prd.source` của catalog: đường dẫn tương đối repo SUT nếu PRD nằm trong đó, không thì chỉ tên file (không lộ đường dẫn máy)."""
    try:
        return prd_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return prd_path.name


def _openapi(args) -> tuple[dict | None, object, dict | None]:
    """(spec, analysis, facts): `analysis` cho api-contract/module-map, `facts` là OpenAPI rút gọn cho bộ chấm coverage, `spec` đầy đủ cho agent. Tải spec MỘT lần (có thể là URL)."""
    if not args.openapi:
        return None, None, None
    try:
        spec = openapi.load(args.openapi)
        return spec, openapi.analyze(spec), gt_coverage.snapshot(spec)
    except openapi.OpenApiError as error:
        raise GTCliError(str(error)) from None


def _use_agent(args) -> bool:
    mode = settings.get().gt_generator
    if mode not in ("single", "agent"):
        raise GTCliError(f"QC_GT_GENERATOR phải là single hoặc agent, nhận {mode!r}")
    if args.agent and args.no_agent:
        raise GTCliError("--agent và --no-agent không đi cùng nhau")
    return args.agent or (mode == "agent" and not args.no_agent)


def _source_root(args, root: Path) -> Path:
    directory = Path(args.source_root) if args.source_root else root
    if not directory.is_dir():
        raise GTCliError(f"--source-root không phải thư mục: {directory}")
    return directory.resolve()


def _auth_prompt(root: Path) -> str | None:
    try:
        return gt_auth.prompt_block(gt_auth.load(root))
    except ValueError as error:
        raise GTCliError(str(error)) from None


def _write_usage(args, root: Path, calls) -> list[dict]:
    """Ghi `<egress-dir>/llm_usage.json` (mảng JSON, mỗi lời gọi đã gửi một dòng, không có nội dung; ngữ nghĩa `null` ở `qc_agent/costing.py`) cạnh egress.jsonl.
    Best-effort: lỗi ghi chỉ là cảnh báo, không đổi exit code. Không có lời gọi nào thì không tạo file. Trả các dòng để summary dùng."""
    rows = usage_rows(calls)
    if rows:
        try:
            target = _egress_dir(args, root) / "llm_usage.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        except (OSError, GTCliError) as error:
            print(f"CẢNH BÁO: không ghi được llm_usage.json ({type(error).__name__})", file=sys.stderr)
    return rows


def _produce(args, root: Path, existing: dict | None = None) -> tuple[ParsedPRD, GenerateResult, object, dict | None]:
    try:
        return _produce_inner(args, root, existing)
    except GTError as error:   # lời gọi đã tốn tiền (kể cả lần bị từ chối schema, hoặc timeout chưa rõ chi phí) vẫn phải nằm trong llm_usage.json trước khi exit 3
        _write_usage(args, root, error.calls)
        raise


def _produce_inner(args, root: Path, existing: dict | None = None) -> tuple[ParsedPRD, GenerateResult, object, dict | None]:
    prd_path = Path(args.prd)
    auth = _auth_prompt(root)
    prd = parse_prd(prd_path, openapi_source=args.openapi)
    spec, analysis, facts = _openapi(args)
    if not _use_agent(args):
        return prd, generate(prd, model=settings.get().gt_model, egress_dir=_egress_dir(args, root), source=_source(prd_path, root),
                                  auth=auth), analysis, facts
    event(log, "gt.cache", logging.INFO, skipped="agent")   # agent đọc mã nguồn qua nhiều lượt: cache theo PRD sẽ trả kết quả cũ khi code đổi (cache.py)
    from qc_agent.groundtruth import agent as gt_agent   # import lười: chỉ khi bật agent (kéo theo vòng lặp LLM nhiều lượt)
    result = gt_agent.generate_agent(prd, model=settings.get().gt_agent_model, egress_dir=_egress_dir(args, root), source_root=_source_root(args, root),
                                     openapi_spec=spec, facts=facts, existing=existing, source=_source(prd_path, root), auth=auth)
    if spec is None:
        result = dataclasses.replace(result, warnings=(*result.warnings, "agent chạy không có --openapi: không có tool openapi_*, và không chấm được technique/API"))
    return prd, result, analysis, facts


def _with_snapshot(files: list[gt_render.RenderedFile], facts: dict | None) -> list[gt_render.RenderedFile]:
    """Thêm `openapi.snapshot.json` (máy sở hữu) để `gt validate` chấm coverage offline. Không có --openapi thì không có snapshot và không đụng file cũ."""
    if facts is None:
        return files
    return sorted([*files, gt_render.RenderedFile(gt_coverage.SNAPSHOT_PATH, gt_coverage.snapshot_text(facts))], key=lambda f: f.path)


def _counts(catalog: dict) -> dict:
    by_status = {"draft": 0, "approved": 0, "rejected": 0}
    for tc in catalog["test_cases"]:
        by_status[tc["status"]] += 1
    return by_status


def _summary(command: str, prd: ParsedPRD, gen: GenerateResult, catalog: dict, orphans, warnings, outcomes, merged: MergeResult | None,
             facts: dict | None = None, usage_summary: dict | None = None) -> dict:
    usage = gen.usage
    # Lúc sinh còn toàn TC draft nên chấm cả draft + waiver draft: đây là "bộ này đã đủ chưa nếu QA duyệt hết", không phải kết quả của gate.
    coverage = gt_coverage.score(catalog, facts, tc_statuses=("draft", "approved"), waiver_statuses=("draft", "approved")).as_dict()
    out = {
        "command": command,
        "prd_id": prd.prd_id, "prd_sha256": prd.sha256, "prd_source": catalog["prd"]["source"], "prd_format": prd.format,
        "model": catalog["generated_by"]["model"], "prompt_version": catalog["generated_by"]["prompt_version"],
        "stories": len(catalog["stories"]), "acs": sum(len(s["acs"]) for s in catalog["stories"]),
        "test_cases": len(catalog["test_cases"]), "by_status": _counts(catalog), "catalog_status": catalog["status"],
        "uncovered_acs": [u["ac_id"] for u in catalog["uncovered_acs"]], "orphans": list(orphans),
        "dropped_test_cases": gen.dropped, "warnings": list(warnings), "coverage": coverage,
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                  "cache_creation_input_tokens": usage.cache_creation_input_tokens, "cache_read_input_tokens": usage.cache_read_input_tokens},
        "files": [{"path": o.label, "status": o.status} for o in outcomes],
        "generator": "agent" if getattr(gen, "agent", None) else "single",
        "cache_hit": bool(getattr(gen, "cache_hit", False)),
        # tổng chi phí ƯỚC TÍNH của các lời gọi đã biết giá (bỏ cache hit); null khi chẳng có lời gọi nào biết giá. `unknown_calls` > 0: số tiền là CẬN DƯỚI (xem costing.py)
        "est_usd": None if usage_summary is None or (usage_summary["priced_calls"] == 0 and usage_summary["cache_hits"] == 0) else usage_summary["est_usd"],
        "unknown_calls": 0 if usage_summary is None else usage_summary["unknown_calls"],
    }
    if getattr(gen, "agent", None):
        out["agent"] = gen.agent
    if merged is not None:
        out["merge"] = {"kept": merged.kept, "added": list(merged.added), "removed_drafts": list(merged.removed_drafts),
                        "lost_acs": {tc_id: list(refs) for tc_id, refs in sorted(merged.lost_acs.items())}}
    return out


def _print_summary(summary: dict) -> None:
    status = summary["by_status"]
    print(f"PRD {summary['prd_id']} (sha256 {summary['prd_sha256'][:12]}…) · model {summary['model']} · prompt {summary['prompt_version']}")
    print(f"{summary['stories']} story, {summary['acs']} AC, {summary['test_cases']} test case "
          f"(draft {status['draft']}, approved {status['approved']}, rejected {status['rejected']}); {summary['dropped_test_cases']} TC bị bỏ do vi phạm")
    if summary["uncovered_acs"]:
        print(f"AC không kiểm được bằng HTTP (uncovered_acs): {', '.join(summary['uncovered_acs'])}")
    if summary["orphans"]:
        print(f"CẢNH BÁO: AC mồ côi (không có TC, không nằm trong uncovered_acs): {', '.join(summary['orphans'])}")
    agent = summary.get("agent")
    if agent:
        cost = f", ~${agent['cost_usd_est']:.2f}" if agent.get("cost_usd_est") is not None else ""
        print(f"Bộ sinh: AGENT — {agent['turns']} lượt, đọc {agent['files_read']} file ({agent['bytes_read']} B), nộp {agent['submissions']} lần, "
              f"{agent['dropped_in_loop']} TC bị bỏ khi nộp{cost}; hoàn tất: {'có' if agent['completed'] else 'KHÔNG (xem coverage và cảnh báo)'}")
    for name, dim in summary["coverage"].items():
        print(f"coverage {name}: {dim['covered'] + dim['waived']}/{dim['total']} ({dim['ratio']:.0%})" + (f", thiếu {dim['gaps_total']}" if dim["gaps_total"] else ""))
    if "technique" not in summary["coverage"]:
        print("coverage technique/api: chưa chấm (thiếu --openapi nên không có mẫu số)")
    for warning in summary["warnings"]:
        print(f"CẢNH BÁO: {warning}")
    merged = summary.get("merge")
    if merged:
        print(f"Merge: giữ nguyên {merged['kept']} TC (approved/rejected/qa), thêm {len(merged['added'])} TC draft mới, thay {len(merged['removed_drafts'])} TC draft cũ")
        for tc_id, refs in merged["lost_acs"].items():
            print(f"CẢNH BÁO: {tc_id} trỏ tới AC không còn trong PRD ({', '.join(refs)}): không sửa TC, QA cần xử lý")
    for file in summary["files"]:
        print(f"{file['status']:<12} {file['path']}")
    usage = summary["usage"]
    if summary["cache_hit"]:
        print(f"cache: HIT, không gọi LLM (token {usage['input_tokens']}/{usage['output_tokens']} là của lần sinh gốc)")
    else:
        print(f"token: vào {usage['input_tokens']}, ra {usage['output_tokens']}")
    print("Tiếp theo: commit và mở PR; QA đổi draft -> approved (hoặc rejected kèm lý do), rồi `qc-agent gt validate`.")


def _write_summary(path: str | None, summary: dict) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _facts(root: Path, fresh: dict | None) -> dict | None:
    """OpenAPI rút gọn cho sheet Coverage: bản vừa tính nếu có, không thì snapshot đã commit (có thể không có)."""
    return fresh if fresh is not None else gt_coverage.load_snapshot(root)


def _write_xlsx(args, root: Path, catalog: dict, facts: dict | None) -> list[scaffold_init.Outcome]:
    """Ghi test-cases.xlsx (nếu bật). Lỗi xuất Excel (vd một ô quá lớn) chỉ là CẢNH BÁO: YAML mới là nguồn sự thật nên không làm hỏng cả lần sinh."""
    if getattr(args, "no_xlsx", False) or not settings.get().gt_xlsx:
        return []
    from qc_agent.groundtruth import xlsx as gt_xlsx   # import lười: openpyxl chỉ được nạp khi cần
    try:
        return [scaffold_init.Outcome(gt_xlsx.XLSX_PATH, gt_xlsx.write_if_changed(root, catalog, facts))]
    except gt_xlsx.XlsxError as error:
        print(f"CẢNH BÁO: không xuất được {gt_render.XLSX_PATH}: {error}", file=sys.stderr)
        return []


def _absorb_xlsx(old: dict, root: Path) -> tuple[dict, str | None]:
    """regen: nếu QA đã sửa xlsx mà chưa import, gộp các sửa đó vào `old` TRƯỚC khi merge để `regen` không ghi đè công việc chưa lưu. Có xung đột/lỗi thì dừng (exit 3)."""
    path = root / gt_render.XLSX_PATH
    if not path.is_file():
        return old, None
    from qc_agent.groundtruth import xlsx as gt_xlsx
    theirs, base, syntax = gt_xlsx.read_xlsx(path)
    state = gt_xlsx.sync_status(old, theirs, base)
    if state in ("in_sync", "yaml_ahead"):
        return old, None
    result = gt_xlsx.import_into(old, theirs, base, syntax)
    if not result.ok:
        raise GTCliError("xlsx có sửa chưa import mà không gộp được vào YAML (" + _import_problems(result) + "): chạy `qc-agent gt import-xlsx`, xử lý rồi regen lại")
    return result.catalog, f"đã gộp {len(result.changed)} sửa, {len(result.added)} TC mới từ {gt_render.XLSX_PATH} trước khi regen"


def _import_problems(result, limit: int = 8) -> str:
    items = [f"xung đột {c}" for c in result.conflicts] + [f"{where}: {message}" for where, message in result.errors]
    return "; ".join(items[:limit]) + (f"; … (+{len(items) - limit})" if len(items) > limit else "")


def _stale_generated(root: Path, rendered: list[gt_render.RenderedFile]) -> list[Path]:
    """File `test_*.py` do máy sinh trước đây mà lần render này không còn (story đã bị xoá khỏi PRD). Chỉ xoá file mang dấu `qc-agent:generated gt`."""
    keep = {(root / f.path).resolve() for f in rendered}
    directory = root / gt_render.TESTS_DIR
    stale = []
    for path in sorted(directory.glob("test_*.py")) if directory.is_dir() else []:
        if path.resolve() not in keep and path.read_text(encoding="utf-8", errors="replace").startswith(GENERATED_MARK):
            stale.append(path)
    return stale


# ---------------- generate / regen ----------------

def _generate(args, root: Path) -> int:
    catalog_path = root / gt_render.CATALOG_PATH
    if catalog_path.exists() and not args.force:
        raise GTCliError(f"{gt_render.CATALOG_PATH} đã có: dùng `qc-agent gt regen` để giữ các TC QA đã duyệt (hoặc --force để ghi đè và MẤT chúng)")
    prd, result, analysis, facts = _produce(args, root)
    usage_summary = costing.summarize(_write_usage(args, root, result.calls))
    files = _with_snapshot(gt_render.render(result.catalog, sut_root=root, openapi=analysis, force=args.force), facts)
    outcomes = gt_render.write(files, root, force=args.force)
    outcomes += _write_xlsx(args, root, result.catalog, facts)
    orphans = result.orphans
    summary = _summary("generate", prd, result, result.catalog, orphans, [*prd.warnings, *result.warnings], outcomes, None, facts, usage_summary)
    _write_summary(args.summary_json, summary)
    _print_summary(summary)
    event(log, "gt.cli", logging.INFO, command="generate", test_cases=len(result.catalog["test_cases"]), orphans=len(orphans))
    return 0


def _regen(args, root: Path) -> int:
    old = gt_check.load_catalog(root)
    problems = gt_schema.validate_catalog(old)
    if problems:
        raise gt_check.GTCheckError(f"{gt_render.CATALOG_PATH} sai schema: " + "; ".join(problems[:5]))
    old, absorbed = _absorb_xlsx(old, root)
    prd, result, analysis, facts = _produce(args, root, existing=old)
    usage_summary = costing.summarize(_write_usage(args, root, result.calls))
    if absorbed:
        result = dataclasses.replace(result, warnings=(absorbed, *result.warnings))
    merged = merge(old, result.catalog)
    files = _with_snapshot(gt_render.render(merged.catalog, sut_root=root, openapi=analysis), facts)
    stale = _stale_generated(root, files)
    owned = lambda f: f.path in (gt_render.CATALOG_PATH, gt_coverage.SNAPSHOT_PATH) or f.path.startswith(gt_render.TESTS_DIR + "/")   # noqa: E731 — máy sở hữu; module-map/suite thuộc về người sau khi tạo
    outcomes = gt_render.write([f for f in files if owned(f)], root, force=True) + gt_render.write([f for f in files if not owned(f)], root, force=False)
    for path in stale:
        path.unlink()
    outcomes += [scaffold_init.Outcome(path.relative_to(root).as_posix(), "removed") for path in stale]
    outcomes += _write_xlsx(args, root, merged.catalog, _facts(root, facts))
    summary = _summary("regen", prd, result, merged.catalog, merged.orphans, [*prd.warnings, *result.warnings], outcomes, merged, facts, usage_summary)
    _write_summary(args.summary_json, summary)
    _print_summary(summary)
    event(log, "gt.cli", logging.INFO, command="regen", kept=merged.kept, added=len(merged.added), removed=len(merged.removed_drafts), lost=len(merged.lost_acs))
    return 0


# ---------------- validate ----------------

def _validate(args, root: Path) -> int:
    result = gt_check.check(root)
    for where, message in result.errors:
        print(f"LỖI  {where}: {message}")
    for where, message in result.warnings:
        print(f"CẢNH BÁO  {where}: {message}")
    failed = bool(result.errors)
    print(f"{'FAIL' if failed else 'OK'}: {len(result.errors)} lỗi, {len(result.warnings)} cảnh báo")
    event(log, "gt.validate", logging.INFO, errors=len(result.errors), warnings=len(result.warnings))
    return 1 if failed else 0


# ---------------- import-xlsx / export-xlsx ----------------

def _load_checked(root: Path) -> dict:
    catalog = gt_check.load_catalog(root)
    problems = gt_schema.validate_catalog(catalog)
    if problems:
        raise gt_check.GTCheckError(f"{gt_render.CATALOG_PATH} sai schema: " + "; ".join(problems[:5]))
    return catalog


def _import_xlsx(args, root: Path) -> int:
    from qc_agent.groundtruth import xlsx as gt_xlsx
    catalog = _load_checked(root)
    path = Path(args.xlsx) if args.xlsx else root / gt_render.XLSX_PATH
    theirs, base, syntax = gt_xlsx.read_xlsx(path)
    result = gt_xlsx.import_into(catalog, theirs, base, syntax)
    for where, message in result.warnings:
        print(f"CẢNH BÁO  {where}: {message}")
    if not result.ok:
        for conflict in result.conflicts:
            print(f"XUNG ĐỘT  {conflict}: cả test-cases.yaml và xlsx cùng sửa khác nhau kể từ lần xuất; sửa một bên cho khớp rồi import lại")
        for where, message in result.errors:
            print(f"LỖI  {where}: {message}")
        print(f"KHÔNG ghi gì: {len(result.conflicts)} xung đột, {len(result.errors)} lỗi")
        return 1
    print(f"{len(result.changed)} TC có sửa ({len(result.converted)} TC của LLM chuyển thành origin: qa), {len(result.added)} TC mới, {len(result.removed)} TC bị xoá")
    for tc_id in result.added:
        print(f"  mới    {tc_id}")
    for tc_id in result.converted:
        print(f"  origin {tc_id} -> qa (sửa nội dung)")
    if args.dry_run:
        print("--dry-run: chưa ghi gì")
        return 0
    if result.catalog != catalog:
        files = [f for f in gt_render.render(result.catalog, sut_root=root) if f.path == gt_render.CATALOG_PATH or f.path.startswith(gt_render.TESTS_DIR + "/")]
        for outcome in gt_render.write(files, root, force=True):
            if outcome.status != "unchanged":
                print(f"{outcome.status:<12} {outcome.label}")
    for outcome in _write_xlsx(argparse.Namespace(no_xlsx=False), root, result.catalog, _facts(root, None)):   # xuất lại: ảnh chụp gốc mới, tc_id thật cho TC mới
        print(f"{outcome.status:<12} {outcome.label}")
    print("Tiếp theo: commit cả test-cases.yaml và test-cases.xlsx, rồi `qc-agent gt validate`.")
    event(log, "gt.cli", logging.INFO, command="import-xlsx", changed=len(result.changed), added=len(result.added), removed=len(result.removed), converted=len(result.converted))
    return 0


def _export_xlsx(args, root: Path) -> int:
    from qc_agent.groundtruth import xlsx as gt_xlsx
    catalog = _load_checked(root)
    path = root / gt_render.XLSX_PATH
    if path.is_file() and not args.force:
        try:
            theirs, base, _syntax = gt_xlsx.read_xlsx(path)
            state = gt_xlsx.sync_status(catalog, theirs, base)
        except gt_xlsx.XlsxError:
            state = "unreadable"
        if state in ("xlsx_ahead", "diverged"):
            print(f"LỖI: {gt_render.XLSX_PATH} đang có sửa chưa import (trạng thái {state}); chạy `qc-agent gt import-xlsx` trước, hoặc --force để bỏ các sửa đó", file=sys.stderr)
            return 1
    print(f"{gt_xlsx.write_if_changed(root, catalog, _facts(root, None)):<12} {gt_render.XLSX_PATH}")
    return 0


def _info(args) -> int:
    prd = parse_prd(Path(args.prd), openapi_source=args.openapi)
    print(json.dumps({"prd_id": prd.prd_id, "prd_sha256": prd.sha256, "format": prd.format, "stories": len(prd.stories),
                      "acs": sum(len(s.acs) for s in prd.stories), "endpoints": len(prd.endpoints), "warnings": list(prd.warnings)}, ensure_ascii=False, sort_keys=True))
    return 0


def _known_errors() -> tuple[type[BaseException], ...]:
    """Lỗi đã được viết để không chứa nội dung. `XlsxError` chỉ có khi module xlsx (openpyxl) đã được nạp, nên không nạp nó chỉ để bắt lỗi."""
    known = (GTCliError, GTInputError, GTError, LLMError, gt_check.GTCheckError, gt_render.GTRenderError, gt_coverage.CoverageError)
    module = sys.modules.get("qc_agent.groundtruth.xlsx")
    return (*known, module.XlsxError) if module is not None else known


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        if args.command == "info":
            return _info(args)
        root = _root(args)
        return {"generate": _generate, "regen": _regen, "validate": _validate, "import-xlsx": _import_xlsx, "export-xlsx": _export_xlsx}[args.command](args, root)
    except SystemExit as exit_:   # --help
        return exit_.code if isinstance(exit_.code, int) else 0
    except _known_errors() as error:
        print(f"LỖI: {error}", file=sys.stderr)   # tất cả đều đã được viết để không chứa nội dung PRD/prompt/response/khoá
    except OSError as error:
        print(f"LỖI: không ghi/đọc được file ({type(error).__name__}: {error.strerror or 'lỗi hệ thống'})", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 — chỉ in loại lỗi: str(error) của yaml/json có thể trích lại nội dung file
        print(f"LỖI NỘI BỘ: {type(error).__name__}", file=sys.stderr)
    return SYSTEM_ERROR
