"""Đo Ground-Truth trên MỘT SUT thật (S1-09): cấu hình bằng YAML, không dính toyapp/noteboard.

    python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --out-json eval/out.json
    python tools/eval_gt_sut.py --config eval/my-sut.yaml --skip-generate          # chỉ đo bộ ĐÃ DUYỆT (không gọi LLM, không tốn quota)

Ba metric (dùng lại phần tính của tools/eval_groundtruth.py):
  (a) AC coverage   AC `testable` có >= 1 TC / tổng AC `testable` (PRD trừ `non_testable` do QA gán). Mỗi lượt sinh; --runs N lấy median.
  (b) TC xanh       TC vừa sinh (ép `approved` trong thư mục tạm) chạy pass trên SUT SẠCH / tổng TC.
  (c) Bắt lỗi       bộ ĐÃ DUYỆT (`<sut_root>/.qc-agent/ground-truth`) FAIL khi SUT bị chèn lỗi (mutant). Mutant khai trong YAML bằng một trong ba cách:
                    `edits` (thay đúng một đoạn mã), `patch` (diff), `env` (cờ lỗi có sẵn của SUT), hoặc `base_url` (bạn tự chạy bản lỗi).
                    `edits`/`patch` chỉ áp lên BẢN SAO của sut_root; repo thật không bao giờ bị sửa.
Exit: 0 đạt mọi ngưỡng đã đo · 1 không đạt · 3 sai cấu hình/lỗi hệ thống. Thông điệp không chứa nội dung PRD/response/khoá.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import eval_groundtruth as base  # noqa: E402  (dùng lại: ac_coverage, green_rate, mutant_result, parse_junit, run_suite, median, estimate_cost)

EvalError = base.EvalError


class NoApprovedSuite(EvalError):
    """Chưa có bộ đã duyệt để đo (c): lỗi khi chỉ đo bộ đã duyệt, chỉ là ghi chú khi vừa sinh vừa đo."""
GT_DIR = base.GT_DIR
DEFAULT_THRESHOLDS = {"ac_coverage": 0.9, "green_rate": 0.9, "mutant_kill_rate": 0.9}
DEFAULT_COPY_IGNORE = [".git", "__pycache__", ".pytest_cache", ".mypy_cache"]
_MUTANT_KINDS = ("edits", "patch", "env", "base_url")


# ---------------- cấu hình ----------------

def load_config(path: Path) -> dict:
    path = Path(path)
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise EvalError(f"không đọc được cấu hình {path.name}") from None
    if not isinstance(cfg, dict):
        raise EvalError("cấu hình phải là một object YAML")
    here = path.resolve().parent
    problems = validate_config(cfg)
    if problems:
        raise EvalError("cấu hình sai: " + "; ".join(problems))
    cfg = dict(cfg)
    cfg["sut_root"] = (here / cfg["sut_root"]).resolve()
    cfg["prd"] = (here / cfg["prd"]).resolve()
    if cfg.get("openapi") and not str(cfg["openapi"]).startswith(("http://", "https://")):
        cfg["openapi"] = str((here / cfg["openapi"]).resolve())
    for mutant in cfg.get("mutants") or []:
        if mutant.get("patch"):
            mutant["patch"] = str((here / mutant["patch"]).resolve())
    return cfg


def validate_config(cfg: dict) -> list[str]:
    problems = []
    for key in ("sut_root", "prd"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            problems.append(f"thiếu `{key}`")
    sut = cfg.get("sut")
    if not isinstance(sut, dict) or not (isinstance(sut.get("base_url"), str) or isinstance(sut.get("start"), dict)):
        problems.append("`sut` cần `base_url` (SUT sạch bạn tự chạy) hoặc `start` (lệnh để công cụ tự chạy)")
    start = sut.get("start") if isinstance(sut, dict) else None
    if isinstance(start, dict) and (not isinstance(start.get("cmd"), str) or "{port}" not in start["cmd"]):
        problems.append("`sut.start.cmd` phải là chuỗi có chỗ `{port}`")
    non_testable = cfg.get("non_testable", [])
    if not isinstance(non_testable, list) or not all(isinstance(x, str) for x in non_testable):
        problems.append("`non_testable` phải là danh sách AC id")
    seen = set()
    for index, mutant in enumerate(cfg.get("mutants") or []):
        label = f"mutants[{index}]"
        if not isinstance(mutant, dict) or not isinstance(mutant.get("id"), str) or not mutant["id"].strip():
            problems.append(f"{label} thiếu `id`")
            continue
        if mutant["id"] in seen:
            problems.append(f"mutant `{mutant['id']}` trùng id")
        seen.add(mutant["id"])
        label = f"mutant `{mutant['id']}`"
        kinds = [k for k in _MUTANT_KINDS if mutant.get(k)]
        if len(kinds) != 1:
            problems.append(f"{label} cần đúng MỘT trong {list(_MUTANT_KINDS)}")
        if not isinstance(mutant.get("acs"), list) or not mutant["acs"]:
            problems.append(f"{label} cần `acs` (AC mà lỗi này vi phạm)")
        if "base_url" not in kinds and not (isinstance(start, dict)):
            problems.append(f"{label} cần `sut.start` (hoặc dùng `base_url` cho mutant)")
        for edit in mutant.get("edits") or []:
            if not (isinstance(edit, dict) and all(isinstance(edit.get(k), str) for k in ("file", "find", "replace")) and edit["find"]):
                problems.append(f"{label}: mỗi edit cần `file`, `find` (không rỗng), `replace`")
    return problems


def build_golden(cfg: dict, prd) -> dict:
    """Cùng dạng golden của eval_groundtruth: `acs` (testable) và `mutants` (acs)."""
    non_testable = set(cfg.get("non_testable") or [])
    acs = {ac.ac_id: {"testable": ac.ac_id not in non_testable} for story in prd.stories for ac in story.acs}
    unknown = sorted(non_testable - set(acs))
    if unknown:
        raise EvalError(f"`non_testable` chứa AC không có trong PRD: {', '.join(unknown)}")
    mutants = {}
    for mutant in cfg.get("mutants") or []:
        bad = [ac for ac in mutant["acs"] if ac not in acs]
        if bad:
            raise EvalError(f"mutant `{mutant['id']}` nêu AC không có trong PRD: {', '.join(bad)}")
        mutants[mutant["id"]] = {"acs": list(mutant["acs"])}
    return {"acs": acs, "mutants": mutants}


# ---------------- chèn lỗi (chỉ trên bản sao) ----------------

def _inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise EvalError(f"đường dẫn `{relative}` nằm ngoài SUT")
    return target


def apply_edits(root: Path, edits: list[dict], mutant_id: str) -> None:
    """Mỗi `find` phải xuất hiện ĐÚNG MỘT lần (hoặc `count` lần nếu khai): nếu SUT đổi mà edit không còn khớp thì báo, không âm thầm đo một "lỗi" không tồn tại."""
    for edit in edits:
        target = _inside(root, edit["file"])
        if not target.is_file():
            raise EvalError(f"mutant `{mutant_id}`: không có file `{edit['file']}`")
        text = target.read_bytes().decode("utf-8")   # bytes: giữ nguyên CRLF/LF
        expected = edit.get("count", 1)
        found = text.count(edit["find"])
        if found != expected:
            raise EvalError(f"mutant `{mutant_id}`: `find` khớp {found} lần trong `{edit['file']}` (cần {expected})")
        target.write_bytes(text.replace(edit["find"], edit["replace"]).encode("utf-8"))


def apply_patch(root: Path, patch: str, mutant_id: str) -> None:
    done = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if done.returncode != 0:
        raise EvalError(f"mutant `{mutant_id}`: `git apply` thất bại (patch không khớp SUT hiện tại)")


def free_port() -> int:
    return base.free_port()


class SutProcess:
    """Chạy SUT trong BẢN SAO của sut_root (đã chèn lỗi nếu có) bằng `sut.start.cmd`; dọn cả cây tiến trình khi thoát."""

    def __init__(self, sut: dict, mutant: dict | None, sut_root: Path):
        self.sut, self.mutant, self.sut_root = sut, mutant or {}, Path(sut_root)

    def __enter__(self) -> str:
        from qc_agent.core.proctree import kill_tree
        self._kill = kill_tree
        start = self.sut["start"]
        self._tmp = tempfile.TemporaryDirectory(prefix="qc-eval-sut-")
        copy = Path(self._tmp.name) / "sut"
        try:
            shutil.copytree(self.sut_root, copy, ignore=shutil.ignore_patterns(*(self.sut.get("copy_ignore") or DEFAULT_COPY_IGNORE)))
            if self.mutant.get("edits"):
                apply_edits(copy, self.mutant["edits"], self.mutant["id"])
            if self.mutant.get("patch"):
                apply_patch(copy, self.mutant["patch"], self.mutant["id"])
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            env = {**os.environ, **{k: str(v) for k, v in (start.get("env") or {}).items()}, **{k: str(v) for k, v in (self.mutant.get("env") or {}).items()},
                   "PORT": str(port), "APP_BASE_URL": url}
            self._log = Path(self._tmp.name) / "sut.log"
            self._logfile = self._log.open("wb")
            cwd = _inside(copy, start.get("cwd", "."))
            self.proc = subprocess.Popen(start["cmd"].format(port=port), shell=True, cwd=cwd, env=env, stdout=self._logfile, stderr=subprocess.STDOUT)
            self._wait(url, start)
            return url
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _wait(self, url: str, start: dict) -> None:
        probe = url + start.get("health_path", "/")
        deadline = time.monotonic() + float(start.get("timeout_s", 60))
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if httpx.get(probe, timeout=2).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        self._logfile.flush()
        tail = self._log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        raise EvalError(f"SUT không lên ({start.get('health_path', '/')}); 8 dòng log cuối của SUT:\n" + "\n".join(tail))

    def __exit__(self, *exc):
        if getattr(self, "proc", None) is not None:
            self._kill(self.proc)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        if getattr(self, "_logfile", None) is not None:
            self._logfile.close()
        self._tmp.cleanup()


@contextmanager
def sut_instance(cfg: dict, mutant: dict | None = None):
    """URL của SUT sạch (mutant=None) hoặc SUT bị chèn `mutant`."""
    sut = cfg["sut"]
    if mutant and mutant.get("base_url"):
        yield mutant["base_url"].rstrip("/")
    elif not isinstance(sut.get("start"), dict):
        if mutant:
            raise EvalError(f"mutant `{mutant['id']}` cần `sut.start`")
        yield sut["base_url"].rstrip("/")
    else:
        with SutProcess(sut, mutant, cfg["sut_root"]) as url:
            yield url


# ---------------- phần thuần (có unit test) ----------------

def summarize_mutants(results: dict[str, dict], required: set[str]) -> dict:
    killed = sorted(m for m, r in results.items() if r.get("killed"))
    survived = sorted(m for m in results if m not in killed)
    total = len(results)
    return {"killed": killed, "survived": survived, "count": len(killed), "total": total, "value": len(killed) / total if total else 0.0,
            "required_missed": sorted(m for m in required if m in results and not results[m].get("killed"))}


def verdict(thresholds: dict, *, coverage: float | None, green: float | None, mutants: dict | None, baseline_green: bool | None) -> dict:
    """Phần nào chưa đo (None) thì không có mặt trong `checks`; `passed` cần ít nhất một check và tất cả đều đạt."""
    checks: dict[str, bool] = {}
    if coverage is not None:
        checks["ac_coverage"] = coverage >= thresholds["ac_coverage"]
    if green is not None:
        checks["green_rate"] = green >= thresholds["green_rate"]
    if baseline_green is not None:
        checks["baseline_green"] = baseline_green
    if mutants is not None:
        checks["mutant_kill_rate"] = mutants["total"] > 0 and mutants["value"] >= thresholds["mutant_kill_rate"]
        checks["required_mutants"] = not mutants["required_missed"]
    return {"checks": checks, "passed": bool(checks) and all(checks.values())}


def render_markdown(report: dict) -> str:
    mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
    checks, thr = report["verdict"]["checks"], report["thresholds"]
    lines = [f"## Đo Ground-Truth trên SUT `{report['name']}` — PRD `{report['prd_id']}` · llm={report['llm']} · model={report['model']}", ""]
    if report.get("labeled_by"):
        lines += [f"Nhãn `non_testable`/mutant do: {report['labeled_by']}", ""]
    lines += ["| Metric | Giá trị | Ngưỡng | |", "|---|---|---|---|"]
    if "generation" in report:
        gen = report["generation"]
        lines += [f"| (a) AC coverage của bộ do LLM sinh (median {len(gen['runs'])} lượt) | {gen['ac_coverage']['median']:.1%} ({gen['ac_coverage']['covered']}/{gen['ac_coverage']['testable']} AC ở lượt thấp nhất) | ≥ {thr['ac_coverage']:.0%} | {mark(checks['ac_coverage'])} |",
                  f"| (b) TC xanh trên SUT sạch (median) | {gen['green_rate']['median']:.1%} | ≥ {thr['green_rate']:.0%} | {mark(checks['green_rate'])} |"]
    if "approved" in report:
        appr = report["approved"]
        lines += [f"| Bộ đã duyệt xanh trên SUT sạch ({appr['test_cases']} TC) | {'có' if appr['baseline_green'] else 'không'} | bắt buộc | {mark(checks['baseline_green'])} |",
                  f"| (c) Mutant bị bắt | {appr['mutants']['count']}/{appr['mutants']['total']} | ≥ {thr['mutant_kill_rate']:.0%} | {mark(checks['mutant_kill_rate'])} |",
                  f"| (c) Mutant `required` đều bị bắt | {'có' if not appr['mutants']['required_missed'] else 'thiếu: ' + ', '.join(appr['mutants']['required_missed'])} | bắt buộc | {mark(checks['required_mutants'])} |",
                  f"| AC coverage của bộ đã duyệt (thông tin) | {appr['ac_coverage']['value']:.1%} | — | |", "",
                  "| Mutant | Bắt? | AC bị vi phạm | TC fail |", "|---|---|---|---|"]
        for mutant_id, result in appr["mutant_results"].items():
            failing = ", ".join(result.get("failing_tcs", [])[:3]) + (" …" if len(result.get("failing_tcs", [])) > 3 else "")
            note = " (đo lỗi)" if result.get("measurement_error") else "" if result.get("caught_by_expected_ac") or not result.get("killed") else " (bắt bởi TC ngoài AC dự kiến)"
            lines.append(f"| {mutant_id} | {mark(result.get('killed', False))}{note} | {', '.join(report['mutant_acs'].get(mutant_id, [])) or '—'} | {failing or '—'} |")
        if appr["mutants"]["survived"]:
            lines += ["", f"Mutant sống sót (bộ test bỏ sót): {', '.join(appr['mutants']['survived'])}"]
    if "generation" in report and report["generation"]["ac_coverage"]["missing"]:
        lines += ["", f"AC testable chưa có TC (lượt thấp nhất): {', '.join(report['generation']['ac_coverage']['missing'])}"]
    lines += ["", f"**Kết luận: {'ĐẠT' if report['verdict']['passed'] else 'KHÔNG ĐẠT'}**"]
    return "\n".join(lines) + "\n"


# ---------------- đo ----------------

def measure_generation(catalog: dict, golden: dict, cfg: dict, analysis) -> dict:
    """(a) và (b) cho MỘT lượt sinh: TC được ép `approved` trong thư mục tạm (không đụng file thật) rồi chạy trên SUT sạch."""
    from qc_agent.groundtruth import render as gt_render
    coverage = base.ac_coverage(golden, catalog["test_cases"])
    forced = json.loads(json.dumps(catalog))
    for tc in forced["test_cases"]:
        tc["status"] = "approved"
    forced["status"] = "approved"
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        gt_render.write(gt_render.render(forced, sut_root=workspace, openapi=analysis), workspace)
        with sut_instance(cfg) as url:
            code, outcomes = base.run_suite(workspace, url)
    return {"ac_coverage": coverage, "green_rate": base.green_rate(outcomes), "test_cases": len(catalog["test_cases"]), "exit_code": code}


def measure_approved(cfg: dict, golden: dict, measure_mutants: bool) -> dict:
    source = Path(cfg["sut_root"]) / GT_DIR
    if not (source / "test-cases.yaml").is_file():
        raise NoApprovedSuite(f"không có bộ Ground-Truth đã duyệt ở {GT_DIR} của SUT (chạy `gt generate`, QA duyệt, rồi đo lại)")
    catalog = yaml.safe_load((source / "test-cases.yaml").read_text(encoding="utf-8"))
    approved = [tc for tc in catalog["test_cases"] if tc.get("status") == "approved"]
    if not approved:
        raise NoApprovedSuite("bộ Ground-Truth chưa có TC nào `approved`")
    out: dict = {"test_cases": len(approved), "ac_coverage": base.ac_coverage(golden, approved)}
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        shutil.copytree(source, workspace / GT_DIR, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        with sut_instance(cfg) as url:
            code, outcomes = base.run_suite(workspace, url)
        out["baseline_green"] = code == 0 and bool(outcomes) and all(state == "passed" for state in outcomes.values())
        out["baseline_failing"] = base.green_rate(outcomes)["failing"]
        results: dict[str, dict] = {}
        if measure_mutants and out["baseline_green"]:   # bộ không xanh trên SUT sạch thì "bắt được lỗi" vô nghĩa: mọi thứ đều fail
            for mutant in cfg.get("mutants") or []:
                with sut_instance(cfg, mutant) as url:
                    code, outcomes = base.run_suite(workspace, url)
                results[mutant["id"]] = base.mutant_result(mutant["id"], code, outcomes, golden, approved)
        out["mutant_results"] = results
        required = {m["id"] for m in cfg.get("mutants") or [] if m.get("required")}
        out["mutants"] = summarize_mutants(results, required)
    return out


def generate_once(cfg: dict, llm: str, fake_response: Path | None, model: str, egress_dir: Path):
    import httpx as _httpx
    from qc_agent.groundtruth.generate import generate
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(cfg["prd"], openapi_source=cfg.get("openapi"))
    if llm == "fake":
        payload = json.loads(Path(fake_response).read_text(encoding="utf-8"))
        return prd, generate(prd, model=model, egress_dir=egress_dir, transport=_httpx.MockTransport(lambda request: _httpx.Response(200, json=payload)))
    return prd, generate(prd, model=model, egress_dir=egress_dir)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--llm", choices=("fake", "real"), default="real")
    ap.add_argument("--runs", type=int, default=1, help="số lượt sinh (median cho (a),(b)); với Gemini free tier mỗi lượt là 1-2 request")
    ap.add_argument("--skip-generate", action="store_true", help="không gọi LLM: chỉ đo bộ đã duyệt trong SUT")
    ap.add_argument("--skip-mutants", action="store_true", help="bỏ đo (c)")
    ap.add_argument("--fake-response", type=Path, default=base.DEFAULT_FAKE, help="response Messages API phát lại ở chế độ fake")
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--yes", action="store_true", help="xác nhận việc gửi PRD ra nhà cung cấp LLM (bắt buộc với --llm real)")
    ap.add_argument("--price-in", type=float, help="USD/MTok đầu vào (Gemini free tier: mặc định 0)")
    ap.add_argument("--price-out", type=float, help="USD/MTok đầu ra")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) and exit_.code == 0 else 3
    try:
        if not 1 <= args.runs <= 10:
            raise EvalError("--runs phải trong 1..10")
        if args.skip_generate and args.skip_mutants:
            raise EvalError("--skip-generate và --skip-mutants cùng bật thì không còn gì để đo ngoài baseline; bỏ một cờ")
        from qc_agent import settings
        from qc_agent.groundtruth.prd import parse_prd
        from qc_agent.llm.client import provider_of
        from qc_agent.scaffold import openapi as scaffold_openapi
        cfg = load_config(args.config)
        thresholds = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
        prd = parse_prd(cfg["prd"], openapi_source=cfg.get("openapi"))
        golden = build_golden(cfg, prd)
        model = settings.get().gt_model
        if args.llm == "fake":
            if provider_of(model) == "gemini":
                model = "claude-sonnet-5"   # response giả có dạng Messages API
            if not os.environ.get("ANTHROPIC_API_KEY", "").strip():   # khoá rỗng (vd. từ .env) cũng phải thay
                os.environ["ANTHROPIC_API_KEY"] = "sk-ant-eval-fake"
        elif not args.skip_generate:
            free = provider_of(model) == "gemini" and args.price_in is None and args.price_out is None
            estimate = base.estimate_for(cfg["prd"], cfg.get("openapi"), args.runs, model, 0.0 if free else args.price_in, 0.0 if free else args.price_out)
            print(f"ƯỚC TÍNH ({model}): ~{estimate['input_tokens_per_run']} token vào + tối đa {estimate['output_tokens_per_run_max']} token ra mỗi lượt, "
                  f"{args.runs} lượt ≈ {'$0 (free tier, tốn quota RPM/RPD)' if free else '$%.2f' % estimate['usd_total']}. "
                  "PRD được gửi tới nhà cung cấp LLM (ghi egress trước khi gửi).", file=sys.stderr)
            if not args.yes:
                raise EvalError("--llm real cần --yes (xác nhận việc gửi PRD ra ngoài)")
        analysis = scaffold_openapi.analyze(scaffold_openapi.load(cfg["openapi"])) if cfg.get("openapi") else None

        report: dict = {"name": cfg.get("name", Path(cfg["sut_root"]).name), "prd_id": prd.prd_id, "llm": "skipped" if args.skip_generate else args.llm,
                        "model": "—" if args.skip_generate else model if args.llm == "real" else f"{model} (fake)", "labeled_by": cfg.get("labeled_by"),
                        "thresholds": thresholds, "mutant_acs": {m["id"]: m["acs"] for m in cfg.get("mutants") or []}}
        coverage = green = mutants = baseline = None
        if not args.skip_generate:
            runs = []
            with tempfile.TemporaryDirectory() as egress:
                for _ in range(args.runs):
                    _, generated = generate_once(cfg, args.llm, args.fake_response, model, Path(egress))
                    measured = measure_generation(generated.catalog, golden, cfg, analysis)
                    measured["usage"] = {"input_tokens": generated.usage.input_tokens, "output_tokens": generated.usage.output_tokens}
                    measured["orphans"] = list(generated.orphans)
                    runs.append(measured)
            worst = min(runs, key=lambda r: r["ac_coverage"]["value"])
            coverage, green = base.median([r["ac_coverage"]["value"] for r in runs]), base.median([r["green_rate"]["value"] for r in runs])
            report["generation"] = {"runs": runs, "ac_coverage": {"median": coverage, **{k: worst["ac_coverage"][k] for k in ("testable", "covered", "missing")}},
                                    "green_rate": {"median": green}}
        if args.skip_generate or not args.skip_mutants:
            try:
                approved = measure_approved(cfg, golden, measure_mutants=not args.skip_mutants)
            except NoApprovedSuite as missing:
                if args.skip_generate:
                    raise
                print(f"Lưu ý: bỏ qua (c): {missing}. Sau khi QA duyệt, chạy lại với --skip-generate.", file=sys.stderr)
            else:
                report["approved"] = approved
                baseline = approved["baseline_green"]
                mutants = approved["mutants"] if not args.skip_mutants and cfg.get("mutants") else None
    except EvalError as error:
        print(f"LỖI: {error}", file=sys.stderr)
        return 3
    except Exception as error:  # noqa: BLE001 — chỉ in loại lỗi: thông điệp của LLM/YAML có thể trích lại nội dung
        print(f"LỖI HỆ THỐNG: {type(error).__name__}", file=sys.stderr)
        return 3

    report["verdict"] = verdict(thresholds, coverage=coverage, green=green, mutants=mutants, baseline_green=baseline)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.write(render_markdown(report))
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
