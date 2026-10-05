"""Kiểm khô mutant ($0, không LLM): với từng mutant của eval/vahan.yaml, so phản hồi của SUT sạch và SUT đã chèn lỗi trên MỘT request phân biệt.
Chứng minh lỗi có thật và không 5xx TRƯỚC khi tốn tiền gọi agent (`--check-only` chỉ bật SUT sạch, không áp mutant).

    .\\.venv\\Scripts\\python.exe eval\\vahan\\probe_mutants.py

Request dò viết tay từ PRD/mã SUT, không dùng test case của agent. Exit 0 khi mọi mutant có hiệu lực.
"""
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
import httpx  # noqa: E402
import eval_gt_sut as e  # noqa: E402

cfg = e.load_config(ROOT / "eval" / "vahan.yaml")
J = str(uuid.uuid4())
FILTERS = {"states": [], "rtos": [], "categoryGroups": [], "fuels": [], "yAxis": "Maker", "xAxis": "Month Wise"}
LOG = {"healthCheck": {"status": "PASS", "checkedAt": "2026-10-03T00:00:00Z"}, "pageUrl": "https://example.test"}
PROBES = {
    "M01-login-accepts-any-credentials": ("POST", "/api/auth/login", {"username": "x", "password": "y"}),
    "M02-me-returns-wrong-field": ("GET", "/api/auth/me", None),
    "M03-login-username-max-129": ("POST", "/api/auth/login", {"username": "a" * 129, "password": "p"}),
    "M04-runners-list-201": ("GET", "/api/runners", None),
    "M05-legacy-source-not-410": ("POST", "/api/jobs", {"runnerId": "r1", "filters": FILTERS, "source": "old"}),
    "M06-offline-runner-400": ("POST", "/api/jobs", {"runnerId": "r1", "filters": FILTERS}),
    "M07-unknown-job-400": ("GET", f"/api/jobs/{J}", None),
    "M07b-cancel": ("POST", f"/api/jobs/{J}/cancel", None),   # cùng mutant M07 (hai AC: 4.7 và 5.3)
    "M08-verify-missing-file-minus-one": ("POST", "/api/jobs/reports/verify", {"fileNames": ["nope.xlsx"]}),
    "M09-nodata-missing-410": ("GET", f"/api/jobs/{J}/no-data", None),
    "M10-report-file-missing-400": ("GET", "/api/jobs/reports/file/nope.xlsx", None),
    "M11-schedule-default-7": ("GET", "/api/ui-health/schedule", None),
    "M12-schedule-max-366": ("PUT", "/api/ui-health/schedule", {"intervalDays": 366}),
    "M13-runnow-unknown-runner-409": ("POST", "/api/ui-health/run-now", {"runnerId": "nope"}),
    "M14-log-created-200": ("POST", "/api/ui-health/logs", LOG),
    "M16-report-download-missing-410": ("GET", "/api/ui-health/reports/nope.csv/download", None),
}


def call(url, spec):
    method, path, body = spec
    r = httpx.request(method, url + path, json=body, timeout=20)
    return r.status_code, r.text.replace("\n", " ")[:90]


clean = {}
with e.sut_instance(cfg) as url:
    for name, spec in PROBES.items():
        clean[name] = call(url, spec)
bad = 0
print(f"{'probe':38} {'sạch':>5} {'lỗi':>5}  kết luận")
for mutant in cfg["mutants"]:
    mid = mutant["id"]
    names = [n for n in PROBES if n == mid or (n.startswith(mid[:3]) and n != mid and n[3] == "b")]
    if not names:
        print(f"{mid:38} KHÔNG CÓ request dò")
        bad += 1
        continue
    with e.sut_instance(cfg, mutant) as url:
        for name in names:
            got, base = call(url, PROBES[name]), clean[name]
            ok = got != base and got[0] < 500 and base[0] < 500
            bad += not ok
            print(f"{name:38} {base[0]:>5} {got[0]:>5}  {'OK khác sạch' if ok else 'KHÔNG KHÁC / 5xx'}   sạch={base[1]!r} lỗi={got[1]!r}")
print("\nTỔNG:", "mọi mutant có hiệu lực, không 5xx" if not bad else f"{bad} mutant có vấn đề")
sys.exit(1 if bad else 0)
