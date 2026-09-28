# Phase 1 · Làn A — Security (Semgrep · gitleaks · Trivy)

> Đọc kèm: [plan-B-integration.md](plan-B-integration.md) (làn của người còn lại) · [architecture.md](../architecture.md) · [core-rules.md](../core-rules.md)
> File này **tự đủ**: phần chung ở §3 và §5 giống hệt trong file của B.
> Không có deadline trong tài liệu này — chỉ có **thứ tự** và **điều kiện xong (DoD)** của từng việc.

---

## 1. Tóm tắt: What · Why · How · Giúp gì cho hệ thống

| | |
|---|---|
| **WHAT — làm cái gì** | Thêm **3 worker tất định** vào QC-Agent để quét bảo mật mỗi PR: **Semgrep** (lỗi trong mã nguồn — SAST), **gitleaks** (lộ secret/token trong mã), **Trivy** (thư viện phụ thuộc có lỗ hổng CVE). Kèm 2 việc phụ trợ thuộc làn này: script lọc credential khỏi file HAR (`tools/har_scrub.py`, để B dùng) và bước đăng review gắn đúng dòng code lên PR. |
| **WHY — vì sao cần** | Hôm nay gate chỉ chạy được **hợp đồng API** (Schemathesis). Khâu **Security** — một trong 4 khâu của kiến trúc ([architecture.md §2](../architecture.md)) — đang trống. Với sản phẩm đụng **credential vào trang B của chính phủ**, một PR làm rò token hoặc kéo vào thư viện có CVE mà gate vẫn xanh là lỗ hổng không chấp nhận được. Thiếu khâu này thì hệ thống chỉ là "contract test runner", chưa phải QC agent 4 khâu. |
| **HOW — làm bằng cách nào** | Mỗi worker = 1 manifest + 1 adapter (2 hàm `build_cmd`/`parse_output`, khung [_base.py](../../src/qc_agent/adapters/_base.py) lo phần còn lại) + 1 mẫu suite. Adapter **đếm** finding theo mức nghiêm trọng thành metric phẳng (`semgrep.high`, `trivy.critical`…), rồi dùng oracle `threshold` **đã có** để phán. Không LLM, không oracle mới, không đụng contract. Công cụ chạy **offline** trong image (rule vendored, DB CVE nướng sẵn) để kết quả tái lập được. |
| **GIÚP HỆ THỐNG** | (1) Khâu Security từ *Thiết kế* → *Đã chạy*, **có quyền chặn merge** ở mức high/critical. (2) Giữ đúng nguyên tắc §4.1: chặn merge chỉ dành cho kiểm tra **tất định** — chạy lại cho đúng kết quả cũ, giải thích được "vì sao PR của tôi bị chặn" bằng cách mở file suite ra. (3) Cung cấp `har_scrub` để làn B ghi bản ghi trang B mà không rò credential. (4) Ở tầm Agentic QC: đây là phần **cảm biến** (khả năng *quan sát*) — điều kiện để vòng ngoài về sau có gì để đọc và sinh test. |

**Không nằm trong làn này:** DAST (ZAP), rà quyền `manifest.json` của extension, quét container image — để Later ([architecture.md §5.4](../architecture.md)).

---

## 2. Nhìn hình trước

```
        PR của dev trên repo SUT (vahan-rpa)
                      │
        ┌─────────────┴─────────────────────────────┐
        │  CI gate → image qc-agent (đã ghim digest) │
        └─────────────┬─────────────────────────────┘
                      │  plan → resolve → chọn worker theo capability
     ┌────────────────┼───────────────────┐
     ▼                ▼                   ▼
 ┌─────────┐    ┌───────────┐       ┌──────────┐
 │ semgrep │    │ gitleaks  │       │  trivy   │        ← LÀN A làm 3 ô này
 │ code.sast│   │code.secret│       │deps.vuln │
 └────┬────┘    └─────┬─────┘       └────┬─────┘
      │ semgrep.json  │ gitleaks.json     │ trivy.json     (raw_output, làm evidence)
      ▼               ▼                   ▼
   ┌────────────────────────────────────────────┐
   │ ADAPTER: đếm theo mức  →  metrics phẳng     │
   │  semgrep.high=1  gitleaks.count=0  trivy.critical=0 …
   └───────────────────┬────────────────────────┘
                       ▼
   ┌────────────────────────────────────────────┐
   │ ORACLE threshold (ĐÃ CÓ SẴN)                │   ngưỡng nằm trong file suite:
   │  semgrep.high == 0 ?  → pass | fail         │   {metric: semgrep.high, op: '==', value: 0}
   └───────────────────┬────────────────────────┘
                       ▼
        verdict gate → PR đỏ/xanh  +  review gắn file:dòng (A-9)
```

**Ba điều cần nhớ nhất của làn này** (mỗi điều có lý do ở §6):

1. Adapter **đếm**, oracle **phán** — không viết logic "high thì đỏ" trong adapter.
2. Công cụ hỏng / thiếu DB / thiếu binary ⇒ **error hoặc skipped ⇒ đỏ**. Không bao giờ "không thấy gì ⇒ xanh".
3. `--error=false` / `--exit-code 0`: exit code của công cụ quét **không** phải là phán quyết. Phán quyết là của oracle.

---

## 3. Bước 0 — Hợp đồng chung (giống hệt nhau trong file của A và file của B)

> Mục đích duy nhất: để từ đây hai người **không phải sửa cùng một file**. Bước này chỉ dựng khung, không có logic, nên nhỏ.
> **Người A** mở PR `chore(phase1): chốt giao diện mở rộng` vào nhánh `phase1`, **người B** review và merge.
> Không ai bắt đầu làn của mình trước khi PR này merge.

### 3.1 Khai hết capability của cả 4 worker — một lần

Thêm vào [schemas/capabilities.json](../../schemas/capabilities.json) (file **dữ liệu**, không thuộc contract đóng băng — chính nó ghi chú như vậy):

```json
"code.sast":        "SAST trên source của SUT  (oracle: threshold)",
"code.secret":      "Quét secret trong source (+ lịch sử git nếu bật)  (oracle: threshold)",
"deps.vuln":        "CVE của dependency  (oracle: threshold)",
"flow.integration": "Chuỗi tích hợp qua runner giả / bản ghi HAR  (oracle: checks)"
```

Sau PR này **không ai được đụng file này nữa** trong Phase 1. Cần thêm capability → nhắn nhau.

### 3.2 Khoét sẵn vùng riêng trong các file dùng chung

| File dùng chung | Vùng của A | Vùng của B |
|---|---|---|
| [Dockerfile](../../Dockerfile) — phần stage ở đầu file (chỗ đang có `FROM ${K6_IMAGE} AS k6`) | `# ==== qc-agent:region security-stages (A) ====` … `# ==== qc-agent:end ====` | `# ==== qc-agent:region integration-stages (B) ====` … `# ==== qc-agent:end ====` |
| [Dockerfile](../../Dockerfile) — stage runtime (sau dòng `COPY --from=k6 /usr/bin/k6 ...`) | `# ==== qc-agent:region security-runtime (A) ====` | `# ==== qc-agent:region integration-runtime (B) ====` |
| [qc-gate.reusable.yml](../../.github/workflows/qc-gate.reusable.yml) — ngay sau bước `Run qc-agent gate` | `# ==== qc-agent:region security-review (A) ====` | *(B không sửa workflow)* |
| [scaffold/init.py](../../src/qc_agent/scaffold/init.py) — ngay sau chỗ sinh `api-contract`/`perf-smoke` (khoảng dòng 187–207) | một lời gọi `suites_security.add_suites(add, ...)` | một lời gọi `suites_integration.add_suites(add, ...)` |

Hai module mới, mỗi người một file, **ban đầu là hàm rỗng**:

- `src/qc_agent/scaffold/suites_security.py` (A) — `def add_suites(add, opts) -> None: ...`
- `src/qc_agent/scaffold/suites_integration.py` (B) — `def add_suites(add, opts) -> None: ...`

`add(path, content)` là hàm mà `init.py` đang dùng để ghi file suite (ví dụ `add(f"{SUITES_DIR}/ui-explore.yaml", ...)`). Tách module riêng để hai người **không cùng sửa** [templates.py](../../src/qc_agent/scaffold/templates.py) và `init.py` sau bước này. Chữ ký `(add, opts)` là dự đoán từ cách `init.py` đang gọi — người A chỉnh cho khớp khi dựng khung.

Kiểm tra sau khi dựng khung: `pytest` toàn bộ xanh, `docker build .` xanh (vùng rỗng thì không đổi gì).

### 3.3 Quy ước đặt tên (ghi thành ~10 dòng vào [docs/core-rules.md](../core-rules.md))

| Thứ | Quy ước | Ví dụ |
|---|---|---|
| Metric worker đếm | `<worker>.<mức>` và `<worker>.total`, mức ∈ `critical/high/medium/low` | `semgrep.high`, `trivy.critical` |
| Check của worker luồng | `snake_case` theo bước nghiệp vụ | `filter_applied`, `report_downloaded` |
| Tên suite | một từ, trùng tên file `.qc-agent/suites/<tên>.yaml` và tên trong policy | `sast`, `secrets`, `deps`, `integration` |
| `task_id` | A dùng `t-010…t-019`, B dùng `t-020…t-029` | không bao giờ trùng |
| Nhánh | `feat/<worker>-<việc>`, một PR một việc, ≤ ~400 dòng | `feat/worker-semgrep` |

### 3.4 Những thứ đã chốt — không bàn lại giữa chừng

1. **Chưa bật** suite mới trong `blocking_suites` cho tới [Bước cuối](#5-bước-cuối--b-ghép-rồi-bàn-giao-để-test-trên-vahan). Trong lúc phát triển, chạy bằng `--suites <tên>` hoặc `--plan`.
2. **Ngưỡng chặn nằm trong file suite** (oracle `threshold` / `checks`), không nằm trong code adapter. Đổi ngưỡng = sửa một dòng YAML.
3. **Không thêm `oracle.kind` mới.** Cả 4 worker dùng `threshold` hoặc `checks` đã có ([oracle/](../../src/qc_agent/oracle/)).
4. **Không sửa contract đóng băng** ([schemas/CONTRACT.lock](../../schemas/CONTRACT.lock), `task_spec.json`, `result.json`). Ràng buộc của nó ảnh hưởng thiết kế — xem §3.5.
5. **Adapter được MẤT thông tin, không được BỊA thông tin** ([adapters/_base.py](../../src/qc_agent/adapters/_base.py)). Không đọc được output → `raise AdapterParseError` → `error` (đỏ nhãn hạ tầng). Không có nhánh nào đổi lỗi thành `pass`.

### 3.5 Ba ràng buộc của contract mà cả hai phải biết (đã đọc schema)

| Ràng buộc | Nguồn | Hệ quả |
|---|---|---|
| `findings[]` có `additionalProperties: false`, **không có trường `location`** | [schemas/result.json](../../schemas/result.json) | Vị trí file:dòng không đi vào finding. Đặt vào `title` (`rule @ path:line`); dữ liệu đầy đủ nằm ở file `raw_output` (evidence) |
| `severity_hint` chỉ nhận `low / medium / high / null` — **không có `critical`** | cùng file | `critical` map về `high` ở finding; nhưng **metric** vẫn đếm riêng `xxx.critical` để suite chặn được theo mức |
| `target` bắt buộc có `kind` **và** `base_url`, kể cả task quét mã tĩnh. `retry.max` ≤ 1, `retry.on` chỉ nhận `error`. `evidence_required` chỉ nhận `raw_output/stdout/metrics/screenshot/trace` | [schemas/task_spec.json](../../schemas/task_spec.json) | Task SAST/secret/deps vẫn khai `base_url: ${env.APP_BASE_URL}`; adapter bỏ qua nó |

### 3.6 Bảng sở hữu file — không giao nhau một dòng nào

| | **A — Security** | **B — Integration** |
|---|---|---|
| `workers/` | `semgrep.yaml` `gitleaks.yaml` `trivy.yaml` | `playwright.yaml` |
| `adapters/` | `semgrep_adapter.py` `gitleaks_adapter.py` `trivy_adapter.py` | `playwright_adapter.py` |
| `scaffold/` | `suites_security.py` + `tmpl/sast.yaml.tmpl` `secrets.yaml.tmpl` `deps.yaml.tmpl` | `suites_integration.py` + `tmpl/integration.yaml.tmpl` + `tmpl/integration-support.mjs.tmpl` |
| `tests/` | `test_semgrep_adapter.py` `test_gitleaks_adapter.py` `test_trivy_adapter.py` `test_har_scrub.py` `test_security_review.py` `test_doctor.py` | `test_playwright_adapter.py` `test_integration_guard.py` `test_scaffold_integration.py` |
| `tests/fixtures/` | `security/` | `integration/` (kể cả `.har`) |
| Khác | `tools/har_scrub.py` · `integrations/security_review.py` · `rules/semgrep/` · `pyproject.toml` + `uv.lock` · `cli.py` (lệnh `doctor`) | `package.json` + `package-lock.json` · thư mục mẫu `.qc-agent/integration/` |
| `docs/` | [architecture.md](../architecture.md) §2.4 và **dòng Security** trong bảng §2 | [architecture.md](../architecture.md) §2.3 và **dòng Integration** trong bảng §2 |

`architecture.md` là file duy nhất hai người cùng sửa, nhưng **khác dòng, khác mục** nên git tự gộp. Nếu vẫn conflict, người merge sau rebase.

### 3.7 Quy tắc làm việc

1. **Không sửa file ngoài cột của mình.** Cần một thay đổi ở đó → nhắn chủ sở hữu.
2. **Rebase `phase1` mỗi ngày** trước khi làm tiếp.
3. **Người kia review PR trong ngày.** Đây là lúc duy nhất hai người đọc code của nhau; nó thay thế mọi cuộc họp tiến độ.
4. Chỉ có **hai lúc cần trao đổi trực tiếp**, đều có điều kiện kích hoạt rõ ràng chứ không theo lịch:
   - A xong `tools/har_scrub.py` → nhắn B (chữ ký hàm + một ví dụ chạy).
   - B chốt được tên `check` thật của tier 1/2 → nhắn A để docs không lệch tên.

---

## 4. Làn A — danh sách việc theo thứ tự phụ thuộc

Sơ đồ phụ thuộc (mũi tên = "phải xong trước"). Các việc **cùng cột** độc lập nhau, làm thứ tự nào cũng được:

```
 Bước 0 (chung) ──► A-1 khảo sát ──┬──► A-2 semgrep ──┐
                                   ├──► A-3 gitleaks ─┼──► A-7 ca âm tính ──► A-8 docs
                                   ├──► A-4 trivy ────┤
                                   │                  └──► A-9 review gắn dòng code
                                   ├──► A-5 har_scrub   ← B đang chờ cái này: làm sớm
                                   └──► A-6 lệnh doctor
                                                        ──► Bước cuối (B ghép, §5)
```

**Khuôn chung cho một worker** — mỗi worker (A-2, A-3, A-4) đi qua đúng 7 bước này, theo thứ tự:

| # | Bước | File |
|---|---|---|
| 1 | Chạy công cụ **thật** trong image một lần, lưu output JSON thật làm fixture | `tests/fixtures/security/<tool>-*.json` |
| 2 | Manifest | `workers/<tool>.yaml` |
| 3 | Adapter (`build_cmd`, `parse_output`) | `adapters/<tool>_adapter.py` |
| 4 | Test adapter bằng fixture (không cần cài công cụ) | `tests/test_<tool>_adapter.py` |
| 5 | Cài công cụ vào image (vùng Dockerfile của A) + `version_probe` chạy được | `Dockerfile` |
| 6 | Mẫu suite + đăng ký trong `suites_security.py` | `scaffold/tmpl/<suite>.yaml.tmpl` |
| 7 | Chạy thật: `qc-agent run --plan ...` với image, xem report | — |

Bước 1 làm **đầu tiên** vì fixture giả do chính mình viết sẽ chỉ chứng minh adapter đọc được thứ mình *tưởng* công cụ in ra.

---

### A-1 · Khảo sát và chốt 3 quyết định (đọc, không code)

**Phụ thuộc:** Bước 0. **Kết quả:** một đoạn ghi ở đầu PR đầu tiên của làn A.

Ba câu phải trả lời bằng cách **đọc code**, không đoán:

1. **Workspace trong gate có `.git` đầy đủ không?** Đọc bước checkout trong [qc-gate.reusable.yml](../../.github/workflows/qc-gate.reusable.yml). Nếu checkout nông (`fetch-depth: 1`), gitleaks chỉ quét được working tree, không quét được lịch sử. Quyết định: chỉ quét working tree (mặc định — rẻ, đủ để bắt secret **mới** đưa vào PR này), hay đổi checkout sang `fetch-depth: 0`.
   *Khuyến nghị:* working tree + thêm input `history: false|true` trong adapter để bật sau.
2. **Container qc-agent trong gate có ra internet được không?** Đọc bước `Run qc-agent gate` (chỗ `docker run … --network …`) và [core/egress.py](../../src/qc_agent/core/egress.py). Lưu ý `egress.py` chỉ **ghi lại theo khai báo** trong `data_egress`, không quan sát mạng và không chặn. Câu trả lời quyết định có dựa vào mạng runtime được không. *Khuyến nghị dù câu trả lời là gì:* chạy **offline** (xem quyết định 3).
3. **Nguồn rule/DB:** chốt **vendor rule Semgrep vào repo** (`rules/semgrep/`) và **nướng DB CVE của Trivy vào image lúc build**. Lý do ở §6.2.

Ghi thêm một dòng vào PR: `target.base_url` bắt buộc cho mọi task (xem §3.5) nên mẫu suite của A luôn khai `base_url: ${env.APP_BASE_URL}`.

**DoD:** 3 câu có đáp án, kèm dòng code bằng chứng (`file:dòng`).

---

### A-2 · Worker Semgrep (`code.sast`)

**Phụ thuộc:** A-1. Độc lập với A-3, A-4.

**Bước 1 — lấy fixture thật.** Trong image (hoặc máy có semgrep), quét một thư mục nhỏ có sẵn 1–2 lỗi rõ ràng:

```bash
semgrep scan --json --output semgrep-sample.json --metrics=off --error=false \
  --config rules/semgrep  tests/fixtures/scan/vahan-rpa
```

Lưu `semgrep-sample.json` (có finding) và `semgrep-empty.json` (quét thư mục sạch) vào `tests/fixtures/security/`. Cờ CLI ở trên là **`[EXTERNAL GAP]`** — kiểm lại đúng theo phiên bản semgrep bạn ghim.

**Bước 2 — manifest** `workers/semgrep.yaml` (khuôn: [workers/k6.yaml](../../workers/k6.yaml)):

```yaml
name: semgrep
version_probe: "semgrep --version"
adapter: "qc_agent/adapters/semgrep_adapter.py"
lanes: [gate]
capabilities:
  - id: code.sast
    oracle_kinds: [threshold]
    verdict_sources: [deterministic_assert]
    parallel_safe: true        # chỉ đọc file → chạy song song với worker khác được
requires:
  env: []
  binaries: [semgrep]
data_egress: [source_code]     # khai thật: mã nguồn được đưa vào công cụ. Chạy offline nên không rời máy
cost_profile: { tokens_per_run: 0, typical_wallclock_s: 120 }
```

**Bước 3 — adapter** (`adapters/semgrep_adapter.py`). Khuôn: [k6_adapter.py](../../src/qc_agent/adapters/k6_adapter.py).

```python
SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}   # tên trường JSON: [EXTERNAL GAP], kiểm bằng fixture
LEVELS = ("critical", "high", "medium", "low")

class SemgrepAdapter(Adapter):
    NAME = "semgrep"
    ADAPTER_VERSION = "0.1.0"

    def build_cmd(self, spec, workdir):
        inputs = spec.get("inputs", {})
        rules = inputs.get("rules_dir")
        if not isinstance(rules, str) or not rules.strip():
            raise AdapterParseError("inputs.rules_dir phải là đường dẫn không rỗng")
        paths = inputs.get("paths") or ["."]
        self._out = (workdir / "semgrep.json").resolve()
        self._out.unlink(missing_ok=True)            # tránh đọc nhầm file của lần chạy trước
        cmd = ["semgrep", "scan", "--json", f"--output={self._out}",
               "--metrics=off", "--error=false", "--config", rules]
        cmd += [f"--exclude={p}" for p in inputs.get("exclude", [])]
        return cmd + list(paths)

    def parse_output(self, proc, workdir, spec):
        if not self._out.is_file():
            raise AdapterParseError("semgrep không ghi ra file JSON")
        data = json.loads(self._out.read_text(encoding="utf-8"))
        if data.get("errors"):                       # rule lỗi / file không parse được = không quét đủ ⇒ KHÔNG được xanh
            raise AdapterParseError(f"semgrep báo {len(data['errors'])} lỗi khi quét")
        counts = {f"semgrep.{lv}": 0 for lv in LEVELS}
        findings = []
        for r in data.get("results", []):
            sev = SEVERITY.get((r.get("extra") or {}).get("severity"), "low")
            counts[f"semgrep.{sev}"] += 1
            where = f'{r["path"]}:{r["start"]["line"]}'
            findings.append({
                "finding_id": f'f-semgrep-{hashlib.sha1((r["check_id"] + where).encode()).hexdigest()[:12]}',
                "title": f'{r["check_id"]} @ {where}',          # vị trí nằm trong title (§3.5)
                "detected_by": "semgrep",
                "verdict_source": "deterministic_assert",
                "confidence": None,
                "severity_hint": sev,
            })
        counts["semgrep.total"] = sum(counts[f"semgrep.{lv}"] for lv in LEVELS)
        return ParsedOutput(metrics=counts, findings=findings,
                            evidence_paths=[("raw_output", self._out)], tokens=0, usd=0.0,
                            exit_code=proc.returncode, replay_cmd=shlex.join(map(str, proc.args)))
```

Ba chỗ dễ sai (đều có test ở bước 4):

- Semgrep có `errors[]` (rule hỏng, file không parse được) **mà vẫn exit 0**. Coi như "không finding" là xanh giả → phải ném `AdapterParseError`.
- `finding_id` phải **duy nhất** trong một result và **ổn định** giữa các lần chạy (dùng hash của `rule+path:line`, không dùng số thứ tự).
- Không đưa nội dung đoạn mã (`extra.lines`) vào `title`: nội dung mã do người gửi PR kiểm soát, sẽ đi vào comment PR.

**Bước 4 — test** (`tests/test_semgrep_adapter.py`), tối thiểu:

| Test | Kỳ vọng |
|---|---|
| fixture có 1 finding ERROR | `metrics["semgrep.high"] == 1`, `severity_hint == "high"`, `title` chứa `path:line` |
| fixture sạch | mọi metric `== 0` **và** có key đủ (oracle cần key tồn tại) |
| fixture có `errors: [...]` | `AdapterParseError` |
| file output không tồn tại | `AdapterParseError`, không phải pass |
| chạy hai lần cùng fixture | `finding_id` giống hệt nhau |
| `paths`/`exclude` chứa ký tự lạ | không thoát khỏi workdir, không chèn được cờ (mỗi phần tử là **một** argv, không qua shell) |

**Bước 5 — image.** Semgrep là Python: thêm vào `pyproject.toml` rồi `uv lock` (chỉ A đụng hai file này). Copy `rules/semgrep/` vào `/opt/qc-rules/semgrep` trong vùng `security-runtime`. Kiểm: `docker run --rm --entrypoint semgrep <image> --version` chạy được bằng user `qc` (không root).

**Bước 6 — mẫu suite** `scaffold/tmpl/sast.yaml.tmpl` (bám cấu trúc [api-contract.yaml.tmpl](../../src/qc_agent/scaffold/tmpl/api-contract.yaml.tmpl)):

```yaml
# qc-agent:generated — suite SAST. Chặn merge hay không do POLICY của qc-agent quyết định, không do file này.
suite: sast
description: SAST bằng Semgrep, rule ghim trong image (chặn merge ở mode pr)
tasks:
- task_id: t-010
  capability: code.sast
  lane: gate
  intent: 'SAST: không có finding mức high trong mã nguồn'
  target:
    kind: source_tree
    base_url: ${env.APP_BASE_URL}       # bắt buộc bởi schema; adapter bỏ qua
  inputs:
    rules_dir: /opt/qc-rules/semgrep
    paths: {{scan_paths}}
    exclude: [node_modules, tests, dist, build]
  oracle:
    kind: threshold
    assertions:
    - {metric: semgrep.high, op: '==', value: 0}
  expected_result_kind: verdict
  budget: {wallclock_s: 300, tokens: 0, usd: 0}
  determinism: {seed: 0, replayable: true}
  evidence_required: [raw_output, stdout]
  retry: {max: 1, 'on': [error]}
  prefer: [semgrep]
```

`{{scan_paths}}` là chỗ trống do `suites_security.py` điền (danh sách thư mục nguồn, lấy từ scanner [scan.py](../../src/qc_agent/scaffold/scan.py) nếu tái dùng được, nếu không thì `["."]` kèm dấu `qc-agent:todo VERIFY:` để người xác nhận). Giá trị chèn vào mẫu phải đi qua bộ làm sạch như các hàm trong [templates.py](../../src/qc_agent/scaffold/templates.py) (JSON-quote) — đừng nối chuỗi thô.

**Bước 7 — chạy thật** trên bản copy vahan-rpa; ghi lại thời gian chạy và số finding vào PR.

**DoD A-2:** `pytest tests/test_semgrep_adapter.py` xanh · `qc-agent init` trên fixture sinh ra `.qc-agent/suites/sast.yaml` hợp lệ (`qc-agent validate` qua) · chạy thật trong image ra report đúng.

---

### A-3 · Worker gitleaks (`code.secret`)

**Phụ thuộc:** A-1. Độc lập với A-2, A-4. **Sao chép khuôn A-2**, chỉ khác những điểm dưới.

| Điểm | Giá trị |
|---|---|
| Lệnh | `gitleaks detect --source . --report-format json --report-path <f> --exit-code 0 --redact --no-banner` (+ `--no-git` khi `history: false`). `[EXTERNAL GAP]` — kiểm cờ theo version ghim |
| capability | `code.secret` |
| Metric | chỉ cần `gitleaks.count` (secret không có "mức": một cái cũng là quá nhiều) |
| Oracle trong suite | `{metric: gitleaks.count, op: '==', value: 0}` |
| `task_id` | `t-011` |
| Bỏ qua có kiểm soát | dùng cơ chế **sẵn của gitleaks** (`.gitleaksignore` theo fingerprint). Nó nằm trong repo SUT nên mọi lần thêm dòng bỏ qua đều hiện trong diff của PR để người review thấy |

🚩 **`--redact` là bắt buộc, không phải tuỳ chọn.** Báo cáo mặc định của gitleaks chứa **chính giá trị secret**. File đó sẽ trở thành evidence `raw_output`, đi vào artifact CI và có thể đi lên API của service qua ingest. Không có `--redact` thì gate quét secret **tự tạo ra** chỗ rò secret. Test bắt buộc:

- fixture mà secret có mặt ⇒ khẳng định **không** có chuỗi secret nào trong `title`, `findings`, `metrics`;
- adapter **từ chối chạy** nếu tự phát hiện output còn trường `Secret`/`Match` khác rỗng/`REDACTED` (một dòng kiểm trong `parse_output`, ném `AdapterParseError`).

Finding: `title = f'{RuleID} @ {File}:{StartLine}'`, `severity_hint = "high"` (cố định — gitleaks không có mức).

**DoD A-3:** như A-2, cộng test "không rò secret" xanh.

---

### A-4 · Worker Trivy (`deps.vuln`)

**Phụ thuộc:** A-1. Độc lập với A-2, A-3. Đây là worker nhiều bẫy nhất của làn.

| Điểm | Giá trị |
|---|---|
| Lệnh | `trivy fs --scanners vuln --skip-db-update --offline-scan --cache-dir /opt/trivy-cache --format json --output <f> --exit-code 0 <path>` `[EXTERNAL GAP]` |
| capability | `deps.vuln` |
| Metric | `trivy.critical`, `trivy.high`, `trivy.medium`, `trivy.low`, `trivy.total`, và **`trivy.db_age_days`** |
| `task_id` | `t-012` |
| Bỏ qua có kiểm soát | `.trivyignore` (cơ chế sẵn có) |
| `severity_hint` | `CRITICAL`/`HIGH` → `high`, `MEDIUM` → `medium`, `LOW` → `low` (schema không có `critical`, §3.5) |

**Ba bẫy phải xử lý trong chính worker này:**

1. **DB CVE.** Nướng vào image lúc build (`trivy image --download-db-only --cache-dir /opt/trivy-cache` trong vùng `security-runtime` — `[EXTERNAL GAP]` kiểm cờ). Runtime chạy `--skip-db-update`. DB không có / hỏng ⇒ Trivy có thể vẫn in "0 vuln" ⇒ **phải ném `AdapterParseError`** khi không xác định được phiên bản DB.
2. **DB cũ = xanh giả từ từ.** Image cũ 3 tháng có DB cũ 3 tháng; gate xanh trong khi CVE mới đã công bố. Đo tuổi DB (từ `trivy version --format json` — `[EXTERNAL GAP]` kiểm tên trường), đưa vào metric `trivy.db_age_days`, và để **suite** chặn ngưỡng:
   ```yaml
   assertions:
   - {metric: trivy.critical,    op: '==', value: 0}
   - {metric: trivy.high,        op: '==', value: 0}
   - {metric: trivy.db_age_days, op: '<=', value: 14}   # image quá cũ ⇒ đỏ, buộc phải build lại
   ```
   Nhờ vậy độ cũ của dữ liệu là một dòng YAML ai cũng đọc được, không phải chuyện ngầm.
3. **Không có lockfile.** Repo không có file dependency nào → Trivy in rỗng → "0 CVE". Adapter phải phân biệt "không tìm thấy manifest nào để quét" (ném lỗi rõ ràng, hoặc metric `trivy.targets = 0` để suite chặn) với "quét rồi và sạch".

**Test bắt buộc:** fixture có CVE critical · fixture sạch · fixture thiếu trường phiên bản DB (→ error) · DB cũ hơn ngưỡng (→ oracle fail chứ không phải error, vì đây là kết quả đo hợp lệ) · không có target nào (→ error).

**DoD A-4:** như A-2, cộng ba bẫy có test.

---

### A-5 · `tools/har_scrub.py` — lọc credential khỏi HAR (B đang chờ)

**Phụ thuộc:** Bước 0 thôi — không phụ thuộc A-2..A-4. **Nên làm sớm** trong làn A vì làn B cần nó để commit bản ghi trang B an toàn. Trong lúc chờ, B dùng HAR tự viết tay (synthetic) nên không bị chặn cứng.

**Vì sao thuộc làn A:** đây là hàm thuần `dict → dict` có fixture riêng, đúng kiểu việc kiểm-soát-rò-rỉ của Security, và cắt nó ra khỏi B là cách cân tải.

**Chữ ký cố định (B sẽ import/gọi theo đúng chữ ký này):**

```python
# tools/har_scrub.py
def scrub(har: dict, *, extra_redact_keys: tuple[str, ...] = ()) -> dict:
    """Trả HAR mới, đã thay giá trị nhạy cảm bằng 'REDACTED'. Không sửa `har` đầu vào. Cấu trúc HAR giữ nguyên hợp lệ."""

def find_leaks(har: dict) -> list[str]:
    """Danh sách mô tả chỗ còn sót (không chứa giá trị). Rỗng = sạch."""

# CLI:  python tools/har_scrub.py IN.har OUT.har     → exit 1 nếu find_leaks(OUT) không rỗng (fail closed)
```

**Phải lọc:**

| Chỗ trong HAR | Lọc gì |
|---|---|
| `request.headers`, `response.headers` | `Authorization`, `Cookie`, `Set-Cookie`, `Proxy-Authorization`, mọi header khớp `x-.*(token\|key\|secret\|auth).*` |
| `request.cookies`, `response.cookies` | toàn bộ `value` |
| `request.queryString`, URL | tham số tên `token`, `key`, `session`, `sid`, `auth`, `code`… (cả trong `url` lẫn mảng `queryString` — phải sửa **cả hai** cho khớp) |
| `request.postData` | trường tên `password`, `pass`, `token`, `secret`, `otp`, `captcha`, `csrf`… (cả dạng JSON lẫn form) |
| `response.content.text` | JWT (`eyJ…` ba đoạn), `Bearer …`, và các trường JSON cùng tên như trên |

**Fail closed:** sau khi lọc, `find_leaks` quét lại bằng các mẫu nhận diện; còn sót ⇒ CLI thoát mã 1 và **không ghi file đầu ra**. Ghi tay một script lọc mà không có bước kiểm ngược thì chỉ chứng minh bạn nhớ được những chỗ bạn đã nghĩ tới.

**Test** (`tests/test_har_scrub.py`): HAR mẫu chứa từng loại trên → sau `scrub` không còn giá trị gốc · cấu trúc còn hợp lệ (mở được bằng `json.loads`, đủ `log.entries[].request/response`) · idempotent (`scrub(scrub(x)) == scrub(x)`) · `find_leaks` phát hiện được khi tự chèn lại một JWT · đầu vào không bị sửa tại chỗ.

**Bàn giao cho B:** khi xong, nhắn B chữ ký hàm + một lệnh ví dụ chạy được.

⚠ **Câu hỏi mở (không phải của làn A quyết, nhưng A phải nêu ra):** `response.content.text` của HAR trang B có thể chứa **dữ liệu cá nhân thật** (thông tin phương tiện/chủ xe trên hệ thống chính phủ). Lọc credential không lọc dữ liệu cá nhân. Ghi vào PR: bản ghi chỉ được lấy bằng **tài khoản thử nghiệm** hoặc dữ liệu đã ẩn danh, và cần mentor/owner xác nhận trước khi commit HAR thật vào repo. Xem thêm §6.5.

**DoD A-5:** test xanh · CLI chạy được trên HAR thật của B (B đưa một file thô để thử) · B đã nhận chữ ký.

---

### A-6 · Lệnh `qc-agent doctor`

**Phụ thuộc:** Bước 0. Độc lập hoàn toàn với A-2..A-5.

**Vì sao:** repo hiện **chưa có** lệnh này. [cli.py](../../src/qc_agent/core/cli.py) chỉ có `run`/`init`/`validate`/`user`/`token`. Nghiệm thu cần một lệnh trả lời "image này có đủ công cụ cho mọi worker không" mà không phải chạy cả gate.

**Làm gì:** thêm một nhánh `argv[0] == "doctor"` cạnh `init`/`validate` trong `main()`, gọi hàm mới `qc_agent/core/doctor.py`. Đọc [registry.py](../../src/qc_agent/core/registry.py) để tìm hàm nạp manifest và hàm probe đã có (đừng viết lại logic probe). Đầu ra:

```
worker      version        probe   lanes
semgrep     1.x.x          ✅
gitleaks    8.x.x          ✅
trivy       0.x.x          ✅      (db: 2 ngày)
playwright  1.x.x          ❌      không tìm thấy 'playwright' trong PATH
```

Exit 0 nếu mọi worker probe OK; exit 1 nếu có cái hỏng. Không gọi mạng, không chạy gate.

**Test:** dùng `--workers-dir` trỏ tới manifest giả (có sẵn kiểu này trong [tests/fake_worker.py](../../tests/fake_worker.py)) — một cái probe OK, một cái hỏng ⇒ đúng exit code và đúng dòng.

**DoD A-6:** `docker run --rm <image> doctor` in bảng đúng, exit code đúng.

---

### A-7 · Các ca âm tính (chỗ phân biệt gate thật với gate trang trí)

**Phụ thuộc:** A-2, A-3, A-4.

Viết thành test tự động, không để "kiểm tay một lần". Mỗi ca dùng `--workers-dir`/fixture để dựng, không cần mạng.

| Ca | Dựng thế nào | Kỳ vọng | Vì sao quan trọng |
|---|---|---|---|
| Thiếu binary | manifest `requires.binaries` trỏ tới lệnh không tồn tại | task `skipped`; với `on_skipped_gate_task: fail` ⇒ gate **FAIL** | Nếu chỉ YELLOW thì gỡ Trivy khỏi image là cách "tắt gate" mà không ai biết |
| Công cụ chết | script giả thoát mã 137, không ghi file | `status=error`, gate đỏ, rationale bắt đầu `parse:` | Không có nhánh nào biến crash thành pass |
| Output rỗng nhưng hợp lệ | fixture JSON `{"results": []}` | pass, **và** mọi metric có mặt bằng 0 | Phân biệt "sạch" với "không đo được" |
| Output hỏng | JSON cụt | `error` | như trên |
| Vượt ngưỡng | fixture 1 finding high | `fail`, finding có `path:line` | Đường vui phải đỏ đúng |
| Secret không rò | xem A-3 | không có chuỗi secret trong result | |
| Đường dẫn hiểm | `paths: ["../../etc"]` | bị từ chối hoặc không thoát khỏi SUT root | Input của suite đến từ repo SUT = không tin cậy |

Dùng [tests/fake_adapter.py](../../tests/fake_adapter.py) / [test_base.py](../../tests/test_base.py) làm mẫu cách dựng.

**DoD A-7:** cả bảng là test tự động, chạy trong `pytest` mặc định.

---

### A-8 · Cập nhật tài liệu

**Phụ thuộc:** A-2..A-4 (biết chắc cái gì đã chạy).

- [architecture.md](../architecture.md) **§2.4 Security**: thay khối `> [T5]` bằng nội dung thật — 3 công cụ, ngưỡng chặn, cơ chế bỏ qua (`.gitleaksignore`/`.trivyignore`/`# nosemgrep`), giới hạn (§6.4). Trong bảng §2, sửa **dòng Security**: cột Công cụ bỏ dấu ngoặc quanh Semgrep/gitleaks/Trivy, cột Trạng thái ghi *Đã chạy* chỉ khi Bước cuối (§5) đã xong.
- [core-rules.md](../core-rules.md): ~10 dòng quy ước tên (§3.3).
- [usage-ci.md](../usage-ci.md): một mục "cách bỏ qua một finding có lý do".
- `docs/onboarding.md`: nếu có bước liệt kê suite `init` sinh ra, thêm 3 suite mới.

**DoD A-8:** người chưa đọc code đọc §2.4 hiểu được PR bị chặn vì sao và bỏ qua thế nào.

---

### A-9 · Review gắn đúng dòng code (`integrations/security_review.py`)

**Phụ thuộc:** A-2 và A-3 (cần biết định dạng `raw_output` thật). Việc này là phần "comment chỉ đúng dòng code" của kịch bản nghiệm thu.

**Vì sao là việc riêng:** finding trong `result.json` **không có trường vị trí** (§3.5) nên `render_summary` trong [github.py](../../src/qc_agent/integrations/github.py) không thể tự gắn dòng. Vị trí đầy đủ nằm trong file evidence `semgrep.json`/`gitleaks.json`.

**Thiết kế:**

```
run_dir/…/semgrep.json ─┐
run_dir/…/gitleaks.json ┼─► security_review.py ──► MỘT review, mỗi finding một comment inline
PR diff (GitHub API)  ──┘            │
                                     └─ finding nằm NGOÀI diff ⇒ chỉ liệt kê trong phần thân review
```

- **Tái dùng, đừng viết lại:** [refine_review.py](../../src/qc_agent/integrations/refine_review.py) đã có `pr_diff_lines` (các dòng nằm trong diff), `already_posted` (chống đăng lặp bằng marker+hash), `build_comments`, và cách xử lý PR từ fork (token chỉ-đọc ⇒ chỉ cảnh báo). Import các hàm đó; nếu cần đổi chữ ký, **nhắn chủ sở hữu file đó** thay vì sửa lén (file đó không nằm trong bảng sở hữu của cả A lẫn B — cứ giữ nguyên, bọc bên ngoài).
- **Giới hạn của GitHub `[EXTERNAL GAP]`:** comment inline chỉ gắn được vào dòng **nằm trong diff**. Đây là lý do kịch bản nghiệm thu ghi "với điều kiện dòng nằm trong diff". Finding ở dòng cũ (không đổi trong PR) chỉ nằm trong thân review — vẫn hiện, không mất.
- **Trivy không có dòng:** lỗ hổng thư viện gắn với file lockfile, thường không có số dòng đáng tin. Trivy chỉ vào thân review, không inline.
- **Nội dung không tin cậy:** `rule id`, `message`, `path` đều xuất phát từ công cụ chạy trên mã của PR ⇒ đi qua `github.clean_md()` trước khi vào Markdown. **Tuyệt đối không** đưa đoạn mã hay `Secret` vào comment.
- **Chỉ ghi nhận:** không đổi verdict/exit code (giống mọi module `integrations/`). Lỗi đăng ⇒ cảnh báo, gate vẫn đúng theo exit code.
- **Chống spam:** marker `<!-- qc-agent:security sha256=<hash của danh sách finding> -->`; push thêm commit mà finding không đổi ⇒ không đăng lại.
- **Nối vào workflow:** một bước trong vùng `security-review (A)` sau bước `Run qc-agent gate`, `if: always()`, `continue-on-error: true` — bước này **chỉ báo cáo**, nên hỏng không được làm đổi kết quả (khác với bước dò nợ ở Phase 2, vốn không được `continue-on-error`). Chạy nó bằng cùng cách bước refine đang gọi image (`docker run … --entrypoint python "$IMAGE" -m qc_agent.integrations.…`, xem [dòng 282–292](../../.github/workflows/qc-gate.reusable.yml)).

**Test** (`tests/test_security_review.py`): hàm ánh xạ finding → comment với `diff` giả ([test_refine_review.py](../../tests/test_refine_review.py) là mẫu) · finding ngoài diff không thành comment inline nhưng có trong thân · nội dung độc (`@user`, `#123`, `<script>`) bị làm sạch · không có chuỗi secret nào trong output · cùng đầu vào ⇒ cùng hash ⇒ không đăng lại.

**DoD A-9:** trên PR gieo lỗi của vahan-rpa, review xuất hiện đúng `apps/api-server/app/api/jobs.py:<dòng>`.

---

---

## 5. Bước cuối — B ghép, rồi bàn giao để test trên vahan

> **Người ghép duy nhất: B.** Lý do: image phải chứa cả worker của A lẫn của B nên một người build và push thì không có khe hở bàn giao; và B nắm giao thức runner + luồng UI của vahan-rpa nên là người debug nhanh nhất khi tầng 2 đỏ.
> A **không** ghép, **không** sửa cấu hình project, **không** build image. A làm xong phần mình thì bàn giao (mục 5.1) rồi dừng.

### 5.1 A và B báo "xong" — điều kiện vào cổng ghép

Mỗi người tự tick hết danh sách của mình rồi nhắn người ghép. **Không tick đủ thì chưa được coi là xong.**

**A báo xong khi:**
- [ ] A-2, A-3, A-4, A-5, A-6, A-7, A-8, A-9: mọi DoD đã đạt.
- [ ] `pytest` toàn bộ xanh trên nhánh của A, đã rebase lên `phase1` mới nhất.
- [ ] `tools/har_scrub.py` đã bàn giao cho B (chữ ký + ví dụ chạy).
- [ ] Vùng `security-*` trong `Dockerfile` và `qc-gate.reusable.yml` đã điền; **không sửa gì ngoài cột sở hữu của A** (§3.6).
- [ ] Đã viết vào mô tả PR cuối: ba suite `sast/secrets/deps` chạy trên bản copy vahan-rpa mất bao lâu và ra bao nhiêu finding.

**B báo xong khi:**
- [ ] B-1 .. B-9: mọi DoD đã đạt (kể cả kết luận spike B-1c ghi vào PR).
- [ ] `pytest` toàn bộ xanh, đã rebase lên `phase1` mới nhất.
- [ ] HAR đã qua `har_scrub` và **một người khác** đã xem bằng mắt; `git grep -i -E "authorization|cookie|bearer|eyJ" .qc-agent/har/` không ra gì.
- [ ] Không sửa file ngoài cột sở hữu của B.

*(B tự kiểm phần của mình theo cùng danh sách; A không phải chờ B mới được báo xong.)*

### 5.2 Trình tự ghép — do B làm, theo đúng thứ tự

```
 A báo xong ─┐
             ├─► [1] merge vào phase1 ─► [2] kiểm ─► [3] image ─► [4] bật ở vahan-rpa ─► [5] BÀN GIAO cho bạn
 B báo xong ─┘
```

| # | Việc | Làm ở đâu | Xong khi |
|---|---|---|---|
| 1 | Merge PR của A và của B vào nhánh `phase1`. Conflict: **người merge sau rebase** (thường là B) | Nhánh `phase1` | Không còn conflict |
| 2 | `pytest` toàn bộ và `docker build .`; chạy `docker run --rm <image> doctor` | Máy B | Xanh; bảng `doctor` báo đủ 4 worker mới probe OK |
| 3 | Build + push image, lấy **digest** (`sha256:…`). Kiểm cách build tự động ở [image.yml](../../.github/workflows/image.yml) trước (chưa đọc khi viết plan) | Repo qc-agent | Có digest ghim được |
| 4 | Bật 4 suite mới (`sast`, `secrets`, `deps`, `integration`) ở [configs/projects/vahan-rpa.yaml](../../configs/projects/vahan-rpa.yaml) — **chỉ ở đây, chưa động vào `_default.yaml`**; cập nhật `image:` = digest trong `qc.yml` của repo vahan-rpa | Repo qc-agent, rồi repo vahan-rpa | PR của vahan-rpa bật gate mới, chưa gieo lỗi |
| 5 | **Bàn giao** — nhắn bạn theo mẫu ở 5.3 | — | Bạn nhận được thông báo |

Vì sao chưa động vào [_default.yaml](../../configs/projects/_default.yaml): comment đầu file ghi rõ đổi nó ảnh hưởng ngay PR của **mọi** team. Nâng lên chỉ sau khi bạn xác nhận (mục 5.5).

### 5.3 Mẫu tin bàn giao — B gửi cho bạn

```
SẴN SÀNG TEST TRÊN VAHAN
- Nhánh:        phase1 @ <sha>
- Image:        ghcr.io/muteen-felix/qc-agent@sha256:<digest>
- Đã bật ở:     configs/projects/vahan-rpa.yaml  (suite: sast, secrets, deps, integration)
- Kiểm trước:   pytest xanh · docker build xanh · doctor: 4/4 worker OK
- Biết trước:   <kết luận spike B-1c: extension có bị HAR chặn không → tầng 2 phủ tới đâu>
                <HAR là từ trang B thật hay từ stub>
- Chưa làm:     bật ở _default.yaml (chờ bạn xác nhận)
```

Hai dòng "Biết trước" là bắt buộc — đó là thứ bạn cần biết để không hiểu nhầm một kết quả xanh của tầng 2.

### 5.4 Bạn test thực tế trên vahan-rpa

**PR gieo lỗi:** sửa `apps/api-server/app/api/jobs.py` bằng một đoạn mã Semgrep bắt được (ví dụ `subprocess.run(cmd, shell=True)` với `cmd` nối từ input), và thêm một dependency có CVE mức high đã biết.

Kết quả **phải** ra đúng như sau — lệch dòng nào cũng là chưa xong:

```
qc-gate  🔴 FAIL · exit 1
  api-contract          ✅ pass
  integration (tier 1)  ✅ pass
  integration (tier 2)  ✅ pass
  secrets               ✅ pass
  sast                  ❌ semgrep.high = 1 vi phạm == 0
  deps                  ❌ trivy.high  = 1 vi phạm == 0
  perf-smoke ⚠   ui-explore ⚠            (tư vấn, không chặn)
→ review gắn file:dòng, với điều kiện dòng đó nằm trong diff của PR (xem A-9)
```

Bốn ca âm tính (mỗi ca phải **đỏ đúng lý do**, không đỏ vì lý do khác):

| Ca | Dựng thế nào | Kỳ vọng |
|---|---|---|
| Thiếu công cụ | gỡ `trivy` khỏi PATH của image | task `deps` → `skipped` → **FAIL** (nhờ `on_skipped_gate_task: fail`) |
| Công cụ hỏng | ép `trivy`/`semgrep` thoát mã lạ, không ghi file | `error` → đỏ. **Không bao giờ** "0 finding → pass" |
| Bản ghi lạc hậu | xoá 1 entry trong HAR | tier 2 đỏ, log nói rõ request nào không có trong bản ghi |
| Gói tin đi ra ngoài | script gọi thử một host lạ | bị chặn, `no_external_requests = false` → đỏ |

**Nếu có ca đỏ sai lý do hoặc xanh sai:** ghi lại (ca nào, kết quả thật, link run) rồi nhắn B. B xác định lỗi thuộc làn nào theo bảng sở hữu §3.6 và **chuyển cho đúng người sửa** — không sửa hộ ngoài cột của mình.

### 5.5 Sau khi bạn xác nhận

| # | Việc | Ai | Ghi chú |
|---|---|---|---|
| 6 | Bạn nhắn "PR gieo lỗi đúng, 4 ca âm tính đúng" | Bạn | Đây là cổng duy nhất mở bước 7 |
| 7 | Nâng 4 suite lên [`_default.yaml`](../../configs/projects/_default.yaml) | B | Từ đây ảnh hưởng mọi team |
| 8 | Đổi cột *Trạng thái* trong [architecture.md](../architecture.md): Integration và Security từ *Thiết kế* sang *Đã chạy* | Mỗi người phần của mình | Chỉ sau bước 6 |

---

## 6. Deep dive — vì sao thiết kế như vậy

### 6.1 Vì sao "adapter đếm, oracle phán"

Có cám dỗ viết `oracle.kind = "severity"` riêng. Đừng, vì:

- Ngưỡng ("chặn high, tha medium") là **chính sách**, không phải kỹ thuật. Đặt trong YAML thì người review PR đọc được và diff được; đặt trong code Python thì đổi ngưỡng là đổi code của gate.
- [threshold.py](../../src/qc_agent/oracle/threshold.py) đã xử lý đúng các tình huống khó: metric khai mà adapter không cung cấp ⇒ `OracleError` ⇒ `error` (không bao giờ pass/fail), giá trị không phải số ⇒ lỗi. Viết oracle mới là viết lại những chỗ dễ sai này.
- Nguyên tắc của cả hệ thống: adapter *được mất thông tin, không được bịa*. Đếm là mất thông tin (hợp lệ); phán là quyết định (không phải việc của adapter).

### 6.2 Vì sao chạy offline (rule vendored, DB nướng vào image)

- **Tất định.** `semgrep --config=auto` tải rule mới nhất từ registry: tuần sau họ thêm rule, PR của người khác bỗng đỏ mà không ai đổi gì. Gate mất tính tái lập là mất quyền chặn ([architecture.md §4.1](../architecture.md), §5.2).
- **Không rò mã.** Đúng dòng `data_egress` khai trong manifest: mã nguồn vào công cụ nhưng không rời máy.
- **Đổi giá:** phải cập nhật rule/DB chủ động. Chấp nhận, vì nó biến "cập nhật rule" thành một PR có diff duyệt được, và `trivy.db_age_days` ép việc build lại image định kỳ.

### 6.3 Vì sao `--error=false` / `--exit-code 0`

Mặc định nhiều công cụ quét thoát mã ≠ 0 khi **tìm thấy finding**. Adapter kiểu k6 ([k6_adapter.py](../../src/qc_agent/adapters/k6_adapter.py)) coi exit ≠ 0 là lỗi ⇒ "tìm thấy lỗ hổng" bị dán nhãn `error: hạ tầng` — sai nhãn, và mất luôn danh sách finding. Tắt cờ đó để **exit ≠ 0 chỉ còn nghĩa là công cụ hỏng**.

### 6.4 Giới hạn — phải nói thẳng trong tài liệu

- **Xanh không có nghĩa là an toàn.** Semgrep là so khớp mẫu: bỏ sót là chuyện chắc chắn xảy ra. Gate này bắt *các lỗi đã có luật* và *CVE đã công bố*, không phải mọi lỗ hổng.
- gitleaks quét working tree (mặc định) chỉ bắt secret **đang có** trong mã; secret đã bị xoá khỏi cây nhưng còn trong lịch sử cần `history: true`.
- Trivy chỉ biết CVE đã vào DB của nó; DB cũ ⇒ mù với CVE mới (đó là lý do có `db_age_days`).
- Quyền của extension (`manifest.json`) và đường đi credential vào trang B **chưa** được kiểm tự động ở Phase 1 — vẫn là mục đọc tay ở [architecture.md §2.4](../architecture.md).

### 6.5 Về dữ liệu cá nhân trong HAR

Lọc credential (A-5) là điều kiện cần, chưa đủ. Bản ghi HAR của một hệ thống chính phủ có thể mang dữ liệu người dân. Quy tắc đề xuất, chờ owner xác nhận: chỉ ghi HAR bằng tài khoản thử nghiệm; mọi HAR đưa vào repo phải qua `har_scrub` **và** được người thứ hai xem bằng mắt một lần trước khi commit.

### 6.6 Tóm lại làn A đóng góp gì cho "Agentic QC"

Trong bảng 5 tính chất agentic ([architecture.md §5.1](../architecture.md)), làn A nuôi **"dùng được công cụ"**: thêm 3 cảm biến tất định có quyền chặn. Nó không tự thông minh hơn, nhưng là nền để những phần thông minh phía sau có thứ để đọc — và mọi thứ ở đây đều chạy lại ra đúng kết quả cũ, nên vòng ngoài (có LLM) sau này có thể tin vào nó.
