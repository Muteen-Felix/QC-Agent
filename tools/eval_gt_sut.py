"""Đo Ground-Truth trên MỘT SUT thật (S1-09): cấu hình bằng YAML, không dính toyapp/noteboard.

    python tools/eval_gt_sut.py --config eval/my-sut.yaml --llm real --runs 3 --yes --out-json eval/out.json
    python tools/eval_gt_sut.py --config eval/my-sut.yaml --skip-generate          # chỉ đo bộ ĐÃ DUYỆT (không gọi LLM, không tốn quota)
    python tools/eval_gt_sut.py --config eval/my-sut.yaml --from-saved runs/eval-generated-<giờ>   # đo lại bộ vừa sinh ở lần trước, KHÔNG gọi LLM, không tốn tiền

Lần chạy `--llm real` ghi bộ vừa sinh (catalog.json, meta.json) và egress vào `--keep-dir` (mặc định runs/eval-generated-<giờ UTC>) NGAY khi LLM trả về, trước khi đo; `--out-json`
cũng được ghi một phần (khoá `partial`) ngay sau khi đo xong phần sinh. Một mutant lỗi khi đo chỉ bị ghi `measurement_error` (tính là không bị bắt), không làm mất cả lượt.

Ba metric (dùng lại phần tính của tools/eval_groundtruth.py):
  (a) AC coverage   AC `testable` có >= 1 TC / tổng AC `testable` (PRD trừ `non_testable` do QA gán). Mỗi lượt sinh; --runs N lấy median.
  (b) TC xanh       TC vừa sinh (ép `approved` trong thư mục tạm) chạy pass trên SUT SẠCH / tổng TC.
  (c) Bắt lỗi       bộ ĐÃ DUYỆT (`<sut_root>/.qc-agent/ground-truth`) FAIL khi SUT bị chèn lỗi (mutant). Mutant khai trong YAML bằng một trong ba cách:
                    `edits` (thay đúng một đoạn mã), `patch` (diff), `env` (cờ lỗi có sẵn của SUT), hoặc `base_url` (bạn tự chạy bản lỗi).
                    `edits`/`patch` chỉ áp lên BẢN SAO của sut_root; repo thật không bao giờ bị sửa.
So hai bộ sinh (`--generator single|agent|both`, mặc định `single` = hành vi cũ). Với `agent` và `both` mỗi lượt còn có:
  (d) Bộ chấm coverage tất định (groundtruth/coverage.py): AC / technique / API, chấm trên bộ vừa sinh; agent phải đạt 100% ở cả ba chiều ở MỌI lượt.
  (e) Mutant trên bộ VỪA SINH: giữ các TC xanh trên SUT sạch rồi chạy mutant lên tập đó (bật mặc định khi `both`; `--generated-mutants` để bật riêng). Đây là cách duy nhất so
      được hai bộ sinh mà không cần QA duyệt tay, vì (c) chỉ đo bộ ĐÃ DUYỆT.
  (f) Chi phí/thời gian của agent (ước tính theo bảng giá trong llm/agent_loop.py, KHÔNG phải hoá đơn) và số lượt.
`both` đạt khi agent: coverage scorer 100% mọi lượt · kill rate ≥ ngưỡng HOẶC hơn single ≥ `kill_gain` (0,20) · tỉ lệ xanh ≥ max(single, `green_min` 0,85) · mọi lượt hoàn tất ·
chi phí ≤ `max_cost_usd` (10) · thời gian ≤ `max_wall_s` (1800); ngưỡng chỉnh được ở khoá `agent_thresholds` của YAML. Chế độ `--llm fake` phát lại hội thoại cố định
(`--fake-script`, mặc định tests/fixtures/llm/gt_agent_noteboard.json) để kiểm đường ống mà không tốn tiền: số liệu của nó KHÔNG nói gì về chất lượng thật.
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
from types import SimpleNamespace

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
AGENT_THRESHOLDS = {"kill_gain": 0.20, "green_min": 0.85, "max_cost_usd": 10.0, "max_wall_s": 1800.0}
DEFAULT_AGENT_FAKE = ROOT / "tests" / "fixtures" / "llm" / "gt_agent_noteboard.json"
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
    given = cfg.get("agent_thresholds")
    if given is not None and not (isinstance(given, dict) and set(given) <= set(AGENT_THRESHOLDS) | set(DEFAULT_THRESHOLDS)
                                  and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in given.values())):
        problems.append(f"`agent_thresholds` phải là object số không âm với khoá thuộc {sorted(set(AGENT_THRESHOLDS) | set(DEFAULT_THRESHOLDS))}")
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
            "required_missed": sorted(m for m in required if m in results and not results[m].get("killed")),
            "measurement_errors": sorted(m for m, r in results.items() if r.get("measurement_error"))}   # đã nằm trong `survived`: chưa chắc bộ test bỏ sót, có thể đo hỏng


def verdict(thresholds: dict, *, coverage: float | None, green: float | None, mutants: dict | None, baseline_green: bool | None,
            scorer_complete: bool | None = None, generated_mutants: dict | None = None, comparison: dict | None = None) -> dict:
    """Phần nào chưa đo (None) thì không có mặt trong `checks`; `passed` cần ít nhất một check và tất cả đều đạt.
    `scorer_complete`, `generated_mutants`, `comparison` chỉ dành cho bộ sinh agent (xem `aggregate_runs`, `compare`)."""
    checks: dict[str, bool] = {}
    if scorer_complete is not None:
        checks["coverage_scorer_100"] = scorer_complete
    if generated_mutants is not None:
        checks["generated_mutant_kill_rate"] = generated_mutants["median_kill_rate"] >= thresholds["mutant_kill_rate"]
    for name, ok in ((comparison or {}).get("checks") or {}).items():
        checks[f"vs_single:{name}"] = ok
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
        if appr["mutants"].get("measurement_errors"):
            lines += ["", f"Mutant LỖI KHI ĐO (đã tính là không bị bắt, chưa chắc do bộ test): {', '.join(appr['mutants']['measurement_errors'])}"]
    if "generation" in report and report["generation"]["ac_coverage"]["missing"]:
        lines += ["", f"AC testable chưa có TC (lượt thấp nhất): {', '.join(report['generation']['ac_coverage']['missing'])}"]
    lines += render_generators(report)
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
                results[mutant["id"]] = run_mutant(cfg, mutant, workspace, golden, approved)
        out["mutant_results"] = results
        required = {m["id"] for m in cfg.get("mutants") or [] if m.get("required")}
        out["mutants"] = summarize_mutants(results, required)
    return out


def scripted_transport(responses: list[dict]) -> httpx.MockTransport:
    """Phát `responses` (response Messages API đã ghi sẵn) tuần tự cho agent ở chế độ fake. Hết script thì trả 599 để lần chạy lộ ra ngay là script quá ngắn."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            return httpx.Response(599, json={"error": {"type": "api_error", "message": "script đã hết"}})
        return httpx.Response(200, json=queue.pop(0))

    return httpx.MockTransport(handler)


def generate_agent_once(cfg: dict, llm: str, fake_script: Path | None, model: str, egress_dir: Path, spec: dict | None):
    """Một lượt sinh bằng AGENT (đọc `sut_root` + OpenAPI đầy đủ). `--llm fake` phát lại `fake_script` (danh sách response), mỗi lượt một transport mới."""
    from qc_agent.groundtruth import agent as gt_agent
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(cfg["prd"], openapi_source=cfg.get("openapi"))
    options: dict = {"model": model, "egress_dir": egress_dir, "source_root": Path(cfg["sut_root"]), "openapi_spec": spec}
    if llm == "fake":
        try:
            options["transport"] = scripted_transport(json.loads(Path(fake_script).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            raise EvalError("không đọc được --fake-script (cần file JSON là danh sách response Messages API)") from None
    return prd, gt_agent.generate_agent(prd, **options)


def save_generated(keep_dir: Path | None, kind: str, index: int, generated) -> Path | None:
    """Ghi bộ VỪA SINH ra đĩa ngay khi LLM trả về, TRƯỚC mọi phép đo: lỗi ở bước đo (SUT không lên, pytest quá giờ...) không làm mất kết quả đã trả tiền.
    Đo lại không tốn tiền bằng `--from-saved <keep_dir>`. Chỉ ghi catalog + số liệu chạy; không có khoá, không có nội dung nào ngoài những gì đã nằm trong catalog."""
    if keep_dir is None:
        return None
    target = Path(keep_dir) / f"{kind}-run{index}"
    target.mkdir(parents=True, exist_ok=True)
    usage = generated.usage
    meta = {"usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                      "cache_read_input_tokens": usage.cache_read_input_tokens},
            "orphans": list(generated.orphans), "warnings": list(generated.warnings), "dropped": generated.dropped, "agent": getattr(generated, "agent", None)}
    (target / "catalog.json").write_text(json.dumps(generated.catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    return target


def load_saved(saved_dir: Path, kind: str, index: int):
    """Đọc lại một lượt đã lưu bởi `save_generated`: cùng dạng với kết quả của bộ sinh (catalog, usage, orphans, warnings, dropped, agent), không gọi LLM."""
    from qc_agent.llm import client
    source = Path(saved_dir) / f"{kind}-run{index}"
    try:
        catalog = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
        meta = json.loads((source / "meta.json").read_text(encoding="utf-8"))
        usage = client.Usage(*(int(meta["usage"][k]) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")))
    except (OSError, ValueError, KeyError, TypeError):
        raise EvalError(f"không đọc được lượt đã lưu {kind}-run{index} trong --from-saved (cần catalog.json + meta.json do lần chạy trước ghi ra)") from None
    return SimpleNamespace(catalog=catalog, usage=usage, orphans=tuple(meta.get("orphans") or ()), warnings=tuple(meta.get("warnings") or ()),
                           dropped=meta.get("dropped", 0), agent=meta.get("agent") or {})


@contextmanager
def egress_dir_for(keep_dir: Path | None, kind: str):
    """Thư mục egress.jsonl (mỗi request rời máy một dòng). Có `keep_dir` thì giữ lại làm bằng chứng kiểm toán, không thì dùng thư mục tạm."""
    if keep_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)
    else:
        path = Path(keep_dir) / f"{kind}-egress"
        path.mkdir(parents=True, exist_ok=True)
        yield path


def run_mutant(cfg: dict, mutant: dict, workspace: Path, golden: dict, tcs: list[dict]) -> dict:
    """Đo MỘT mutant. Mutant lỗi khi đo (không áp được, SUT không lên, pytest quá giờ) không làm mất cả lượt: ghi `measurement_error` kèm lý do ngắn,
    và tính là KHÔNG bị bắt (bảo thủ: không thổi phồng kill rate)."""
    try:
        with sut_instance(cfg, mutant) as url:
            code, outcomes = base.run_suite(workspace, url)
    except Exception as error:  # noqa: BLE001 — EvalError có thông điệp an toàn; lỗi khác chỉ nêu loại
        reason = str(error) if isinstance(error, EvalError) else type(error).__name__
        return {"killed": False, "exit_code": None, "failing_tcs": [], "caught_by_expected_ac": False, "measurement_error": True, "error": reason}
    return base.mutant_result(mutant["id"], code, outcomes, golden, tcs)


def write_report(report: dict, path: Path | None) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")


def measure_scorer(catalog: dict, spec: dict | None) -> dict:
    """(d) Bộ chấm coverage tất định trên bộ VỪA SINH (tính TC draft và waiver draft: 'nếu QA duyệt hết thì đã đủ chưa'). Không có OpenAPI thì chỉ chấm được chiều AC."""
    from qc_agent.groundtruth import coverage as gt_coverage
    report = gt_coverage.score(catalog, gt_coverage.snapshot(spec) if spec else None, tc_statuses=("draft", "approved"), waiver_statuses=("draft", "approved"))
    return {"dims": report.as_dict(limit=10), "complete": report.complete()}


def measure_generated_mutants(catalog: dict, golden: dict, cfg: dict, analysis) -> dict:
    """(e) Mutant chạy trên bộ VỪA SINH: ép approved, chạy trên SUT sạch, GIỮ các TC xanh, rồi chạy mutant lên tập xanh đó. TC đỏ trên SUT sạch bị loại vì 'bắt được lỗi' của
    chúng vô nghĩa (chúng fail ở mọi nơi). Nhờ vậy so được hai bộ sinh mà không cần QA duyệt tay."""
    from qc_agent.groundtruth import render as gt_render
    if not cfg.get("mutants"):
        return {"measured": False, "reason": "cấu hình không có mutant"}
    forced = json.loads(json.dumps(catalog))
    for tc in forced["test_cases"]:
        tc["status"] = "approved"
    forced["status"] = "approved"
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "clean"
        clean.mkdir()
        gt_render.write(gt_render.render(forced, sut_root=clean, openapi=analysis), clean)
        with sut_instance(cfg) as url:
            _, outcomes = base.run_suite(clean, url)
        green = {tc_id for tc_id, state in outcomes.items() if state == "passed"}
        subset = json.loads(json.dumps(forced))
        subset["test_cases"] = [tc for tc in forced["test_cases"] if tc["tc_id"] in green]
        if not subset["test_cases"]:
            return {"measured": False, "reason": "không có TC nào xanh trên SUT sạch"}
        work = Path(tmp) / "green"
        work.mkdir()
        gt_render.write(gt_render.render(subset, sut_root=work, openapi=analysis), work)
        results: dict[str, dict] = {}
        for mutant in cfg["mutants"]:
            results[mutant["id"]] = run_mutant(cfg, mutant, work, golden, subset["test_cases"])
    required = {m["id"] for m in cfg["mutants"] if m.get("required")}
    return {"measured": True, "test_cases": len(subset["test_cases"]), "excluded_red": len(forced["test_cases"]) - len(subset["test_cases"]),
            "results": results, **summarize_mutants(results, required)}


def aggregate_runs(runs: list[dict]) -> dict:
    """Gộp N lượt của MỘT bộ sinh. Giữ nguyên các khoá cũ của `report["generation"]` (runs, ac_coverage, green_rate) và thêm scorer / mutants_generated / agent khi có đo."""
    worst = min(runs, key=lambda r: r["ac_coverage"]["value"])
    out: dict = {"runs": runs,
                 "ac_coverage": {"median": base.median([r["ac_coverage"]["value"] for r in runs]), **{k: worst["ac_coverage"][k] for k in ("testable", "covered", "missing")}},
                 "green_rate": {"median": base.median([r["green_rate"]["value"] for r in runs])}}
    scored = [r["scorer"] for r in runs if r.get("scorer") and r["scorer"]["dims"]]
    if scored:
        out["scorer"] = {"median": {name: base.median([s["dims"][name]["ratio"] for s in scored if name in s["dims"]]) for name in ("ac", "technique", "api")
                                    if any(name in s["dims"] for s in scored)},
                         "complete_runs": sum(1 for s in scored if s["complete"]), "runs": len(scored)}
    measured = [r["mutants_generated"] for r in runs if (r.get("mutants_generated") or {}).get("measured")]
    if measured:
        out["mutants_generated"] = {"median_kill_rate": base.median([m["value"] for m in measured]), "runs": len(measured),
                                    "survived_in_worst": min(measured, key=lambda m: m["value"])["survived"]}
        errored = sorted({e for m in measured for e in m.get("measurement_errors", [])})
        if errored:     # chỉ có khoá này khi có mutant đo hỏng: giữ nguyên dạng cũ của báo cáo khi mọi thứ đo được
            out["mutants_generated"]["measurement_errors"] = errored
    agents = [r["agent"] for r in runs if r.get("agent")]
    if agents:
        costs = [a["cost_usd_est"] for a in agents if a.get("cost_usd_est") is not None]
        out["agent"] = {"runs": len(agents), "completed_runs": sum(1 for a in agents if a["completed"]), "turns_median": base.median([a["turns"] for a in agents]),
                        "cost_usd_median": base.median(costs) if costs else None, "cost_usd_max": max(costs) if costs else None,
                        "wall_s_max": max(a.get("duration_s", 0.0) for a in agents)}
    return out


def compare(single: dict, agent: dict, thresholds: dict) -> dict:
    """Agent có 'tốt hơn hẳn' single không (kết quả của `aggregate_runs`). Mục nào chưa đo thì không có mặt trong `checks`."""
    t = {**DEFAULT_THRESHOLDS, **AGENT_THRESHOLDS, **thresholds}
    checks: dict[str, bool] = {}
    if agent.get("scorer"):
        checks["scorer_100"] = agent["scorer"]["complete_runs"] == agent["scorer"]["runs"]
    if agent.get("mutants_generated") and single.get("mutants_generated"):
        a, s = agent["mutants_generated"]["median_kill_rate"], single["mutants_generated"]["median_kill_rate"]
        checks["kill_rate"] = a >= t["mutant_kill_rate"] or a - s >= t["kill_gain"]
    checks["green_rate"] = agent["green_rate"]["median"] >= max(single["green_rate"]["median"], t["green_min"])
    run = agent.get("agent")
    if run:
        checks["completed"] = run["completed_runs"] == run["runs"]
        if run["cost_usd_max"] is not None:
            checks["cost"] = run["cost_usd_max"] <= t["max_cost_usd"]
        checks["wall_time"] = run["wall_s_max"] <= t["max_wall_s"]
    return {"checks": checks, "passed": bool(checks) and all(checks.values())}


def render_generators(report: dict) -> list[str]:
    """Bảng so các bộ sinh đã chạy (single / agent) + các check so sánh. Rỗng khi chỉ chạy `single` như trước."""
    generators = report.get("generators") or {}
    if "agent" not in generators:
        return []
    mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
    names = [n for n in ("single", "agent") if n in generators]
    cell = lambda fn: " | ".join(fn(generators[n]) for n in names)  # noqa: E731
    pct = lambda value: "—" if value is None else f"{value:.0%}"  # noqa: E731
    lines = ["", "### So các bộ sinh", "", "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names),
             "| AC coverage theo golden (median) | " + cell(lambda g: pct(g["ac_coverage"]["median"])) + " |",
             "| TC xanh trên SUT sạch (median) | " + cell(lambda g: pct(g["green_rate"]["median"])) + " |"]
    for dim, label in (("ac", "AC"), ("technique", "technique"), ("api", "API")):
        lines.append(f"| Coverage scorer {label} (median) | " + cell(lambda g, d=dim: pct((g.get("scorer") or {}).get("median", {}).get(d))) + " |")
    lines.append("| Coverage scorer 100% (số lượt) | " + cell(lambda g: "—" if not g.get("scorer") else f"{g['scorer']['complete_runs']}/{g['scorer']['runs']}") + " |")
    lines.append("| Mutant bị bắt trên bộ vừa sinh (median) | " + cell(lambda g: pct((g.get("mutants_generated") or {}).get("median_kill_rate"))) + " |")
    lines.append("| Số TC (median) | " + cell(lambda g: str(int(base.median([r["test_cases"] for r in g["runs"]])))) + " |")
    agent = generators["agent"].get("agent")
    if agent:
        cost = "—" if agent["cost_usd_max"] is None else f"≤ ${agent['cost_usd_max']:.2f}"
        lines += ["", f"Agent: {agent['completed_runs']}/{agent['runs']} lượt hoàn tất · {agent['turns_median']:.0f} lượt gọi (median) · chi phí ước tính {cost} · {agent['wall_s_max']:.0f}s (lượt lâu nhất)"]
    for name in names:
        errors = (generators[name].get("mutants_generated") or {}).get("measurement_errors")
        if errors:
            lines += ["", f"Mutant LỖI KHI ĐO ở bộ sinh {name} (đã tính là không bị bắt, chưa chắc do bộ test): {', '.join(errors)}"]
    comparison = report.get("comparison")
    if comparison:
        lines += ["", "| Tiêu chí 'agent tốt hơn hẳn' | |", "|---|---|"] + [f"| {name} | {mark(ok)} |" for name, ok in comparison["checks"].items()]
    return lines


def generate_once(cfg: dict, llm: str, fake_response: Path | None, model: str, egress_dir: Path):
    import httpx as _httpx
    from qc_agent.groundtruth.generate import generate
    from qc_agent.groundtruth.prd import parse_prd
    prd = parse_prd(cfg["prd"], openapi_source=cfg.get("openapi"))
    if llm == "fake":
        payload = json.loads(Path(fake_response).read_text(encoding="utf-8"))
        return prd, generate(prd, model=model, egress_dir=egress_dir, transport=_httpx.MockTransport(lambda request: _httpx.Response(200, json=payload)))
    return prd, generate(prd, model=model, egress_dir=egress_dir)


def check_only(cfg: dict, prd, golden: dict) -> int:
    """Kiểm khô, KHÔNG gọi LLM: mọi thứ mà lần chạy thật cần phải đúng (đường dẫn, PRD, OpenAPI, repo map, SUT khởi động được) để không đốt tiền vì lỗi cấu hình."""
    from qc_agent.groundtruth import coverage as gt_coverage, repo_map as gt_repo_map
    from qc_agent.scaffold import openapi as scaffold_openapi
    acs = sum(len(s.acs) for s in prd.stories)
    testable = sum(1 for spec_ in golden["acs"].values() if spec_["testable"])
    lines = [f"PRD `{prd.prd_id}`: {len(prd.stories)} story, {acs} AC ({testable} testable theo non_testable), {len(prd.endpoints)} endpoint từ OpenAPI"]
    if acs == 0:
        raise EvalError("PRD không có AC nào: định dạng PRD sai (xem `qc-agent gt info`)")
    if cfg.get("openapi"):
        facts = gt_coverage.snapshot(scaffold_openapi.load(cfg["openapi"]))
        cells = sum(len(gt_coverage.requirements(op)) for op in facts["operations"])
        lines.append(f"OpenAPI: {len(facts['operations'])} operation, {cells} yêu cầu technique để chấm")
    else:
        lines.append("OpenAPI: KHÔNG có trong cấu hình: agent không có tool openapi_*, scorer chỉ chấm AC")
    built = gt_repo_map.build(Path(cfg["sut_root"]))
    lines.append(f"repo map: {built['stats']['files']} file Python đã quét ({built['stats']['skipped']} bỏ qua), văn bản {len(gt_repo_map.render_text(built))} ký tự")
    with sut_instance(cfg) as url:
        lines.append(f"SUT sạch khởi động được: {url}")
    lines.append(f"mutant: {len(cfg.get('mutants') or [])} (khai báo hợp lệ)")
    print("\n".join("OK  " + line for line in lines))
    print("KIỂM KHÔ ĐẠT: chưa gọi LLM nào. Bước tiếp: `--llm fake` chỉ hợp với noteboard; với SUT khác hãy chạy thật nhỏ (smoke) với QC_GT_AGENT_MAX_COST_USD=1.0.")
    return 0


def main(argv: list[str]) -> int:
    os.environ["QC_GT_CACHE_DIR"] = "none"   # đo model: cache sẽ làm các lượt lặp trả cùng một kết quả và sai số liệu/chi phí (S4-02)
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
    ap.add_argument("--generator", choices=("single", "agent", "both"), default="single",
                    help="bộ sinh cần đo: single (một lời gọi, mặc định, hành vi cũ), agent (đọc mã nguồn, nhiều lượt, chỉ Claude), hoặc both để so hai bên cùng PRD/SUT")
    ap.add_argument("--agent-model", help="ghi đè QC_GT_AGENT_MODEL cho agent (phải là model Claude)")
    ap.add_argument("--fake-script", type=Path, default=DEFAULT_AGENT_FAKE, help="hội thoại nhiều lượt (danh sách response Messages API) phát lại ở chế độ fake cho agent")
    ap.add_argument("--max-total-usd", type=float, help="BẮT BUỘC khi --llm real với agent: tổng ngân sách tối đa cho cả lệnh; từ chối chạy nếu trần/lượt × số lượt × số bộ sinh agent vượt mức này")
    ap.add_argument("--check-only", action="store_true", help="kiểm khô KHÔNG gọi LLM: đọc cấu hình, PRD, OpenAPI, quét repo map, khởi động SUT sạch một lần; in số liệu rồi thoát (làm trước khi chạy thật)")
    ap.add_argument("--generated-mutants", action="store_true", help="đo mutant trên bộ VỪA SINH (tập TC xanh); tự bật khi --generator both")
    ap.add_argument("--keep-dir", type=Path, help="nơi ghi bộ vừa sinh + egress NGAY khi LLM trả về, trước khi đo (mặc định với --llm real: runs/eval-generated-<giờ UTC>)")
    ap.add_argument("--from-saved", type=Path, help="đo lại bộ đã lưu bởi một lần chạy trước (thư mục --keep-dir của lần đó), KHÔNG gọi LLM, không tốn tiền")
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
        if args.from_saved and (args.skip_generate or args.keep_dir):
            raise EvalError("--from-saved không đi cùng --skip-generate hay --keep-dir (nó đọc bộ đã lưu, không sinh và không ghi thêm)")
        from qc_agent import settings
        from qc_agent.groundtruth.prd import parse_prd
        from qc_agent.llm.client import provider_of
        from qc_agent.scaffold import openapi as scaffold_openapi
        cfg = load_config(args.config)
        thresholds = {**DEFAULT_THRESHOLDS, **(cfg.get("thresholds") or {})}
        prd = parse_prd(cfg["prd"], openapi_source=cfg.get("openapi"))
        golden = build_golden(cfg, prd)
        if args.check_only:
            return check_only(cfg, prd, golden)
        model = settings.get().gt_model
        gens = ("single", "agent") if args.generator == "both" else (args.generator,)
        agent_model = args.agent_model or settings.get().gt_agent_model
        if "agent" in gens and provider_of(agent_model) != "anthropic":
            raise EvalError("agent chỉ hỗ trợ model Claude (--agent-model / QC_GT_AGENT_MODEL)")
        if args.llm == "fake":
            if provider_of(model) == "gemini":
                model = "claude-sonnet-5"   # response giả có dạng Messages API
            if not os.environ.get("ANTHROPIC_API_KEY", "").strip():   # khoá rỗng (vd. từ .env) cũng phải thay
                os.environ["ANTHROPIC_API_KEY"] = "sk-ant-eval-fake"
        elif not args.skip_generate and not args.from_saved:
            if "single" in gens:
                free = provider_of(model) == "gemini" and args.price_in is None and args.price_out is None
                estimate = base.estimate_for(cfg["prd"], cfg.get("openapi"), args.runs, model, 0.0 if free else args.price_in, 0.0 if free else args.price_out)
                print(f"ƯỚC TÍNH ({model}): ~{estimate['input_tokens_per_run']} token vào + tối đa {estimate['output_tokens_per_run_max']} token ra mỗi lượt, "
                      f"{args.runs} lượt ≈ {'$0 (free tier, tốn quota RPM/RPD)' if free else '$%.2f' % estimate['usd_total']}. "
                      "PRD được gửi tới nhà cung cấp LLM (ghi egress trước khi gửi).", file=sys.stderr)
            if "agent" in gens:
                if not os.environ.get("QC_GT_AGENT_MAX_COST_USD", "").strip():
                    raise EvalError("chạy agent thật cần đặt tường minh QC_GT_AGENT_MAX_COST_USD (trần cứng USD cho MỘT lượt), vd 1.0 cho smoke call, 3.0 cho lượt đo")
                if args.max_total_usd is None or args.max_total_usd <= 0:
                    raise EvalError("chạy agent thật cần --max-total-usd (tổng ngân sách tối đa của cả lệnh)")
                cap = settings.get().gt_agent_max_cost_usd
                worst = cap * args.runs * sum(1 for g in gens if g == "agent")
                if worst > args.max_total_usd + 1e-9:
                    raise EvalError(f"trần ${cap:.2f}/lượt × {args.runs} lượt = ${worst:.2f} vượt --max-total-usd ${args.max_total_usd:.2f}: giảm --runs hoặc QC_GT_AGENT_MAX_COST_USD")
                print(f"ƯỚC TÍNH AGENT ({agent_model}): KHÔNG ước lượng được trước (số lượt tuỳ model); trần cứng ${cap:.2f}/lượt (QC_GT_AGENT_MAX_COST_USD) nên {args.runs} lượt ≤ ${cap * args.runs:.2f}. "
                      "PRD, OpenAPI và MÃ NGUỒN của SUT được gửi tới Anthropic (egress ghi trước khi gửi).", file=sys.stderr)
            if not args.yes:
                raise EvalError("--llm real cần --yes (xác nhận việc gửi PRD, và với agent cả mã nguồn, ra ngoài)")
        spec = scaffold_openapi.load(cfg["openapi"]) if cfg.get("openapi") else None
        analysis = scaffold_openapi.analyze(spec) if spec else None

        labels = {"single": model, "agent": agent_model}
        shown = " vs ".join(labels[g] for g in gens)
        llm_label = "skipped" if args.skip_generate else "saved" if args.from_saved else args.llm
        report: dict = {"name": cfg.get("name", Path(cfg["sut_root"]).name), "prd_id": prd.prd_id, "llm": llm_label,
                        "model": "—" if args.skip_generate else "(bộ đã lưu)" if args.from_saved else shown if args.llm == "real" else f"{shown} (fake)", "labeled_by": cfg.get("labeled_by"),
                        "thresholds": thresholds, "mutant_acs": {m["id"]: m["acs"] for m in cfg.get("mutants") or []}, "generator": "—" if args.skip_generate else args.generator}
        coverage = green = mutants = baseline = scorer_complete = generated_mutants = comparison = None
        keep_dir = args.keep_dir
        if keep_dir is None and args.llm == "real" and not args.skip_generate and not args.from_saved:
            keep_dir = ROOT / "runs" / time.strftime("eval-generated-%Y%m%dT%H%M%SZ", time.gmtime())   # lần chạy tốn tiền luôn để lại bộ vừa sinh
        if not args.skip_generate:
            measure_generated = (args.generator == "both" or args.generated_mutants) and not args.skip_mutants and bool(cfg.get("mutants"))
            aggregates: dict[str, dict] = {}
            for kind in gens:
                runs = []
                with egress_dir_for(keep_dir, kind) as egress:
                    for index in range(1, args.runs + 1):
                        if args.from_saved:
                            generated = load_saved(args.from_saved, kind, index)
                        elif kind == "single":
                            _, generated = generate_once(cfg, args.llm, args.fake_response, model, Path(egress))
                        else:
                            _, generated = generate_agent_once(cfg, args.llm, args.fake_script, agent_model, Path(egress), spec)
                        saved = save_generated(keep_dir, kind, index, generated)
                        if saved:
                            print(f"Đã lưu bộ vừa sinh ({kind}, lượt {index}) ở {saved} trước khi đo; nếu bước đo lỗi, đo lại miễn phí bằng --from-saved {keep_dir}", file=sys.stderr)
                        try:
                            measured = measure_generation(generated.catalog, golden, cfg, analysis)
                            measured["usage"] = {"input_tokens": generated.usage.input_tokens, "output_tokens": generated.usage.output_tokens}
                            measured["orphans"] = list(generated.orphans)
                            measured["scorer"] = measure_scorer(generated.catalog, spec)
                            if measure_generated:
                                measured["mutants_generated"] = measure_generated_mutants(generated.catalog, golden, cfg, analysis)
                        except Exception as error:  # noqa: BLE001 — bộ đã trả tiền và đã lưu: nói rõ cách đo lại thay vì chỉ báo lỗi
                            reason = str(error) if isinstance(error, EvalError) else type(error).__name__
                            where = f"bộ đã lưu ở {saved}; đo lại không tốn tiền: --from-saved {keep_dir}" if saved else "bộ vừa sinh chưa được lưu (dùng --keep-dir lần sau)"
                            raise EvalError(f"đo {kind} lượt {index} thất bại: {reason}. {where}") from None
                        if kind == "agent":
                            measured["agent"] = {key: generated.agent.get(key) for key in ("turns", "stop", "completed", "error_kind", "files_read", "bytes_read", "submissions", "dropped_in_loop",
                                                                                           "finish_rejections", "waivers", "spec_conflicts", "cost_usd_est", "duration_s", "techniques", "repo_map")}
                        runs.append(measured)
                aggregates[kind] = aggregate_runs(runs)
            primary = aggregates["agent"] if "agent" in aggregates else aggregates["single"]
            coverage, green = primary["ac_coverage"]["median"], primary["green_rate"]["median"]
            report["generation"], report["generators"] = primary, aggregates
            write_report({**report, "partial": "mới có phần sinh/đo bộ vừa sinh; chưa có (c) và kết luận"}, args.out_json)   # còn lại có thể hỏng (SUT/QA chưa duyệt): không để mất phần đã đo
            if args.generator == "agent":       # agent một mình: scorer 100% và kill rate trên bộ vừa sinh là điều kiện; `both` dồn hết vào `comparison`
                scorer_complete = primary["scorer"]["complete_runs"] == primary["scorer"]["runs"] if primary.get("scorer") else None
                generated_mutants = primary.get("mutants_generated")
            if args.generator == "both":
                comparison = report["comparison"] = compare(aggregates["single"], aggregates["agent"], {**thresholds, **(cfg.get("agent_thresholds") or {})})
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
        kind = getattr(error, "kind", None)   # GTError/LLMError: `kind` là từ khoá ngắn (bad_output, timeout, refused...), không chứa nội dung
        print(f"LỖI HỆ THỐNG: {type(error).__name__}" + (f" ({kind})" if isinstance(kind, str) and kind.isidentifier() else ""), file=sys.stderr)
        return 3

    report["verdict"] = verdict(thresholds, coverage=coverage, green=green, mutants=mutants, baseline_green=baseline,
                                scorer_complete=scorer_complete, generated_mutants=generated_mutants, comparison=comparison)
    write_report(report, args.out_json)
    sys.stdout.write(render_markdown(report))
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
