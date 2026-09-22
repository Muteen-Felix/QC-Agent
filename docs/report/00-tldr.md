# 00 — TL;DR

**Đối tượng đọc:** mentor + người review kiến trúc · **Ngày:** 2026-09-22
**Nguồn:** `docs/architecture.md` (§1–§9) + kết quả PoC thật trong `runs/`. Không có nghiên cứu mới.

---

## Bốn luận điểm

### 1. Chúng tôi KHÔNG đề xuất "một AI agent làm QC"

Phần mới của bản này không nằm ở chỗ có agent. Trong một lần chạy gate xanh, **cái được gọi là
"orchestrator agent" không phải agent**: routing là phép lọc trên registry, thứ tự chạy là sắp
xếp topo trên DAG, so ngưỡng là số học, gộp verdict là phép `AND`. Không việc nào trong số đó
cần LLM, và mỗi việc dùng LLM là một chỗ đánh đổi tính tái lập lấy vẻ ngoài thông minh
`[arch §15.2]`.

Chúng tôi nói đúng điều này thay vì che nó — đó là bằng chứng team hiểu mình đang xây gì.

### 2. Chúng tôi đề xuất: Universal Contract phân biệt **nguồn phán quyết** + chính sách **hai lane**

Ba prior art trưởng thành nhất của lĩnh vực (TestZeus Hercules, Testkube, ReportPortal) **chia
nhau** ba mảnh của bài toán và đều hội tụ về JUnit XML. Trong JUnit XML, **một `assert` tất định
và một điểm LLM-judge trông y hệt nhau** — không consumer chuẩn nào gate trên trường tuỳ biến
`<properties>` `[R4 S14.2]`. Đó là chỗ tính tất định của gate chết trong mọi hệ hiện có.

Đề xuất của chúng tôi là **một hợp đồng kết quả mang `verdict_source` xuống tới từng finding**,
cộng hai lane trên **cùng một** contract:

| | **Gate lane** (chặn merge) | **Discovery lane** (không chặn) |
|---|---|---|
| Đầu ra | `verdict: pass/fail`, `gating: true` | `candidate_finding[]`, `gating: false` |
| Chạy khi | mỗi PR | nightly, có trần ngân sách |
| Tái lập | **bắt buộc** | không yêu cầu |
| Đường thoát | fail = chặn | finding → human triage → **promote** thành assert tất định ở gate lane |

Ràng buộc `allOf` trong `schemas/result.json` khiến `gating: true` **chỉ có thể** tồn tại khi
`verdict_source = deterministic_assert`. Đây không phải quy ước trong tài liệu — nó là điều kiện
validate. Adapter vi phạm thì **không qua được cửa schema**, kể cả khi người viết quên
`[arch D4]`.

### 3. Cổng Gate xanh tiêu tốn **0 lời gọi LLM**

LLM chỉ xuất hiện ở hai công cụ **rời, nằm ngoài đường chạy CI**: `plan-gen` (chạy khi spec đổi,
người duyệt *diff* rồi commit) và `failure-analysis` (chỉ chạy khi gate đỏ). Plan là **artifact
đã commit**, không phải output sinh lại mỗi lần `[arch D2]`.

Hệ quả kinh tế và vận hành:
- **Chi phí:** gate xanh = 0 token. Con số ~15× token của một orchestrator LLM-mỗi-PR **không áp
  dụng** cho kiến trúc này.
- **Không phụ thuộc mạng:** gate không cần provider nào sống để cho ra verdict.
- **Latency điều phối** là mili giây (đọc YAML, lọc registry, spawn tiến trình); latency tổng là
  `max(worker)`, không phải `sum(worker)`.

Đo thật trên run xanh `r-0019` (4 worker gating): `LLM tokens: 0 · cost $0.00`.

> ⚠️ Cái giá của quyết định này được nói thẳng, không giấu: plan-as-artifact chữa được
> non-determinism nhưng **đẻ ra staleness** (rủi ro V3 — "plan mục"). Job định kỳ chạy planner,
> xuất diff, mở PR là **bắt buộc**, không phải tuỳ chọn `[arch D2 Consequences]`.

### 4. Bằng chứng thực nghiệm: PoC 4 worker thật, 1 schema, verdict tái lập **100% (IDENTICAL)**

Bốn worker khác loại chạy gate lane qua **cùng một** `result.json`, không worker nào cần trường
riêng: `http-collect` (`http.collect`) · `schemathesis` (`api.property`) · `k6` (`http.load`) ·
`deepeval` (`llmapp.eval`). Worker thứ năm — `midscene-cli` (`ui.explore`) — chạy thật ở
discovery lane.

**Tái lập:** bốn run liên tiếp cùng `run_signature = f0df138d…`, so bằng `tools/diff_runs.py`
trên phần tất định (`run_signature`, `gate_verdict`, `exit_code`, `deterministic_view`):

```
$ python tools/diff_runs.py runs/r-0020 runs/r-0021
IDENTICAL — 4 kết quả gating, gate_verdict=FAIL
$ python tools/diff_runs.py runs/r-0021 runs/r-0022
IDENTICAL — 4 kết quả gating, gate_verdict=FAIL
$ python tools/diff_runs.py runs/r-0022 runs/r-0023
IDENTICAL — 4 kết quả gating, gate_verdict=FAIL
```

3/3 cặp liên tiếp IDENTICAL. Đây là tiêu chí nghiệm thu #4 `[arch Phụ lục B]`, và là lý do phần
LLM **không** được chặn merge: phần tất định cho ra `diff` rỗng, phần LLM thì không.

**Contract dùng chung, đo được:** validator trên 4 result thật của `r-0023` → **4/4 PASS**; trên
4 ví dụ trong `examples/` → **4/4 PASS**; result discovery của Midscene (`r-0015/results/t-101.json`)
→ **PASS** trên cùng schema. Đây là tiêu chí #2 — tiêu chí **trung tâm**: không đạt #2 thì kiến
trúc chưa được chứng minh, dù chạy đẹp đến đâu `[arch Phụ lục B]`.

---

## Câu định vị chốt `[R11]`

> Đề xuất mô tả sản phẩm QC Agent end-to-end (trigger → chọn phạm vi → thực thi → phân tích →
> báo cáo). Phần nhóm **đào sâu và chứng minh bằng PoC** là *hợp đồng kết quả và chính sách phán
> quyết* — `verdict_source`, hai lane, ba trạng thái — để lớp giữa của sản phẩm đó **không nói
> dối**, kể cả khi kiểm thử một AI application. LLM nằm ngoài đường phán quyết của gate; gate
> xanh gọi LLM **0 lần**.

Và câu phân biệt với prior art gần nhất `[R5 CORRECTION]`:

> Hercules đặt LLM vào đường phán quyết của **mọi** lần chạy; chúng tôi đặt LLM **ra ngoài** nó.
> Với một quality gate, đó không phải khác biệt về tính năng — đó là khác biệt về việc gate có
> **tái lập được** hay không.

---

## Giới hạn phải đọc cùng bốn luận điểm trên

| Giới hạn | Chi tiết |
|---|---|
| Gate không bắt được **chất lượng cảm nhận** | Summary tệ đi mà vẫn đúng schema + đúng độ dài ⟹ gate **xanh**. Đây là giới hạn thật của việc chỉ gate bậc 1–5 `[arch D5]` |
| Tiêu chí #5 (**N5**) chưa có bằng chứng trong repo | `git show --stat` cho commit thêm worker thứ 5 (`mock2`) là STEP 44, **chưa chạy**. Ước lượng ~1.5 giờ/worker là **phán đoán kỹ thuật, không phải số đo**, có thể lệch hệ số 2 `[arch §8.2]` |
| Số đo chỉ của **toy app** | Không ngoại suy sang suite thật `[arch Q10]` |
| `baseline` trong báo cáo là **giá trị viết tay** | PoC không có baseline store ⟹ chưa gate được theo delta `[arch D5]` |

Chi tiết đầy đủ: `03-prior-art.md` · `04-architecture.md` · `09-roadmap.md`.
