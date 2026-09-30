"""`qc-agent gt generate | validate | regen` (S1-06): nối parse -> generate -> render, merge khi PRD đổi, và cổng HITL.

Ranh giới: `core/cli.py` chỉ import module này LÚC được gọi (import lười) nên luồng gate/manual không kéo LLM vào tiến trình.
Mọi thứ được tính TRONG BỘ NHỚ trước; chỉ khi generate/merge/render đều xong mới ghi file, nên lỗi LLM/egress/schema không để lại file GT ghi dở.
Exit code: 0 xong · 1 (chỉ `validate`) còn việc cho người · 3 lỗi input, LLM, egress, schema hoặc hệ thống. Thông điệp lỗi không chứa nội dung PRD/prompt/response.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from qc_agent import settings
from qc_agent.groundtruth import check as gt_check
from qc_agent.groundtruth import render as gt_render
from qc_agent.groundtruth import schema as gt_schema
from qc_agent.groundtruth.generate import GenerateResult, GTError, generate
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

    gen = sub.add_parser("generate", help="sinh catalog + test + suite từ PRD (lần đầu)")
    common(gen, prd=True)
    gen.add_argument("--force", action="store_true", help="ghi đè cả khi đã có test-cases.yaml (MẤT các TC QA đã duyệt; dùng `regen` để giữ chúng)")
    reg = sub.add_parser("regen", help="sinh lại khi PRD đổi và merge theo tc_id: giữ nguyên TC approved/rejected/qa, thay TC draft của LLM")
    common(reg, prd=True)
    info = sub.add_parser("info", help="in JSON định danh PRD (prd_id, sha256, số story/AC): offline, không LLM; workflow dùng để đặt tên nhánh")
    info.add_argument("--prd", required=True, metavar="FILE", help="PRD cần đọc")
    info.add_argument("--openapi", metavar="FILE|URL", help="OpenAPI kèm theo (tuỳ chọn)")
    val = sub.add_parser("validate", help="cổng HITL: exit 1 khi còn draft, drift, rejected thiếu lý do, module-map chưa duyệt…")
    common(val, prd=False)
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


def _analysis(args):
    if not args.openapi:
        return None
    try:
        return openapi.analyze(openapi.load(args.openapi))
    except openapi.OpenApiError as error:
        raise GTCliError(str(error)) from None


def _produce(args, root: Path) -> tuple[ParsedPRD, GenerateResult, object]:
    prd_path = Path(args.prd)
    prd = parse_prd(prd_path, openapi_source=args.openapi)
    analysis = _analysis(args)
    result = generate(prd, model=settings.get().gt_model, egress_dir=_egress_dir(args, root), source=_source(prd_path, root))
    return prd, result, analysis


def _counts(catalog: dict) -> dict:
    by_status = {"draft": 0, "approved": 0, "rejected": 0}
    for tc in catalog["test_cases"]:
        by_status[tc["status"]] += 1
    return by_status


def _summary(command: str, prd: ParsedPRD, gen: GenerateResult, catalog: dict, orphans, warnings, outcomes, merged: MergeResult | None) -> dict:
    usage = gen.usage
    out = {
        "command": command,
        "prd_id": prd.prd_id, "prd_sha256": prd.sha256, "prd_source": catalog["prd"]["source"], "prd_format": prd.format,
        "model": catalog["generated_by"]["model"], "prompt_version": catalog["generated_by"]["prompt_version"],
        "stories": len(catalog["stories"]), "acs": sum(len(s["acs"]) for s in catalog["stories"]),
        "test_cases": len(catalog["test_cases"]), "by_status": _counts(catalog), "catalog_status": catalog["status"],
        "uncovered_acs": [u["ac_id"] for u in catalog["uncovered_acs"]], "orphans": list(orphans),
        "dropped_test_cases": gen.dropped, "warnings": list(warnings),
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                  "cache_creation_input_tokens": usage.cache_creation_input_tokens, "cache_read_input_tokens": usage.cache_read_input_tokens},
        "files": [{"path": o.label, "status": o.status} for o in outcomes],
    }
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
    print(f"token: vào {usage['input_tokens']}, ra {usage['output_tokens']}")
    print("Tiếp theo: commit và mở PR; QA đổi draft -> approved (hoặc rejected kèm lý do), rồi `qc-agent gt validate`.")


def _write_summary(path: str | None, summary: dict) -> None:
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


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
    prd, result, analysis = _produce(args, root)
    files = gt_render.render(result.catalog, sut_root=root, openapi=analysis, force=args.force)
    outcomes = gt_render.write(files, root, force=args.force)
    orphans = result.orphans
    summary = _summary("generate", prd, result, result.catalog, orphans, [*prd.warnings, *result.warnings], outcomes, None)
    _write_summary(args.summary_json, summary)
    _print_summary(summary)
    event(log, "gt.cli", logging.INFO, command="generate", test_cases=len(result.catalog["test_cases"]), orphans=len(orphans))
    return 0


def _regen(args, root: Path) -> int:
    old = gt_check.load_catalog(root)
    problems = gt_schema.validate_catalog(old)
    if problems:
        raise gt_check.GTCheckError(f"{gt_render.CATALOG_PATH} sai schema: " + "; ".join(problems[:5]))
    prd, result, analysis = _produce(args, root)
    merged = merge(old, result.catalog)
    files = gt_render.render(merged.catalog, sut_root=root, openapi=analysis)
    stale = _stale_generated(root, files)
    owned = lambda f: f.path == gt_render.CATALOG_PATH or f.path.startswith(gt_render.TESTS_DIR + "/")   # noqa: E731 — máy sở hữu; module-map/suite thuộc về người sau khi tạo
    outcomes = gt_render.write([f for f in files if owned(f)], root, force=True) + gt_render.write([f for f in files if not owned(f)], root, force=False)
    for path in stale:
        path.unlink()
    outcomes += [scaffold_init.Outcome(path.relative_to(root).as_posix(), "removed") for path in stale]
    summary = _summary("regen", prd, result, merged.catalog, merged.orphans, [*prd.warnings, *result.warnings], outcomes, merged)
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


def _info(args) -> int:
    prd = parse_prd(Path(args.prd), openapi_source=args.openapi)
    print(json.dumps({"prd_id": prd.prd_id, "prd_sha256": prd.sha256, "format": prd.format, "stories": len(prd.stories),
                      "acs": sum(len(s.acs) for s in prd.stories), "endpoints": len(prd.endpoints), "warnings": list(prd.warnings)}, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        if args.command == "info":
            return _info(args)
        root = _root(args)
        return {"generate": _generate, "regen": _regen, "validate": _validate}[args.command](args, root)
    except SystemExit as exit_:   # --help
        return exit_.code if isinstance(exit_.code, int) else 0
    except (GTCliError, GTInputError, GTError, LLMError, gt_check.GTCheckError, gt_render.GTRenderError) as error:
        print(f"LỖI: {error}", file=sys.stderr)   # tất cả đều đã được viết để không chứa nội dung PRD/prompt/response/khoá
    except OSError as error:
        print(f"LỖI: không ghi/đọc được file ({type(error).__name__}: {error.strerror or 'lỗi hệ thống'})", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 — chỉ in loại lỗi: str(error) của yaml/json có thể trích lại nội dung file
        print(f"LỖI NỘI BỘ: {type(error).__name__}", file=sys.stderr)
    return SYSTEM_ERROR
