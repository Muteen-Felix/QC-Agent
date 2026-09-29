# Phase 1 · Làn B — Integration (Playwright + runner giả + bản ghi HAR)

> Đọc kèm: [plan-A-security.md](plan-A-security.md) (làn của người còn lại) · [architecture.md](../architecture.md) · [core-rules.md](../core-rules.md)
> File này **tự đủ**: phần chung ở §3 và §5 giống hệt trong file của A.
> Không có deadline trong tài liệu này — chỉ có **thứ tự** và **điều kiện xong (DoD)** của từng việc.

---

## 0. Bàn giao từ Làn A (cập nhật 2026-09-28)

> Đọc mục này trước. **Làn A chưa đủ điều kiện "xong" theo §5.1**: phần code + test + tài liệu đã có, còn 6 mục cần Docker / PR thật (liệt kê ở cuối). Chưa commit, chưa tạo nhánh `phase1` (repo mới có `main`, `develop`, `ncnghia`…; cần tạo `phase1` rồi rebase).

**Đã có (A):** 3 worker `semgrep`/`gitleaks`/`trivy` (manifest + adapter + suite mẫu `sast`/`secrets`/`deps`, `qc-agent init` sinh sẵn) · `tools/har_scrub.py` · lệnh `qc-agent doctor` · `integrations/security_review.py` (+ bước trong workflow, vùng `security-review (A)`) · `rules/semgrep/` (12 rule khởi điểm tự viết, **cần người review**) · hai vùng `(A)` trong Dockerfile đã điền + `docker/semgrep.lock` · tài liệu A-8.
**Kiểm:** `pytest` toàn bộ **990 passed / 130 skipped / 0 failed** (130 skip = test cần Postgres hoặc chỉ chạy trên Linux) · `python tools/freeze_contract.py --check` OK. Có 2 test nhạy thời gian đã thấy fail ngẫu nhiên một lần rồi xanh lại (`test_runner::test_04…`, `test_base::test_02…`); chưa có lần chạy trên bản gốc để đối chứng.

**Giao diện B dùng (từ A-5) — `har_scrub`:**

```python
import json, sys
sys.path.insert(0, "tools")
from har_scrub import scrub, find_leaks            # scrub(har, *, extra_redact_keys=()) -> dict · find_leaks(har) -> list[str] (rỗng = sạch)

clean = scrub(json.load(open("raw.har", encoding="utf-8-sig")), extra_redact_keys=("ma_so_thue",))   # extra: tuỳ chọn, tên khoá riêng của trang B
assert find_leaks(clean) == []
```

```bash
python tools/har_scrub.py raw.har .qc-agent/har/vahan-b.har [--extra-key ma_so_thue]
# exit 0 = đã ghi · 1 = còn sót credential sau khi lọc, KHÔNG ghi file (fail closed) · 2 = đầu vào không phải HAR
```

Giới hạn: chỉ lọc **credential** (header, cookie, query/URL, postData, JWT/Bearer trong body), **không lọc dữ liệu cá nhân** trong `response.content.text`; nội dung nhị phân (base64 không giải được UTF-8) không kiểm được; JSON trong thân có thể bị dump lại (khoảng trắng đổi) khi có giá trị bị lọc. HAR thật vẫn cần tài khoản thử nghiệm + người thứ hai xem (§6.5). Đã thử trên HAR mẫu tự dựng, **chưa** trên HAR thô thật của B.

**Cái B có thể dùng lại:**
- `tests/securitykit.py`, `tests/fixtures/workers/fake_security.py` + `tests/test_security_negative_cases.py`: cách chạy **engine + runner thật** với adapter thật, chỉ thay bước "chạy công cụ" (`Adapter._exec`) bằng kịch bản giả (crash exit 137, JSON cụt, thiếu binary…). Khuôn cho các ca bắt buộc dùng fixture ở B-8 (skip/thiếu `events.json`/thiếu binary); ca cần Playwright thật thì vẫn phải chạy trình duyệt thật.
- `doctor` (`qc-agent doctor [--workers-dir DIR]`): bảng probe mọi worker, exit 0 chỉ khi tất cả OK; `playwright.yaml` của B sẽ tự hiện trong bảng.
- Quy ước tên đã ghi ở `docs/core-rules.md`: `task_id` của A = `t-010…012`, B = `t-020…029`.

**File dùng chung / ngoài cột — B cần biết:**
- `src/qc_agent/scaffold/init.py` đã gọi `suites_security.add_suites(add, opts)` và `suites_integration.add_suites(add, opts)`; chữ ký giữ nguyên `(add, opts)`, `opts` là `init.Options` (**không** có kết quả scanner `found`; cần thì tự gọi `scan.scan(opts.sut_root, …)`).
- `src/qc_agent/scaffold/suites_integration.py` là file của B nhưng A tạo khung ở Bước 0: hiện chỉ là hàm rỗng, B điền.
- A đã sửa hai test có sẵn không thuộc cột nào: `tests/test_scaffold_init.py` và `tests/test_scaffold_templates.py` (khẳng định đúng tập file `init` sinh ra / mọi mẫu `.tmpl` đều được dùng). Nay chúng lấy file của từng làn từ chính `add_suites` nên **khi B điền `suites_integration.add_suites` và thêm `tmpl/integration*.tmpl` thì hai test tự tính**, không phải sửa; mẫu của B phải được render qua `templates.render` (module attribute) để test "mọi mẫu đều được dùng" đếm được.
- Bộ test cũ khẳng định: một bộ cấu hình `init` sinh **đủ tham số thì không còn dấu `qc-agent:todo`** (`test_scaffold_validate`). Khung `tier1/tier2.spec.mjs` có dấu VERIFY theo B-6 sẽ chạm ràng buộc này ⇒ B cần đọc `test_a_completed_generated_setup_has_no_errors_or_warnings` trước khi chốt cơ chế.
- `schemas/capabilities.json` (4 capability) và các vùng `(A)`/`(B)` trong Dockerfile/workflow đã khoét; vùng `(B)` còn nguyên là comment khung. Vùng `security-stages` dùng `FROM …@sha256` literal (không dùng `ARG`, vì `ARG` đầu file nằm ngoài vùng của A): B làm tương tự nếu cần stage riêng.
- Image có thêm gitleaks 8.30.1, Trivy 0.74.0 (kèm DB CVE nướng sẵn ở `/opt/trivy-cache`), Semgrep 1.178.0 (venv riêng `/opt/semgrep-venv`, khóa hash ở `docker/semgrep.lock`; **không** thêm vào `pyproject.toml`/`uv.lock` vì Semgrep ghim `jsonschema==4.25.1` lệch `4.26.0` của qc-agent). Trivy DB chỉ tải được lúc build ⇒ build cần mạng tới `ghcr.io`. Nếu B thêm tầng cho image thì build ngoài mạng-tối là lỗi.
- `docs/architecture.md`: A chỉ sửa §2.4 và dòng Security trong bảng §2 (cột Trạng thái để *Thiết kế* kèm ghi chú "worker + test đã có, chưa chạy thật"). File này còn thay đổi dở dang có từ trước ở working tree (~300 dòng, không phải của A): khi commit cần tách hunk.

**Việc B làm khi ghép mà A chưa làm được (đây cũng là 6 mục ⬜ của A trong §5.1):**
1. `docker build .` — **chưa build lần nào** (máy dựng không chạy được Docker). Đường dẫn binary trong image gitleaks (`/usr/bin/gitleaks`) và Trivy (`/usr/local/bin/trivy`) là theo hiểu biết, có smoke test `gitleaks version && trivy --version && semgrep --version` ngay khi build để sai thì **build đỏ** chứ không lộ ra lúc chạy gate. Rồi `docker run --rm <image> doctor` (A-6 DoD).
2. Chạy công cụ thật và **thay fixture** `tests/fixtures/security/*.json`: hiện là bản **soạn tay** theo định dạng đã biết, chưa phải output thật. `tests/fixtures/security/README.md` liệt kê từng điểm cần đối chiếu `[EXTERNAL GAP]` (Semgrep có `paths.scanned`; gitleaks `--redact` cho `Secret`/`Match` thế nào và có ghi báo cáo khi sạch không; Trivy có liệt kê lockfile sạch trong `Results` không; tên `UpdatedAt` của `db/metadata.json`). Lệch thì adapter báo `error` (đỏ hạ tầng), **không** xanh giả.
3. `semgrep --validate --config rules/semgrep` bằng Semgrep thật, và thử trên bản copy vahan-rpa (chưa từng chạy rule bằng Semgrep).
4. Thử `har_scrub` trên HAR thô thật của B (A-5 DoD).
5. Chạy 3 suite trên bản copy vahan-rpa; ghi thời gian + số finding vào mô tả PR cuối (A-9 DoD cũng cần PR gieo lỗi thật: review phải xuất hiện đúng `apps/api-server/app/api/jobs.py:<dòng>`).
6. Tạo `phase1`, rebase A lên đó, rồi merge.

**Cố ý chưa làm:** bật `sast`/`secrets`/`deps` trong `configs/projects/*.yaml` hay `_default.yaml` (§3.4, §5.2 bước 4); suite mới chạy thử được ở mode `manual` (`qc-agent run --project <slug> --mode manual --suites sast,secrets,deps`) vì mode `pr` từ chối `--suites` ngoài policy. Đổi cột Trạng thái sang *Đã chạy* chỉ sau khi bạn xác nhận (§5.5).

---

## 1. Tóm tắt: What · Why · How · Giúp gì cho hệ thống

| | |
|---|---|
| **WHAT — làm cái gì** | Thêm **1 worker `playwright`** (capability `flow.integration`) kiểm **chuỗi tích hợp** của vahan-rpa: web-ui → api-server → runner → extension → trang B. Chuỗi này được kiểm ở 2 tầng **có quyền chặn merge** — **tầng 1**: hợp đồng runner/job với *runner giả*; **tầng 2**: chuỗi đầy đủ với trang B được **phát lại từ bản ghi HAR** (`routeFromHAR`) — và 1 tầng **tư vấn**: **tầng 3** chạm trang B thật, tần suất thấp. |
| **WHY — vì sao cần** | Khâu **Integration** trong 4 khâu ([architecture.md §2](../architecture.md)) đang trống. Sản phẩm phụ thuộc một hệ thống ngoài **không kiểm soát được** — trang B của chính phủ — có thể chậm, đổi giao diện, bật captcha. Nếu gate gọi B thật thì PR của dev đỏ vì lý do không phải lỗi của họ (vi phạm §4.4). Nếu không kiểm gì thì lỗi ở chỗ nối giữa các thành phần là lỗi sản xuất phổ biến nhất của một sản phẩm RPA mà không ai bắt. Cần cách **giả trang B một cách tất định**. |
| **HOW — làm bằng cách nào** | Worker chạy Playwright test; **mỗi `test()` là một check** trả `True/False` vào `signals["checks"]`, rồi dùng oracle `checks` **đã có** để phán. Một **lớp bảo vệ (guard) nằm trong code của mình** — không nằm trong script của SUT — ép mọi test dùng `routeFromHAR(update:false, notFound:'abort')` và chặn mọi host ngoài allowlist. Không LLM, không oracle mới, không đụng contract. |
| **GIÚP HỆ THỐNG** | (1) Khâu Integration từ *Thiết kế* → *Đã chạy*, có quyền chặn. (2) Thực thi bằng **code** hai nguyên tắc đang chỉ nằm trên giấy: §4.3 *không bắn tải vào B* và §4.4 *gate không chạm B thật*. (3) Tách được "lỗi của dev" khỏi "B đổi giao diện": tầng 3 fail ⇒ việc cần làm là **ghi lại HAR mới**, không chặn dev. (4) Ở tầm Agentic QC: đây là phần **cảm biến** cho ranh giới giữa hệ thống của mình và thế giới ngoài — và là nguồn tín hiệu "bản ghi lạc hậu" mà vòng ngoài (tự chữa khi B đổi, [architecture.md §5.4](../architecture.md)) sẽ đọc sau này. |

**Không nằm trong làn này:** Keploy — **bỏ hẳn**, không phải hoãn (§6.1). Sinh test từ PRD (Later). Nightly cron cho tầng 3 (Phase 1 chỉ khai suite, chạy bằng Mode 2).

---

## 2. Nhìn hình trước

```
 TẦNG 1 — hợp đồng runner/job           CHẶN
   Playwright (APIRequestContext) ──HTTP──► api-server (SUT, đang chạy trong gate)
        ▲                                         │
        └── đóng vai RUNNER GIẢ: đăng ký, nhận job, báo trạng thái ──┘
   kiểm:  job_accepted · status_transitions · retry_honored ...       (tên thật: từ B-1b)

 TẦNG 2 — chuỗi đầy đủ, B được PHÁT LẠI  CHẶN
   Chromium ──► web-ui ──► api-server ──► runner ──► extension ──► ┃ .qc-agent/har/vahan-b.har ┃
                                                                   ┃  = trang B GIẢ, từ bản ghi  ┃
   guard:  routeFromHAR(update:false, notFound:'abort')  +  chặn mọi host ngoài allowlist
   kiểm:  chọn filter → apply → tải report (đúng luồng nghiệp vụ trong architecture.md §3)

 TẦNG 3 — trang B THẬT                   TƯ VẤN, tần suất thấp, KHÔNG chạy trên PR
   cùng script tầng 2 nhưng không có HAR  →  fail = "B đã đổi"  →  việc cần làm: GHI LẠI HAR MỚI
```

```
 Cách một test trở thành phán quyết
   spec.test("filter_applied")  ──►  Playwright JSON reporter  ──►  adapter  ──►  signals["checks"]
                                                                     │            {"filter_applied": True,
   host lạ bị chặn / request thiếu trong HAR ─► events.json ─────────┘             "report_downloaded": False,
                                                                                    "no_external_requests": True}
                                                                                    │
                                              oracle "checks" (ĐÃ CÓ): required = [...] ─► pass | fail
                                              check vắng mặt ⇒ error (KHÔNG BAO GIỜ pass)
```

**Ba điều cần nhớ nhất của làn này** (lý do ở §6):

1. `notFound: 'abort'`, không phải mặc định `'fallback'` — mặc định sẽ để request lạ **đi thật ra trang B của chính phủ** trong khi test vẫn xanh.
2. Guard nằm trong **code của mình**, không nhờ script của SUT tự nhớ gọi đúng.
3. Bản ghi HAR đã **lạc hậu** phải làm test **đỏ ngay trên PR** và nói rõ request nào thiếu — đó là tín hiệu đúng, không phải nhiễu.

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

## 4. Làn B — danh sách việc theo thứ tự phụ thuộc

```
 Bước 0 (chung) ──► B-1 khảo sát + spike ──┬──► B-2 dep + manifest + guard ──► B-3 adapter ──┐
   (B-1a xin quyền/ghi HAR thô:             │                                                 ├─► B-4 tầng 1 ──┐
    chạy ngầm, bắt đầu NGAY)                │                                                 │                ├─► B-6 mẫu suite ─► B-8 ca âm tính ─► B-9 docs
                                            └──────────────────────────────────────────────────► B-5 tầng 2 ──┤                       ▲
                                                 (cần HAR đã lọc: A-5, hoặc HAR tự viết tay)  └─► B-7 tầng 3 ──┘───────────────────────┘
                                                                                                                 ──► Bước cuối (B ghép, §5)
```

Lưu ý phụ thuộc chéo duy nhất giữa hai làn: **B-5 cần `tools/har_scrub.py` của A (A-5)** để commit HAR thật. B **không bị chặn** — B-2..B-4 không cần nó, và B-3/B-5 có thể phát triển bằng HAR tự viết tay (`tests/fixtures/integration/synthetic.har`) cho tới khi A bàn giao.

---

### B-1 · Khảo sát và spike (đọc + thử nhỏ, trước khi viết adapter)

Đây là phần **rủi ro cao nhất của cả Phase 1** vì hai thứ nằm ngoài nhóm: quyền truy cập trang B, và hành vi của extension. Làm trước để biết sớm.

#### B-1a · Xin quyền và ghi HAR thô — **bắt đầu ngay, chạy ngầm song song mọi việc khác**

- Việc này phụ thuộc **người ngoài nhóm** (quyền vào trang B, tài khoản). Đừng để nó nằm cuối danh sách.
- Ghi HAR một lần: mở trang B thật, làm đúng luồng **chọn filter → apply → tải report**, xuất HAR. Chưa lọc, **chưa commit**.
- Dùng **tài khoản thử nghiệm** hoặc dữ liệu đã ẩn danh (xem §6.5 — HAR của hệ thống chính phủ có thể mang dữ liệu cá nhân).
- **Phương án dự phòng nếu bị chặn quá lâu:** dựng một *stub của trang B* từ tài liệu/ảnh chụp màn hình, ghi HAR từ stub. Tầng 2 vẫn chạy và vẫn chặn được — nó kiểm *code của mình gọi đúng hợp đồng*, không kiểm *trang B*. Ghi rõ vào PR và [architecture.md §2.3](../architecture.md) rằng bản ghi này là từ stub, để không ai tưởng đã kiểm B thật.

#### B-1b · Đọc giao thức runner/job của vahan-rpa **thật**

`tests/fixtures/scan/vahan-rpa/` trong repo này chỉ là **fixture cho scanner** (mới có `GET /health` và `GET /{job_id}`), không phải sản phẩm. Nguồn đúng là repo `vahan-rpa` thật. Từ đó rút ra và ghi thành bảng ngắn trong PR đầu tiên:

| Cần biết | Vì sao |
|---|---|
| Runner đăng ký/nhận job/báo trạng thái bằng những endpoint nào, xác thực ra sao | Để viết runner giả (tầng 1). Tài liệu hiện chỉ liệt kê `GET /api/health`, `/api/runners`, `POST /api/jobs`, `POST /api/jobs/{job_id}/upload-excel` |
| Các trạng thái của job và chuyển trạng thái hợp lệ | Để có check `status_transitions` có nghĩa |
| Retry hoạt động thế nào | Check `retry_honored` |
| Luồng UI: những màn hình/nút nào (`chọn filter`, `apply`, `tải report`) và selector ổn định | Tầng 2 |

**Tên check là thứ B tự chốt ở bước này** — các tên `job_accepted`, `status_transitions`, `retry_honored`, `filter_applied`, `report_downloaded` trong tài liệu này chỉ là **gợi ý**. Khi chốt xong, nhắn A (§3.7) để tài liệu không lệch.

#### B-1c · Spike: `routeFromHAR` có chặn được request của **extension** không?

Đây là câu hỏi chưa ai kiểm và quyết định tầng 2 dựng thế nào. `[EXTERNAL GAP]` — hành vi bên dưới là hiểu biết về Playwright, **chưa kiểm chứng trong repo này**:

- `context.routeFromHAR` áp cho request của **các trang** trong context, kể cả request do *content script* của extension phát ra từ trang.
- Request do **service worker/background** của extension phát ra thường **không** đi qua `context.route`.

Cách thử (một script bỏ đi, không commit): nạp extension bằng `launchPersistentContext` + `--load-extension`, đặt `routeFromHAR` với `notFound: 'abort'`, cho extension gọi B, xem request có bị chặn/phát lại không. Ba kết quả và quyết định cho mỗi kết quả:

| Kết quả spike | Quyết định cho tầng 2 |
|---|---|
| Extension gọi B từ content script ⇒ bị HAR chặn/phát lại đúng | Dựng tầng 2 đúng như thiết kế |
| Extension gọi B từ service worker ⇒ **lọt ra ngoài** | Dựng một *server HAR nhỏ* (Node) phát lại HAR, trỏ extension vào đó qua cấu hình base URL của B (cần extension có tuỳ chọn này). Nếu extension không cấu hình được ⇒ ghi nợ và xuống phương án sau |
| Cả hai đều không làm được | Tầng 2 chỉ phủ **web-ui → api-server → runner**; đoạn **extension ↔ B** chuyển xuống tầng 3 (tư vấn) và ghi thành nợ loại `recording` cho Phase 2. Tầng 2 vẫn chặn được, chỉ hẹp hơn |

**Bất kể kết quả:** guard "chặn mọi host ngoài allowlist" (B-2) vẫn là điều kiện bắt buộc — nếu có đường request lọt ra ngoài, test phải **đỏ**, không được xanh im lặng.

**DoD B-1:** B-1a đã có người nhận/đang chạy · bảng B-1b điền xong · spike có kết luận ghi vào PR (một trong ba dòng ở bảng trên).

---

### B-2 · Dependency Node, manifest và guard

**Phụ thuộc:** B-1c (kết quả spike quyết định dạng context). Có thể bắt đầu manifest/dependency trước.

**Dependency.** Playwright Chromium **đã có trong image** ([Dockerfile:62–63](../../Dockerfile), `npx playwright install --with-deps chromium`) — phục vụ Midscene. Nhưng [package.json](../../package.json) hiện chỉ khai `@midscene/cli`; `playwright` trong `package-lock.json` có vẻ đến từ phụ thuộc bắc cầu. **Đừng dựa vào cái bắc cầu**: thêm tường minh `@playwright/test` (ghim phiên bản khớp với `playwright` đang được cài để không cài hai bản Chromium), chạy `npm install` để cập nhật `package-lock.json`, và chỉ B sửa hai file này. Kiểm: `docker run --rm --entrypoint npx <image> playwright --version`.

**Manifest** `workers/playwright.yaml`:

```yaml
name: playwright
version_probe: "npx playwright --version"
adapter: "qc_agent/adapters/playwright_adapter.py"
lanes: [gate, discovery]        # gate: tầng 1+2 · discovery: tầng 3 (B thật, tư vấn)
capabilities:
  - id: flow.integration
    oracle_kinds: [checks]
    verdict_sources: [deterministic_assert]
    parallel_safe: false        # mở trình duyệt: độc quyền, như midscene
requires:
  env: [APP_BASE_URL]
  binaries: [npx]
data_egress: [app_input, app_output]
cost_profile: { tokens_per_run: 0, typical_wallclock_s: 180 }
```

Lưu ý `version_probe` chạy ở preflight với timeout 20 giây ([registry.py](../../src/qc_agent/core/registry.py) `PROBE_TIMEOUT_SECONDS`): `npx` lần đầu có thể chậm — đo thử trong image; nếu vượt thì probe bằng đường dẫn `node_modules/.bin/playwright` thay vì `npx`.

**Guard** — mẫu `scaffold/tmpl/integration-support.mjs.tmpl`, được `init` ghi thành `.qc-agent/integration/support.mjs` trong repo SUT. Test của SUT **chỉ được** `import { test, expect } from './support.mjs'`:

```js
import { test as base, expect } from '@playwright/test';
import fs from 'node:fs';

const ALLOWED = (process.env.QC_ALLOW_HOSTS || '').split(',').filter(Boolean);   // host của SUT/UI; KHÔNG gồm host trang B
const HAR = process.env.QC_HAR_FILE || '';                                        // rỗng = tầng 3 (B thật)
const B_HOST = process.env.QC_B_HOST || '';                                       // host trang B, để HAR chỉ áp cho nó
const EVENTS = process.env.QC_EVENTS_FILE;

export const test = base.extend({
  context: async ({ context }, use) => {
    const events = { blocked_hosts: [], har_missing: [] };

    // 1) đăng ký TRƯỚC: bắt mọi host, host ngoài allowlist bị hủy  (route đăng ký sau chạy trước)
    await context.route('**/*', (route) => {
      const host = new URL(route.request().url()).hostname;
      if (ALLOWED.includes(host)) return route.fallback();
      events.blocked_hosts.push(host);                    // chỉ host, không lưu URL/query
      return route.abort();
    });

    // 2) đăng ký SAU: cho host trang B, phát lại từ bản ghi. Hằng số, KHÔNG cấu hình được từ ngoài
    if (HAR) {
      await context.routeFromHAR(HAR, { update: false, notFound: 'abort', url: new RegExp(`^https?://${B_HOST}/`) });
      context.on('requestfailed', (r) => {
        if (new URL(r.url()).hostname === B_HOST) events.har_missing.push(new URL(r.url()).pathname);   // pathname, không query
      });
    }

    await use(context);
    if (EVENTS) fs.writeFileSync(EVENTS, JSON.stringify(events));
  },
});
export { expect };
```

Thứ tự đăng ký route và chi tiết `url:` của `routeFromHAR` là `[EXTERNAL GAP]` — **test guard ở B-8 chính là bằng chứng** rằng chúng hoạt động như mô tả; nếu không thì sửa guard chứ đừng sửa test.

Nếu spike B-1c yêu cầu extension: guard phải dựng context bằng `launchPersistentContext` với `QC_EXTENSION_DIR`; phần này quyết theo kết quả spike.

**DoD B-2:** `doctor`/preflight thấy worker `playwright` probe OK trong image · guard có test đơn vị bằng Playwright thật (xem B-8).

---

### B-3 · Adapter `playwright_adapter.py`

**Phụ thuộc:** B-2. Độc lập với B-4/B-5 (dùng fixture JSON để phát triển).

Khuôn: [midscene_adapter.py](../../src/qc_agent/adapters/midscene_adapter.py) (cũng chạy trình duyệt qua `npx`, cũng độc quyền). Hai hàm:

**`build_cmd`:**
1. Kiểm `inputs`: `spec_file` (đường dẫn `.spec.mjs` tương đối SUT root), `har` (tuỳ chọn), `b_host` (nếu có `har`), `allow_hosts`. Sai kiểu ⇒ `AdapterParseError`.
2. **Lint tĩnh file spec** (một lớp bảo vệ *sơ suất*, không phải bảo vệ chống ác ý — xem §6.3):
   - từ chối nếu chứa `routeFromHAR(` hoặc `update: true` (phải đi qua guard);
   - từ chối nếu import `playwright`/`@playwright/test` trực tiếp thay vì `./support.mjs`;
   - từ chối nếu tên `test('…')` không khớp `^[a-z][a-z0-9_]*$` — **tên test chính là tên check**.
3. Đặt env cho tiến trình: `QC_ALLOW_HOSTS` (host của `target.base_url` và `APP_UI_URL`), `QC_HAR_FILE`, `QC_B_HOST`, `QC_EVENTS_FILE=<workdir>/events.json`, `PLAYWRIGHT_JSON_OUTPUT_NAME=<workdir>/pw.json`. Gán qua `self.env = {**self.env, ...}` như [k6_adapter.py](../../src/qc_agent/adapters/k6_adapter.py).
4. Xoá `pw.json`, `events.json` cũ. Lệnh: `npx playwright test <spec_file> --reporter=json --workers=1`.

**`parse_output`:**
1. Không có `pw.json` ⇒ `AdapterParseError` (không có kết quả ≠ pass).
2. Duyệt cây kết quả (`suites → specs → tests → results`) — tên trường `[EXTERNAL GAP]`, kiểm bằng fixture thật. Với mỗi spec: `checks[title] = (all results passed)`. Test bị skip hoặc không chạy ⇒ **không** đưa vào `checks` ⇒ oracle sẽ báo `error` vì check bắt buộc vắng ([checks.py](../../src/qc_agent/oracle/checks.py)). Đó là hành vi mong muốn.
3. Đọc `events.json` (thiếu ⇒ `AdapterParseError`): `checks["no_external_requests"] = (blocked_hosts == [])`; `checks["har_covers_all_requests"] = (har_missing == [])`.
4. Finding cho mỗi check `False`: `title` nêu đường dẫn thiếu (`HAR thiếu: GET /report/download`) hoặc host bị chặn — **chỉ host và pathname, không query, không header**. `severity_hint: "high"`.
5. Evidence: `raw_output` = `pw.json`; thêm `trace`/`screenshot` nếu bật, để người xem lý do đỏ.

**Test** (`tests/test_playwright_adapter.py`, dùng fixture JSON — không cần trình duyệt): tất cả pass · một test fail ⇒ check đó `False` · test bị skip ⇒ vắng khỏi `checks` · `pw.json` cụt/thiếu ⇒ error · `events.json` có `blocked_hosts` ⇒ `no_external_requests False` · lint bắt đúng 3 lỗi ở trên · `spec_file` thoát khỏi SUT root (`../`) bị từ chối.

**DoD B-3:** test xanh · chạy thử với một spec giả trong image ra `Result JSON` hợp lệ theo contract.

---

### B-4 · Tầng 1 — hợp đồng runner/job với runner giả

**Phụ thuộc:** B-1b (biết giao thức), B-3.

- **Runner giả nằm trong chính script test** (Playwright `request` fixture đóng vai runner qua HTTP), **không phải một container riêng**. Lý do: tránh phải sửa workflow (file dùng chung) và tránh dựng thêm thành phần; runner giả chỉ cần nói HTTP với api-server.
- Script: `.qc-agent/integration/tier1.spec.mjs`. Mỗi `test()` một check, tên `snake_case`:

```js
import { test, expect } from './support.mjs';
const API = process.env.APP_BASE_URL;

test('job_accepted', async ({ request }) => {
  const r = await request.post(`${API}/api/jobs`, { data: { /* payload tối thiểu, theo B-1b */ } });
  expect(r.ok()).toBeTruthy();
});
test('status_transitions', async ({ request }) => { /* runner giả nhận job, báo running → done; kiểm chuỗi trạng thái hợp lệ */ });
test('retry_honored',      async ({ request }) => { /* runner giả báo lỗi; kiểm job được retry theo luật */ });
```

- **Dữ liệu test phải tự dọn và không đụng nhau** khi nhiều job chạy song song: tạo job với tên có nonce riêng mỗi lần chạy, và xoá ở cuối (mẫu: `{{nonce}}` và `cleanup` trong [ai-eval.yaml](../../tests/fixtures/sut/noteboard/.qc-agent/suites/ai-eval.yaml)).
- Tầng 1 **không cần HAR, không cần trình duyệt** ⇒ nhanh, ổn định; là tầng đầu tiên nên chạy xanh.

**DoD B-4:** chạy trên vahan-rpa (đã dựng trong container) cho 3 check xanh; cố tình phá hành vi (vd. sửa cho retry sai) ⇒ đúng check đó đỏ.

---

### B-5 · Tầng 2 — chuỗi đầy đủ với B phát lại từ HAR

**Phụ thuộc:** B-1a (có HAR thô), B-2 (guard), B-3, và **A-5** (`har_scrub`) để commit HAR thật. Trong lúc chờ A: dùng HAR tự viết tay.

1. **Lọc rồi mới commit.** `python tools/har_scrub.py raw.har .qc-agent/har/vahan-b.har` (chữ ký do A bàn giao). CLI **fail closed** nếu còn sót. Sau đó kiểm chéo: `git grep -i -E "authorization|cookie|bearer|eyJ" .qc-agent/har/` không ra gì. **Người thứ hai xem bằng mắt** trước khi commit (§6.5).
2. **Script** `.qc-agent/integration/tier2.spec.mjs`: mở web-ui, làm luồng chọn filter → apply → tải report; mỗi mốc là một `test()`/check (`filter_applied`, `report_downloaded`…). Import từ `./support.mjs` để có guard. HAR được nạp bởi guard qua `QC_HAR_FILE`, không gọi `routeFromHAR` trong script.
3. **Chỉ áp HAR cho host trang B** (`QC_B_HOST`); request tới SUT/UI vẫn đi thật (đến container đang chạy trong gate), request tới bất kỳ host nào khác bị guard hủy.
4. **Khớp request:** HAR khớp theo URL + method (+ body cho POST); các giá trị đổi theo lần chạy (timestamp, nonce, token đã lọc) làm request không khớp bản ghi. Nếu gặp: dùng tham số `url`/quy tắc chuẩn hoá của guard, **không** đổi `notFound` thành `fallback` cho "dễ qua".
5. Kết quả spike B-1c quyết định phần có extension hay không (B-1c).

**DoD B-5:** tầng 2 xanh khi HAR khớp · xoá 1 entry HAR ⇒ đỏ, finding nêu đúng pathname thiếu · `git grep` không thấy credential · một người khác đã xem HAR.

---

### B-6 · Mẫu suite và nối vào `init`

**Phụ thuộc:** B-4, B-5 (biết chính xác tên check và input).

`scaffold/tmpl/integration.yaml.tmpl` — hai task gate. Khuôn cấu trúc: [api-contract.yaml.tmpl](../../src/qc_agent/scaffold/tmpl/api-contract.yaml.tmpl) (cùng các trường bắt buộc của schema):

```yaml
suite: integration
description: Tích hợp — tầng 1 (runner giả) + tầng 2 (trang B phát lại từ HAR). Chặn merge ở mode pr.
tasks:
- task_id: t-020
  capability: flow.integration
  lane: gate
  intent: 'Tầng 1: hợp đồng runner/job với runner giả'
  target: {kind: http_service, base_url: ${env.APP_BASE_URL}}
  inputs: {spec_file: .qc-agent/integration/tier1.spec.mjs}
  oracle: {kind: checks, required: [job_accepted, status_transitions, retry_honored, no_external_requests]}
  expected_result_kind: verdict
  budget: {wallclock_s: 300, tokens: 0, usd: 0}
  determinism: {seed: 0, replayable: true}
  evidence_required: [raw_output, stdout]
  retry: {max: 1, 'on': [error]}
  prefer: [playwright]
- task_id: t-021
  capability: flow.integration
  lane: gate
  intent: 'Tầng 2: chuỗi đầy đủ, trang B phát lại từ bản ghi'
  target: {kind: web_app, base_url: ${env.APP_BASE_URL}}
  inputs:
    spec_file: .qc-agent/integration/tier2.spec.mjs
    har: .qc-agent/har/vahan-b.har
    b_host: {{b_host}}
  oracle: {kind: checks, required: [filter_applied, report_downloaded, no_external_requests, har_covers_all_requests]}
  # …các trường còn lại như t-020, cộng depends_on: [t-020]
```

`suites_integration.add_suites(add, opts)`: ghi `integration.yaml`, `integration/support.mjs`, và **khung** `tier1.spec.mjs`/`tier2.spec.mjs` kèm dấu `qc-agent:todo VERIFY:` (xem [templates.py](../../src/qc_agent/scaffold/templates.py) `TODO`): `qc-agent validate` **từ chối** repo còn dấu này, nên repo mới không thể bật gate cho tới khi người điền xong luồng thật và HAR. Đây là cơ chế duyệt-một-lần đã có của hệ thống, tận dụng lại thay vì tự chế.

`{{b_host}}` và mọi giá trị chèn vào mẫu phải đi qua bộ kiểm/quote như trong `templates.py` — đừng nối chuỗi thô.

**Test** (`tests/test_scaffold_integration.py`): `init` sinh đủ file · `validate` từ chối khi còn dấu todo · mẫu sinh ra qua được schema task ([schema.py](../../src/qc_agent/core/schema.py)) · giá trị độc (`b_host` có dấu cách, ký tự YAML) bị từ chối.

**DoD B-6:** `qc-agent init` trên fixture sinh đủ; điền xong các dấu thì `validate` qua.

---

### B-7 · Tầng 3 — trang B thật, chỉ tư vấn

**Phụ thuộc:** B-5.

- Task `t-022`, **`lane: discovery`**, cùng script tầng 2 nhưng **không có `har`** (guard nhận `QC_HAR_FILE` rỗng ⇒ B là host được phép). Chỉ chạy ở **Mode 2** (`modes.manual.suites: "*"` trong [_default.yaml](../../configs/projects/_default.yaml)); **không** đưa vào `advisory_suites` của mode `pr` — PR của dev không được chạm B thật (§4.4).
- Khi task discovery `fail`, [base.py `_verdict`](../../src/qc_agent/adapters/_base.py) tự gán `gating: false` — nên không thể chặn ai dù cấu hình sai.
- **Tải:** tầng 3 chạm hệ thống chính phủ ⇒ **một luồng, một lần, không vòng lặp, không song song** (§4.3). `parallel_safe: false` đã đảm bảo phần song song; đừng thêm retry ở suite này.
- **Không dựng cron** trong Phase 1. Ghi vào [usage-ci.md](../usage-ci.md) cách chạy tay và ý nghĩa của kết quả: *tầng 3 fail ⇒ ghi lại HAR mới (B-1a), không chặn dev*.

**DoD B-7:** suite khai đúng lane; chạy thử một lần tay ở Mode 2; xác nhận rằng mode `pr` **không** chọn task này.

---

### B-8 · Các ca âm tính — bằng chứng guard hoạt động

**Phụ thuộc:** B-2..B-5. Đây là test **bằng Playwright thật** trong image (không thể giả bằng fixture, vì mục tiêu là chứng minh hành vi mạng thật).

| Ca | Dựng thế nào | Kỳ vọng | Vì sao quan trọng |
|---|---|---|---|
| Request lạ ra host ngoài | spec cố `fetch('https://example.org/…')` | bị chặn, `blocked_hosts` có host đó, `no_external_requests = False` ⇒ **đỏ** | Chứng minh gate không thể chạm B thật/host lạ dù script muốn |
| HAR lạc hậu | xoá 1 entry khỏi HAR | `har_covers_all_requests = False`; finding nêu đúng pathname ⇒ **đỏ** | Đây là tín hiệu "B đã đổi/HAR cũ" |
| `notFound` bị đổi | (kiểm thủ công guard) thử đổi sang `'fallback'` trong bản sao | test "host lạ" trở nên **xanh giả** ⇒ ca đầu phải bắt được | Chứng minh ca 1 thực sự bảo vệ `abort` |
| Spec lách guard | file có `routeFromHAR(` hoặc `update: true` | lint từ chối ⇒ `error` | Chống sơ suất |
| Check bị skip | `test.skip(...)` một check bắt buộc | check vắng ⇒ oracle `error` ⇒ **đỏ** | "Vắng mặt" không bao giờ là pass |
| Không có trình duyệt | probe/`requires` hỏng | `skipped` ⇒ **FAIL** nhờ `on_skipped_gate_task: fail` | Không ai tắt gate bằng cách gỡ Chromium |
| Thiếu `events.json` | xoá file trước khi parse | `AdapterParseError` ⇒ `error` | Không đoán |
| HAR còn credential | nhét một JWT vào HAR trong fixture | kiểm `git grep`/`find_leaks` của A bắt được | Chốt chặn cuối của rò rỉ |

**DoD B-8:** toàn bộ bảng là test tự động (những ca cần trình duyệt có thể đánh dấu để chạy trong CI có image, không bắt buộc trên máy dev).

---

### B-9 · Cập nhật tài liệu

**Phụ thuộc:** B-4..B-7.

- [architecture.md](../architecture.md) **§2.3 Integration**: thay khối `> [T6]` bằng nội dung thật — ba tầng, guard, cách ghi HAR, cách lọc, và kết quả spike B-1c. Trong bảng §2 sửa **dòng Integration**: **bỏ hẳn "(Keploy)"** khỏi cột Công cụ (lý do ở §6.1). Cột Trạng thái ghi *Đã chạy* chỉ khi Bước cuối (§5) xong.
- [usage-ci.md](../usage-ci.md): cách ghi lại HAR mới; cách chạy tầng 3 bằng tay.
- [onboarding.md](../onboarding.md): repo mới muốn có Integration cần chuẩn bị gì (một HAR đã lọc, hai spec, `b_host`).
- Cập nhật ngôn ngữ về nguyên tắc: **"Gate không chạm trang B thật" giờ được thi hành bằng code (guard), không chỉ là ghi chú** — đáng nêu trong [core-rules.md](../core-rules.md).

**DoD B-9:** người chưa đọc code đọc §2.3 biết được cách ghi HAR và đọc kết quả tầng 3.

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

### 6.1 Vì sao bỏ Keploy (và không phải "hoãn")

Keploy ghi/phát lại traffic **của service bạn đang chạy**, để dựng test cho *chính service đó*. Việc cần ở đây là giả **trang B ở phía ngoài** để service của mình gọi vào. Hai việc ngược chiều nhau:

```
Keploy:        client ──► [GHI] ──► service CỦA MÌNH        (mình là bên bị gọi)
routeFromHAR:  service CỦA MÌNH ──► [PHÁT LẠI] ──► trang B  (mình là bên gọi)
```

Dùng Keploy ở đây là lắp ngược mũi tên. Bỏ khỏi bảng §2 để mentor không hỏi lại.

### 6.2 Vì sao `notFound: 'abort'` là dòng quan trọng nhất

Mặc định của Playwright là `'fallback'`: request không có trong HAR sẽ **đi thật ra internet**. Với gate của mình, đó là request thật tới trang B của chính phủ — vi phạm §4.3 và §4.4 mà **test vẫn xanh**, nên không ai biết. Với `'abort'`, hôm nào code sinh ra request chưa có trong bản ghi thì test đỏ **ngay trên PR**, lý do đọc được: *bản ghi đã lạc hậu*. Guard thêm một lớp nữa: chặn cả host lạ không thuộc B, vì `'abort'` chỉ có tác dụng với những request mà HAR route nhận.

### 6.3 Vì sao guard nằm trong code của mình, và giới hạn của nó

Nếu chỉ dặn "script nhớ dùng `notFound: 'abort'`" thì mọi repo SUT mới đều có thể quên, và sẽ không ai biết. Nên `support.mjs` do `init` sinh ra là điểm duy nhất tạo context, hằng số hoá hai tham số, và adapter lint để bắt các cách vòng qua *do sơ suất*.

**Giới hạn phải nói thẳng:** đây là chống sơ suất, **không** chống ác ý. Script test là mã của PR — người viết PR có thể sửa cả `support.mjs`. Mô hình tin cậy của toàn hệ thống đã ghi ở đầu [qc-gate.reusable.yml](../../.github/workflows/qc-gate.reusable.yml): *"tin team SUT, chỉ chống sơ suất"*. Chống ác ý cần chạy guard ngoài tầm sửa của PR (ví dụ ở tầng mạng của container); để Later.

### 6.4 Vì sao runner giả nằm trong script chứ không là container

Runner giả chỉ cần nói HTTP với api-server. Biến nó thành container thì phải sửa workflow, thêm mạng docker, thêm bước dọn — toàn là file dùng chung, kéo làn B đụng vào làn A, và tăng bề mặt lỗi hạ tầng. Trong script: không đụng workflow, không thành phần phụ.

### 6.5 Về dữ liệu cá nhân trong HAR

`har_scrub` (A-5) lọc **credential**, không lọc **dữ liệu cá nhân** trong thân phản hồi. HAR trang B có thể chứa thông tin phương tiện/chủ xe thật. Quy tắc đề xuất, chờ owner xác nhận: chỉ ghi bằng tài khoản thử nghiệm hoặc dữ liệu ẩn danh; mọi HAR vào repo phải qua `har_scrub` **và** được người thứ hai xem bằng mắt trước khi commit.

### 6.6 Giới hạn của chính tầng 2 — nói thẳng

Tầng 2 kiểm **code của mình gọi đúng theo một bản ghi**, không kiểm **trang B thật có còn hành xử như bản ghi**. Việc thứ hai là của tầng 3 (tư vấn). Hệ quả: tầng 2 xanh không có nghĩa "luồng chạy được với B hôm nay" — nó nghĩa là "PR này không làm hỏng cách mình nói chuyện với B như đã biết". Đó là lý do hai tầng phải tách quyền: tầng 2 chặn, tầng 3 chỉ báo.

### 6.7 Tóm lại làn B đóng góp gì cho "Agentic QC"

Trong bảng 5 tính chất agentic ([architecture.md §5.1](../architecture.md)), làn B nuôi **"dùng được công cụ"** ở chỗ khó nhất — ranh giới với thế giới ngoài không kiểm soát. Ngoài ra nó tạo ra thứ vòng ngoài sẽ cần: một tín hiệu **có cấu trúc** khi trang B đổi (`har_covers_all_requests = False`, kèm pathname). Về sau, "tự chữa khi trang B đổi giao diện" ([architecture.md §5.4](../architecture.md), mục 2) sẽ đọc đúng tín hiệu này để đề xuất ghi lại HAR — thay vì phải đoán vì sao test đỏ.
