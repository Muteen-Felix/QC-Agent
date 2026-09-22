# 04 — ĐỀ XUẤT KIẾN TRÚC

**Nguồn:** `docs/architecture.md` §2–§6 (D1–D5), §8 · JSON thật từ `examples/` và `runs/`.
Không có nghiên cứu mới trong file này.

---

## 4.0 Vì sao orchestrator–worker, nói cho đúng lý do `[R10]`

Lý do chọn cấu trúc này **không** phải "QC cần tính xác định nên dùng hierarchical". Đó là một
category error: hierarchical vs peer-to-peer là **topology phối hợp**, còn tính xác định đến từ
chỗ khác `[R1 S4e1]`. Câu đúng:

> Chúng tôi chọn cấu trúc **orchestrator–worker** vì các worker QC **không có phụ thuộc lẫn nhau**
> — k6 không cần biết DeepEval nghĩ gì — nên **không có gì để thương lượng ngang hàng**. Peer-to-peer
> ở đây không phải rủi ro, nó đơn giản là vô nghĩa. **Tính xác định không đến từ hình dạng của sơ
> đồ; nó đến từ việc quy định chỗ nào LLM được phép quyết** (N1).

Và hệ quả của việc gọi đúng tên: nếu giữ N1 + N4 + N5 nghiêm túc thì cái ở giữa **không còn là
multi-agent system** — nó là một **plan-execute pipeline có LLM làm planner** `[R1 S4e2]`. Gọi
đúng tên có lợi thật: rẻ hơn ~15× token, audit tốt hơn, và không phải biện minh cho việc thêm
agent. Toàn bộ N1–N5 vẫn đúng nguyên văn.

---

## 4.1 Năm nguyên lý N1–N5

> **Nhãn nguồn:** N1–N5 là năm nguyên lý của kiến trúc D trong tiền đề ban đầu. RUN 1–5 **dùng**
> chúng liên tục nhưng không viết lại định nghĩa ở một chỗ. Cách phát biểu dưới đây được **dựng
> lại từ cách các RUN sử dụng chúng**, có dẫn dòng nguồn cho từng cái. Đây là chỗ người review
> được phép cãi về câu chữ — nhưng **cơ chế cưỡng chế ở cột 3 là code thật**, không phải diễn giải.

| | Nguyên lý | Phát biểu | Cưỡng chế bằng CÁI GÌ |
|---|---|---|---|
| **N1** | **Ranh giới quyền phán quyết** | LLM **sinh ứng viên**; **oracle tất định quyết định** ứng viên nào sống. Worker tự hành được **LÁI** và **MÔ TẢ**, không được **PHÁN** | `oracle` do **orchestrator khai** trong task spec — worker không có quyền tự chọn "tôi sẽ tự chấm bằng LLM" `[R4 S12 nguyên lý 2]` |
| **N2** | **Non-determinism không rò rỉ vào verdict** | Verdict tổng là **hàm tất định** của các assert tất định. Phương sai LLM được ghi lại, **không được chặn merge** | Ràng buộc `allOf` trong `schemas/result.json`: `gating: true` **⟹** `verdict_source = deterministic_assert`. Cộng `expected_result_kind` khai **trước** khi chạy `[R5 P2.4]` `[R4 S12 nguyên lý 3]` |
| **N3** | **Human chèn đúng chỗ, không rải đều** | Human ở đúng những điểm **hành động không đảo ngược rẻ được** hoặc **chuẩn mực bị thay đổi** — không ở mọi bước | **4 điểm escalation** + bảng **7 điểm HITL** với mức tự động cao nhất còn an toàn cho từng điểm `[R4 S13.3, S13.7]` |
| **N4** | **Universal Result** | **Một** `result.json` cho **mọi** worker. Khác biệt của tool được chuẩn hoá **tại adapter**, không làm phình schema lõi | `additionalProperties: false` ở top-level `result.json`. Tiêu chí nghiệm thu #2: 4 adapter qua **cùng một** schema, **không adapter nào cần trường riêng** `[arch Phụ lục B #2]` |
| **N5** | **Thêm worker là việc config, không phải việc sửa lõi** | Orchestrator địa chỉ hoá **`capability`**, không địa chỉ hoá **tên worker**. Thay k6 bằng Locust là đổi một dòng trong registry, không đụng plan | Không có `if worker == ...` trong `core/`. Kiểm bằng `git show --stat` cho commit thêm worker: **`core/` không xuất hiện** `[R4 S12 nguyên lý 1]` |

### Ba nhận xét làm N1–N5 không phải khẩu hiệu

**(1) N2 chết ở đâu trong mọi hệ hiện có.** Cả Hercules và Testkube hội tụ về JUnit XML, nơi một
assert tất định và một điểm LLM-judge **không phân biệt được nữa**. Trong kiến trúc này N2 không
phải một điều khoản trong tài liệu, nó là **hai ràng buộc `allOf`**:

```
verdict.verdict_source == "deterministic_assert"  ⟹  verdict.confidence == null
verdict.gating == true                            ⟹  verdict.verdict_source == "deterministic_assert"
```

> *"Hai ràng buộc này là toàn bộ N2 viết thành code… không ai có thể vi phạm N2 mà vẫn validate
> qua, kể cả khi quên"* `[R5 P2.4]`.

Vì sao phải là schema chứ không phải code review: *"một prompt rule không phải là một control"*
(`Day16`, dẫn gián tiếp qua `[R2 S7.5]`). Cưỡng chế bằng `allOf` mạnh hơn mọi tài liệu.

**(2) N4 là tiêu chí trung tâm, và nó có giá.** Adapter **được phép mất thông tin, KHÔNG được phép
bịa thông tin** `[R4 S12c]`. HTML report giàu của Midscene và cấu trúc metric lồng nhau của
DeepEval bị **ép phẳng**. Đó là chủ đích, nhưng nó là cái giá thật, không phải tính năng.

**(3) N5 có một rủi ro cụ thể đã được đặt tên: V1.** Ngày 2 của sprint, ai đó thêm
`if worker == "midscene"` vào lõi vì đang gấp. **Đúng 30 giây đó, N4 và N5 chết**, và thứ duy
nhất mới của bản propose biến mất — còn lại một script chạy 4 tool `[R4 S15.1]`. Cách chặn đã
dùng thật: **viết adapter Midscene TRƯỚC** (slot 3, không phải slot 4), vì nó là cái khó nhất.

### Bốn trạng thái, không phải hai

`pass | fail | error | skipped`. *Gộp `error` vào `fail` là cách nhanh nhất giết niềm tin vào
gate* `[R3 S11(3)]`. `skipped` làm gate **VÀNG** và phải in ở **đầu** report — vì failure mode
nguy hiểm nhất của cả hệ là **V2: gate xanh vì worker không chạy**, và nó **im lặng, trông giống
thành công** `[R4 S15.1]`.

Bằng chứng V2 bắt được trong PoC (`runs/r-0012/report.json`, trường `banner`):

```
t-101 | skipped: có worker nhưng probe hỏng: version_probe không chạy được:
        [WinError 2] The system cannot find the file specified
```

---

## 4.2 Two-Lane Policy — hai lane trên CÙNG MỘT contract

Đây là chỗ chính sách, không phải chỗ code. Tách hai lane vì **nhóm worker tất định và nhóm worker
tự hành không thể nằm chung một lane**: cho worker tự hành quyền chặn merge là đưa thẳng phương sai
LLM vào gate — *"vi phạm N2 dù report có dán nhãn 'LLM judgment' đi nữa, vì cái bị chặn là merge,
không phải cái nhãn"* `[R1 S4e4]`.

| | **GATE lane** | **DISCOVERY lane** |
|---|---|---|
| **Worker** | tất định: `schemathesis`, `k6`, `http-collect`, `deepeval` (chỉ metric tất định) | tự hành: `midscene-cli` (+ **canary**) |
| **`expected_result_kind`** | `verdict` | `candidate_finding` |
| **Đầu ra** | `verdict: pass/fail`, `gating: true` | `findings[]`, `gating: false` |
| **Chạy khi** | **mỗi PR** | **nightly**, có trần ngân sách cứng |
| **Tái lập** | **bắt buộc** — cùng signature khác verdict = bug của hệ | **không yêu cầu** |
| **Lời gọi LLM** | **0** khi xanh | có, và `cost` lên report kể cả khi xanh |
| **Đường thoát** | `fail` = chặn merge, `exit 1` | finding → triage → **promote** thành assert tất định ở gate lane |

```
LANE:  ├─────────── GATE (chặn merge) ───────────┤   ├── DISCOVERY ──┤
       http-collect  schemathesis  k6  deepeval*     midscene · canary
                                        * chỉ metric deterministic mới gating

                     ◄────── promote ──────┘
       (điều kiện: deterministic_assert  ∧  tái lập ≥3/3  ∧  có suggested_assertion)
```

### Mũi tên *promote* là giá trị thật của discovery lane

Không phải "agent tìm thêm bug". Giá trị là: nó **sinh ứng viên cho gate lane**, đúng mô hình
TestGen-LLM — **LLM đề xuất, filter tất định quyết định cái nào vào** `[R1 S1]`.

Neo định lượng cho mô hình đó: TestGen-LLM trên Instagram Reels/Stories — **75%** test build được,
**57%** pass ổn định, **25%** tăng coverage `VERIFIED` [arxiv.org/abs/2402.09171]. Đọc theo hướng
kiến trúc: **43% output bị vứt, và cái làm nó dùng được không phải LLM — mà là bộ filter tất định**
(build được? pass ổn định qua nhiều lần chạy? có tăng coverage không?).

**Bằng chứng promote có thật trong PoC** — finding từ run discovery `r-0015`, phát hiện bằng **tín
hiệu tất định** (`dom_unchanged`), nên nó mang sẵn `repro_steps` + `suggested_assertion`:

```
## 3. HEURISTIC / DISCOVERY (KHÔNG chặn gate)

- `t-101` · midscene-cli
  - [f-sig-dom_unchanged] DOM không render lại sau thao tác xoá
    detected_by: implicit_signal:dom_unchanged  ← TẤT ĐỊNH
    → ứng viên promote: ui.explore
```

Lưu ý điểm tinh: finding này ở **discovery lane** (`gating: false`) nhưng
`verdict_source: deterministic_assert` ở cấp finding. **Hai trục độc lập nhau** — lane quyết định
có chặn hay không; `verdict_source` ghi nguồn phán quyết. Đúng finding loại này mới đủ điều kiện
promote.

### Ngay ở discovery lane, tín hiệu PHÁT HIỆN vẫn tất định

`oracle.kind: implicit_signals` = console error, HTTP 5xx, unhandled rejection, DOM không đổi,
element không tìm thấy. LLM chỉ được `llm_observations_allowed: true` — được **mô tả**, không được
**phán quyết** `[R4 S12a]`.

### Canary — lớp phòng thủ chưa xuất hiện trong prior art nào

Mỗi run discovery kèm một **task chắc chắn phải fail**. Nếu worker tự hành báo `pass` cho task bất
khả thi, ta biết **chính worker hỏng — không phải app tốt** `[R4 S15.4 lớp 3]`. Chạy thật
(`docs/decisions.md`, STEP 29, 2026-09-22): canary trả `status=fail`, `gating=false`, finding
`implicit_signal:element_not_found`, result qua schema.

Trong 5 lớp phòng thủ chống "worker tự hành báo pass sai", **lớp 1 là triệt để**: nếu nó không
phán quyết thì nó không thể báo pass sai. Canary là lớp 3 — bắt trường hợp lớp 1 bị cấu hình sai.

### Rủi ro mặc định của lane này: V4 — nghĩa địa

Nightly sinh ~40 finding/tuần, không ai triage, một tháng sau có 160 finding và người ta **tắt
nightly**. Đây là số phận thường gặp nhất của mọi công cụ exploratory `[R4 S15.1]`. Chặn: trần cứng
số finding mỗi run; ưu tiên `deterministic_assert`; **tuần nào không ai triage thì tự động TẮT
discovery và báo** — thà tắt công khai còn hơn tích nợ.

---

## 4.3 Hợp đồng ba mảnh — với JSON thật

Ba mảnh, **không phải hai**. *"Không có manifest thì orchestrator buộc phải biết tên worker, và
N5 chết"* `[R1 S4e3]`.

```
workers/<name>.yaml   ──►  worker tự khai NĂNG LỰC        (điều kiện của N5)
      │
      ▼
schemas/task_spec.json ──►  orchestrator GIAO VIỆC + KHAI ORACLE   (cưỡng chế N1)
      │
      ▼
schemas/result.json   ──►  worker TRẢ KẾT QUẢ + NGUỒN PHÁN QUYẾT   (cưỡng chế N2, N4)
```

Ba file này đã được **freeze** và băm: `schemas/CONTRACT.sha256` (tag `qrs-v0.1`).

### Mảnh 1 — Worker manifest: worker tự khai, orchestrator không đoán

Đây là chỗ N5 sống. Orchestrator đọc file này, chạy `version_probe`, rồi **lọc**; nó không bao giờ
hard-code tên worker.

```yaml
# workers/k6.yaml — worker TẤT ĐỊNH, gate lane
name: k6
version_probe: "k6 version"
adapter: "adapters/k6_adapter.py"
lanes: [gate]
capabilities:
  - id: http.load
    oracle_kinds: [threshold]
    verdict_sources: [deterministic_assert]   # tập giá trị ĐƯỢC PHÉP phát ra
    parallel_safe: false                      # dùng thiết bị đo → xếp hàng riêng
requires:
  env: [APP_BASE_URL]
  binaries: [k6]
data_egress: []                               # KHÔNG gửi gì ra ngoài
cost_profile: { tokens_per_run: 0, typical_wallclock_s: 60 }
```

```yaml
# workers/midscene.yaml — worker TỰ HÀNH, discovery lane
name: midscene-cli
lanes: [discovery]                            # ← không bao giờ nhận task gate
capabilities:
  - id: ui.explore
    oracle_kinds: [implicit_signals]
    verdict_sources: [deterministic_assert, llm_judgment, heuristic]
    parallel_safe: false
requires:
  env: [MIDSCENE_MODEL_API_KEY, MIDSCENE_MODEL_NAME, MIDSCENE_MODEL_BASE_URL, APP_BASE_URL]
data_egress: [screenshot, dom]                # ← khai TRƯỚC, không khai sau
cost_profile: {tokens_per_run: null, typical_wallclock_s: 120}
```

Ba điểm đáng nói:

1. **`verdict_sources` trong manifest có nghĩa KHÁC** với `verdict_source` trong result. Ở manifest
   nó là *tập giá trị worker được phép phát ra* (dùng để routing + validate); nó **không phải** nhãn
   của kết quả. Sửa đổi này phát hiện được nhờ verify DeepEval `[R3 9.6]`.
2. **`data_egress` khai trước.** Ba đường egress, xếp theo mức nghiêm trọng: `DeepEval (gửi cả
   input lẫn output của app)` > `Midscene (screenshot/DOM — có thể chứa dữ liệu khách hàng trên
   màn hình)` > `plan-gen (spec/mã nguồn)` > `Schemathesis, k6 (không gửi gì)` `[R3 S11(5)]`.
3. **Routing là phép lọc, không phải suy luận** `[R4 S13.1]`:

```python
candidates = [w for w in registry
              if task.capability   in w.capabilities
              and task.lane        in w.lanes
              and task.oracle.kind in w.capability.oracle_kinds
              and w.probe_ok]
```

0 ứng viên ⟹ task `skipped` + **ghi lý do vào report** (không được im lặng bỏ qua). >1 ứng viên ⟹
chọn theo `prefer:` khai trong plan. **Không để LLM chọn** — chọn bằng LLM là đưa non-determinism
vào *coverage*, dạng vi phạm N2 im lặng nhất.

### Mảnh 2 — Task spec: orchestrator KHAI oracle, worker không tự chọn

JSON thật, `examples/task.k6.json` — task gate lane, tất định:

```json
{
  "task_id": "t-002", "plan_id": "plan-poc-001", "run_id": "r-0001",
  "capability": "http.load", "lane": "gate",
  "intent": "GET /notes chịu 10 VU trong 30s với p95 < 300ms",
  "target": {"kind": "http_service", "base_url": "http://127.0.0.1:8000"},
  "inputs": {"script": "tests/perf/notes_list.js", "vus": 10, "duration": "30s"},
  "oracle": {"kind": "threshold", "assertions": [
    {"metric": "http_req_duration.p95", "op": "<", "value": 300, "unit": "ms"},
    {"metric": "http_req_failed.rate", "op": "<", "value": 0.01}]},
  "expected_result_kind": "verdict",
  "budget": {"wallclock_s": 90, "tokens": 0, "usd": 0},
  "determinism": {"seed": null, "replayable": true},
  "sut_identity_ref": "sut-poc",
  "evidence_required": ["raw_output", "stdout"],
  "retry": {"max": 1, "on": ["error"]}
}
```

Năm trường mang toàn bộ chính sách:

| Trường | Vì sao nó ở đây chứ không ở worker |
|---|---|
| `capability: "http.load"` | **Việc cần làm, không phải ai làm.** Đây là điều kiện sống của N5 — thay k6 bằng Locust là đổi một dòng registry, **không đụng plan** |
| `lane: "gate"` | Quyết định worker nào **đủ tư cách** nhận task này |
| `oracle` | **Orchestrator khai.** Đây là cách cưỡng chế N1 bằng **cấu trúc** thay vì bằng quy ước — worker không có quyền quyết định "tôi sẽ tự chấm bằng LLM" |
| `expected_result_kind` | Khai **trước** khi chạy. Worker discovery trả verdict chặn gate thì **adapter từ chối tại tầng validate schema** — non-determinism bị chặn **ở cửa**, không phải bị chặn bằng thiện chí |
| `retry: {on: ["error"]}` | **Chỉ được retry `error`, KHÔNG BAO GIỜ retry `fail`.** Retry một `fail` là cách hệ thống tự nói dối |
| `budget` | Trần **cứng**, không phải gợi ý. Chặn rủi ro V5: UI đổi → agent đi vòng → số bước ×3 → hoá đơn ×3 mà vẫn "pass" |

Task discovery lane khác **đúng bốn chỗ** (`examples/task.midscene.json`): `lane: "discovery"` ·
`oracle.kind: "implicit_signals"` với danh sách `signals[]` + `llm_observations_allowed: true` ·
`expected_result_kind: "candidate_finding"` · `determinism.replayable: false` và
`budget.tokens: 150000`. **Cùng một schema, không thêm trường nào.**

### Mảnh 3 — Result: `verdict_source` ở cấp TỪNG finding

Đây là mảnh mang toàn bộ phần mới của bản propose. JSON thật, `examples/result.k6_pass.json`:

```json
{
  "task_id": "t-002", "run_id": "r-0001",
  "worker": {"name": "k6", "version": null, "adapter_version": "0.1.0"},
  "status": "pass",
  "verdict": {
    "value": "pass",
    "verdict_source": "deterministic_assert",
    "gating": true,
    "confidence": null,
    "rationale": null
  },
  "findings": [],
  "metrics": {"http_req_duration.p95": 214.7, "http_req_failed.rate": 0.002},
  "evidence": [
    {"kind": "raw_output", "uri": "runs/r-0001/t-002/k6-summary.json",
     "sha256": "6b59cdf8c95d86bc32706f762862e08ccb7e9b83d60359c9f913f218c0f6b7a1"},
    {"kind": "stdout", "uri": "runs/r-0001/t-002/stdout.log",
     "sha256": "275f8eb0b3670719242ead5792bf63d698bda7b3a0506a3a7d2880a8b44fecb9"}
  ],
  "cost": {"wallclock_s": 31.4, "tokens": 0, "usd": 0.0},
  "sut_identity_ref": "sut-poc",
  "determinism": {
    "seed": null,
    "replay_cmd": "k6 run --summary-export=k6-summary.json tests/perf/notes_list.js"
  },
  "adapter_notes": ["k6 exit_code=0"]
}
```

`gating: true` ở đây **chỉ hợp lệ vì** `verdict_source = deterministic_assert`. Đảo một trong hai
giá trị là **không validate qua schema**.

**Ví dụ quyết định nhất — MỘT result, HAI `verdict_source`.** Đây là lý do `verdict_source` phải ở
cấp từng finding, và nó là **sửa đổi bắt buộc phát hiện nhờ verify DeepEval, không phải nhờ suy
luận** `[R3 9.6]`. Trích `examples/result.ai_eval.json` (rút gọn phần lặp):

```json
{
  "task_id": "t-003", "run_id": "r-0088",
  "worker": {"name": "deepeval", "version": null, "adapter_version": "0.1.0"},
  "status": "fail",
  "verdict": {
    "value": "fail",
    "verdict_source": "deterministic_assert",
    "gating": true,
    "confidence": null,
    "rationale": null
  },
  "findings": [
    {
      "finding_id": "f-11",
      "title": "summary_shorter_than_body: 4/5 case đạt — case #3 summary dài hơn body",
      "detected_by": "metric:summary_shorter_than_body",
      "verdict_source": "deterministic_assert",
      "confidence": null,
      "severity_hint": "high",
      "evidence": [{"kind": "raw_output", "uri": "runs/r-0088/t-003/case-3.json",
                    "sha256": "d740be21c93f5a0861ed4c7b92038fa5716ce0db43829af150c6be73048d921e"}]
    },
    {
      "finding_id": "f-12",
      "title": "G-Eval 'summary có giữ ý chính không': 0.71 (baseline 0.78, delta -0.07)",
      "detected_by": "metric:GEval",
      "verdict_source": "llm_judgment",
      "confidence": 0.64,
      "rationale": "Judge cho rằng case #2 và #5 bỏ mất ý về deadline. Ngưỡng cảnh báo delta = -0.10, chưa đạt.",
      "severity_hint": "low"
    }
  ],
  "metrics": {
    "JSONCorrectness.pass_rate": 1.0,
    "summary_shorter_than_body.pass_rate": 0.8,
    "GEval.score": 0.71
  },
  "cost": {"wallclock_s": 48, "tokens": 12440, "usd": 0.04},
  "adapter_notes": [
    "judge_config.model != SUT model — đã kiểm, không trùng.",
    "GEval là advisory: KHÔNG tham gia verdict.value."
  ]
}
```

Đọc kỹ ba điều trong khối JSON này:

1. `f-11` (tất định) và `f-12` (judge) **nằm trong cùng một result** — một worker sinh **hai** loại
   phán quyết. Vì vậy `verdict_kind` ở **worker manifest** là mô hình **sai về thực tế**, và
   `verdict_source` phải ở cấp finding.
2. `verdict.value = "fail"` **chỉ do** `f-11`. Điểm G-Eval 0.71 **không tham gia**. Đổi G-Eval
   thành fail thì **verdict tổng không đổi** — đó chính là tiêu chí nghiệm thu #3.
3. `confidence` chỉ xuất hiện ở finding `llm_judgment` (0.64). Ở finding tất định nó **buộc phải là
   `null`** theo `allOf` #1 — một ràng buộc nhỏ nhưng nó khiến "assert tất định có độ tin 0.9" trở
   thành câu **không biểu diễn được** trong hệ này.

### Ba bất biến của auditability `[R4 S13.6]`

1. **Không có evidence thì không có verdict** — result thiếu evidence bắt buộc là `error`.
2. **Mọi evidence có sha256.**
3. **`plan.yaml` có commit hash** — nếu không thì không biết đã chạy *cái gì*.

Cộng `determinism.replay_cmd` trong mỗi result: chạy lại **một worker độc lập, không cần
orchestrator**. Chuỗi truy ngược đầy đủ:

```
gate_verdict  →  results[] (chỉ gating=true)  →  result t-002
   ├─ verdict.verdict_source = deterministic_assert
   ├─ oracle đã dùng  →  task_spec  →  plan.yaml @ commit
   ├─ evidence[]      →  k6-summary.json (sha256 …)
   ├─ replay_cmd      →  chạy lại độc lập
   ├─ worker k6 + adapter@0.1.0
   └─ sut_identity_ref  →  {code commit, prompt hash, model snapshot, corpus id, decoding params}
```

---

## 4.4 Chính sách: **LLM là NGƯỜI TỐ GIÁC, không phải QUAN TOÀ**

Phép gộp verdict là một dòng:

```python
gate_verdict = AND( r.verdict.value == "pass"
                    for r in results
                    if r.verdict.gating == True )
```

Theo `allOf`, `gating: true` **chỉ có thể** đến từ `deterministic_assert`. Vậy **verdict tổng là
một hàm tất định của các assert tất định** — *N2 ở dạng một dòng công thức, chứng minh được chứ
không phải hứa hẹn* `[R4 S13.4]`.

### Bảng luật khi xung đột

| # | Tình huống | **Verdict tổng** | Hành động kèm theo |
|---|---|---|---|
| 1 | Mọi `gating=true` đều `pass` | 🟢 **PASS** · exit 0 | — |
| 2 | Có ≥1 `gating=true` = `fail` | 🔴 **FAIL** · exit 1 | In task gây fail ở mục 1 của report |
| 3 | `deterministic PASS` + `llm_judgment FAIL` | 🟢 **PASS** — LLM **không** lật verdict | Ghi vào mục "LLM disagreement", **tách hẳn** khỏi verdict. Đếm tần suất. **≥3 run cùng loại bất đồng ⟹ tự mở task review cho người** |
| 4 | `deterministic FAIL` + `llm_judgment PASS` | 🔴 **FAIL** — LLM **không** lật verdict | Như trên, cùng bộ đếm |
| 5 | Có `error` (sau retry ≤1) | 🔴 **FAIL** · exit 1 | Nhãn **"hạ tầng"**, không phải "code sai" |
| 6 | Có `skipped` | 🟡 **VÀNG** · exit 0 **[SUY RA]** | **In khối SKIPPED ở ĐẦU report.** Ghi rõ *vùng nào không được kiểm* |
| 7 | Discovery có finding (kể cả `severity_hint: high`) | **không ảnh hưởng** | Vào mục 3 của report |
| 8 | Canary báo `pass` (lẽ ra phải `fail`) | **không ảnh hưởng gate** | 🚨 **Báo động worker hỏng** — không phải app tốt |

**Vì sao luật 3 và 4 đối xứng** — cả hai chiều đều hỏng, nhưng hỏng theo hai cách khác nhau:

| Nếu cho LLM lật thành `fail` | Nếu cho LLM lật thành `pass` |
|---|---|
| Gate đỏ **không tái lập được** → dev chạy lại là xanh → **team học cách bấm re-run** → gate mất hết giá trị | Một phán quyết **phi tất định ghi đè một assert tất định** — đây là **thảm hoạ**, không phải trade-off |

**Bất đồng được xử lý như DỮ LIỆU, không như phán quyết.** Sau ≥3 lần, người review kết luận một
trong ba: (a) LLM đúng → **viết assert tất định mới** (đây là mũi tên *promote* ở cấp metric);
(b) LLM sai → chỉnh rubric / loại metric; (c) mơ hồ → giữ advisory `[R4 S13.4]`.

> **Nói ngắn: LLM là người tố giác, không phải quan toà. Nó được quyền làm ồn cho tới khi ai đó
> biến tiếng ồn đó thành một assert — lúc đó nó mới có quyền chặn.**

### Cùng một chính sách, nhìn thấy được trong report thật

Report chia **ba mục không bao giờ cộng vào nhau** — đây là một trong ba thứ *không bao giờ cắt*
`[R5 4.2]`. Trích `runs/r-0019/report.md` (gate xanh, 4 worker):

```
# QC Gate Report — run r-0019
wallclock 40s · LLM tokens: 0 (tasks: —) · cost $0.00

## VERDICT: ✅ PASS

## 1. DETERMINISTIC ASSERT (chặn gate)
| task  | worker       | capability   | status | chi tiết |
| t-000 | http-collect | http.collect | pass   | — |
| t-001 | schemathesis | api.property | pass   | — |
| t-002 | k6           | http.load    | pass   | http_req_duration.p95=15.5559, http_req_failed.rate=0 |
| t-003 | deepeval     | llmapp.eval  | pass   | — |
→ gate_verdict = pass AND pass AND pass AND pass = **PASS**

## 2. LLM JUDGMENT (KHÔNG chặn gate — chỉ tham khảo)
Các số này không cộng vào 'pass rate' và không chặn gate.

## 3. HEURISTIC / DISCOVERY (KHÔNG chặn gate)
## 4. SKIPPED / ERROR
## 5. AUDIT
```

Một chi tiết không dàn dựng, đáng giá hơn cả bảng: trong run này `adapter_notes` của `t-003` ghi
**"G-Eval advisory report không hợp lệ; deterministic checks không đổi"**. Phần advisory **hỏng
thật**, và `gate_verdict` **không bị ảnh hưởng**. Đó là N2 hoạt động trong điều kiện xấu, không
phải trong slide.

### Human escalation — đúng 4 chỗ, không hơn (N3)

| # | Tín hiệu kích hoạt | Ai xử lý |
|---|---|---|
| 1 | Gate đỏ do **`error` lặp ≥2 run liên tiếp** trên cùng worker | nghi **hạ tầng**, không nghi code |
| 2 | `llm_judgment` mâu thuẫn `deterministic_assert` **≥3 run** | task review (luật 3/4) |
| 3 | Discovery sinh finding `deterministic_assert` **tái lập được** | hàng chờ **promote** |
| 4 | **Vượt trần chi phí toàn run** | người quyết có nâng trần không |

Tổng kết mức tự động hoá trên 10 bước vòng đời QC: **7 bước tự động hoàn toàn** (test data,
execution, failure detection, regression selection, reporting, aggregation, và phần *soạn* của bug
reporting); **3 bước còn human**, và cả ba nằm đúng chỗ **hành động không đảo ngược rẻ được** hoặc
**chuẩn mực bị thay đổi** `[R4 S13.7]`. Ba chỗ đó: duyệt diff `plan.yaml` · sửa golden set/baseline
· kết luận bất đồng LLM vs deterministic.

Chỗ đáng chú ý nhất trong bảng 7 điểm HITL: **sửa golden set / baseline là gian lận khó phát hiện
nhất** (sửa chuẩn cho test pass) ⟹ **không tự động hoá**, bắt buộc qua PR có review.

---

## 4.5 AI application là **capability hạng nhất**

Đây là chỗ không prior art nào phủ (xem `03-prior-art.md` §3.2). Trong kiến trúc này, kiểm thử AI
app **không phải một hệ riêng** — nó là `capability: llmapp.eval`, đi qua **cùng** task spec,
**cùng** result schema, **cùng** phép gộp verdict như `http.load`.

### Oracle strategy: thang 7 bậc, luôn lấy bậc CAO NHẤT còn áp dụng được

| Bậc | Oracle | Gate? |
|---|---|---|
| 1 | **Structural assert** — parse được, đúng schema, đủ trường | ✅ |
| 2 | **Property/invariant** — không rò secret, không gọi tool cấm, luôn có citation | ✅ |
| 3 | **Behavioral assert trên tool call** — đúng tool, đúng tham số, đúng thứ tự | ✅ |
| 4 | **Retrieval metric có nhãn** — recall@k / precision@k trên golden set | ✅ |
| 5 | **Reference-based text metric** — khi có đáp án đóng | ✅ |
| 6 | **LLM-as-judge** | ⚠️ **chỉ theo delta so baseline đã pin** |
| 7 | **Human review** | ❌ |

Nguyên tắc rút ra, và nó là chỗ hầu hết mọi người làm sai `[R2 S7.1]`:

> **Phần lớn thứ người ta định chấm bằng LLM ở bậc 6 thật ra chấm được ở bậc 1–3.** *"Chatbot trả
> lời có lịch sự không"* thì cần judge; *"agent có gọi `refund()` khi user chưa xác nhận không"* là
> **assert cứng** — và cái thứ hai mới là thứ làm hỏng production.

Áp vào toy app: cho endpoint `summarize`, bậc 1 (JSON đúng schema) + bậc 2 (summary không rỗng,
ngắn hơn body) **đã phủ được phần quan trọng**. G-Eval là bậc 6 và ở lại **advisory**.

**Bằng chứng chống LLM-judge làm ngưỡng tuyệt đối:** position bias đo trên **15 judge · 22 task ·
>150.000 instance**, và bias **mạnh nhất đúng lúc hai lời giải gần nhau về chất lượng** — tức
**đúng vùng của regression testing (A vs A')** `[R2 S7.4]`. Cộng self-preference bias ⟹ **judge
phải KHÁC model của hệ đang test**; adapter DeepEval **từ chối chạy** (`status: error`) nếu hai
model trùng, **không âm thầm chạy**.

### Bảy cấu phần mà QC truyền thống không cần `[R2 S7 kết luận]`

Đây là cái giá của việc coi AI app là hạng nhất. Nói rõ cái nào có trong PoC, cái nào không:

| # | Cấu phần | Trong PoC? |
|---|---|---|
| 1 | **SUT identity đa chiều** — code commit + prompt hash + model snapshot + corpus id + decoding params | ⚠️ bản tối thiểu (`runs/*/sut_identity.json`, `sut_id` vào `run_signature`); **chưa pin model snapshot id** |
| 2 | **Dataset store** (golden set có nhãn) | ✅ `tests/eval/golden.json` |
| 3 | **Baseline store** | ❌ **Không có** ⟹ chưa gate được theo delta ⟹ cột `baseline` trong report là **giá trị viết tay** |
| 4 | **Judge calibration** | ❌ `[KHÔNG KỊP SPRINT NÀY]` — cần tập nhãn human, việc nhiều tuần |
| 5 | **Sampling policy** | ❌ Không có |
| 6 | **Cost budget như assert hạng nhất** | ✅ `budget` là trần cứng trong task spec; `cost` lên report kể cả khi xanh |
| 7 | **Verdict provenance** | ✅ Đây là toàn bộ giá trị của bản propose |

**Rủi ro còn treo, không giấu:** provider đổi model sau **cùng một tên alias** ⟹ **regression im
lặng**: không có commit, không có PR, không có ai để đổ lỗi `[R2 S7.2]`. Đây là lý do
`pin model snapshot id` **không phải tuỳ chọn** — và PoC **chưa có** nó.

---

## 4.6 Tài sản vs chi phí biên — vì sao tỉ lệ này CHÍNH LÀ luận điểm kiến trúc

| **TÁI SỬ DỤNG** khi mở rộng — viết một lần | **VIẾT LẠI** cho từng worker/surface |
|---|---|
| `schemas/task_spec.json` (~80 dòng) — **bất biến** | Dịch `inputs` → tham số CLI (15–30 dòng) |
| `schemas/result.json` (~120 dòng) + từ vựng `verdict_source` 3 giá trị — **bất biến** | Đọc output thô → `metrics`/`findings` (20–50 dòng) |
| `core/registry.py` · `core/runner.py` · `core/verdict.py` · `core/report.py` · `core/evidence.py` | Cách áp `oracle` (10–30 dòng) |
| `adapters/_base.py` — adapter mới **kế thừa, chỉ override 2 hàm** | Map trạng thái tool → **4 trạng thái** (10–20 dòng) |
| `oracle/threshold.py`, `oracle/signals.py` — **bộ so sánh dùng chung** | `workers/<name>.yaml` (~25 dòng) |
| **Chính sách**: 2 lane · luật gộp · luật retry · 4 điểm escalation · 7 điểm HITL — **không phụ thuộc surface** | *(Với surface mới: cách bóc UI state và định nghĩa oracle phải viết lại từ đầu)* |
| **≈680 dòng — ĐÂY LÀ TÀI SẢN.** Nó tồn tại sau khi mọi worker hôm nay đã lỗi thời | **≈80–150 dòng/worker — chi phí biên** |

> **Nếu tỉ lệ này đảo ngược — mỗi worker mới cần sửa lõi — thì kiến trúc đã hỏng và chúng tôi chỉ
> đang viết một script gọi 4 tool** `[R5 1.3]`.

⚠️ **Hai con số 680/110 là ước lượng, không phải số đo, và có thể lệch hệ số 2** `[arch §8.2]`.
Cách chứng minh đúng là bằng bằng chứng: `git show --stat` cho commit thêm worker thứ 5 phải cho
thấy **`core/` không xuất hiện trong diff**. **Bằng chứng này chưa có trong repo** (STEP 44 —
worker `mock2` — chưa chạy), nên tiêu chí nghiệm thu #5 hiện **chưa đạt**.

---

## 4.7 Trạng thái sáu tiêu chí nghiệm thu — đo, không hứa

| # | Tiêu chí | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 | Một lệnh, exit code phản ánh verdict | ✅ | `r-0019`: `PASS`/`exit_code 0` · `r-0020`: `FAIL`/`exit_code 1` |
| **2** | ⭐ **4 adapter qua CÙNG một `result.json`, không trường riêng** | ✅ | `tools/validate.py result runs/r-0023/results/*.json` → **4/4 PASS**; trên `examples/` → **4/4 PASS**; result discovery của Midscene → **PASS** cùng schema |
| 3 | Report tách 3 mục; verdict tổng chỉ tính `deterministic_assert` | ✅ | `runs/r-0019/report.md`; G-Eval advisory hỏng mà verdict không đổi |
| 4 | **Tái lập** | ✅ | `diff_runs.py` → **IDENTICAL** trên **3/3** cặp liên tiếp `r-0020…r-0023`, cùng `run_signature f0df138d…` |
| 5 | **N5** (`core/` không xuất hiện khi thêm worker) | ❌ **chưa đạt** | STEP 44 (`mock2`) chưa chạy |
| 6 | **Canary** | ✅ | `docs/decisions.md` STEP 29: canary live → `status=fail`, `gating=false`, `element_not_found` |

**Ngưỡng ship:** đạt **1, 2, 3, 4** = thành công, present được. Thêm **5, 6** = present mạnh.
Hiện đạt **1, 2, 3, 4, 6**; thiếu **5**. **Không đạt #2 thì kiến trúc chưa được chứng minh, dù chạy
đẹp đến đâu** `[arch Phụ lục B]`.

---

## 4.8 Hệ thống này KHÔNG bắt được loại bug nào

Mục này ở đây có chủ đích. Một bản propose không nói giới hạn thì người review mặc định là team
chưa kiểm.

| Loại bug | Vì sao không bắt được |
|---|---|
| **Test đúng cho requirement SAI** | Sai ở bước 1 nhân lên toàn bộ hạ nguồn, và **không oracle nào phát hiện được** |
| **Bug logic khi spec không viết ra** | Oracle đến từ spec; spec không đầy đủ là **trạng thái mặc định của mọi team**. Agent lấp khoảng trống bằng cách **bịa** criteria hợp lý — và test bịa vẫn "pass", nên **không có tín hiệu báo động** |
| **Chất lượng cảm nhận của AI feature** | Gate chỉ dùng bậc 1–5. Summary tệ đi mà đúng schema + đúng độ dài ⟹ **gate xanh** |
| **Regression im lặng khi provider đổi model** sau cùng alias | Chỉ chặn được nếu pin model snapshot id — PoC **chưa có** |
| **Vùng không có trong `plan.yaml`** (V3) | Gate vẫn xanh, vẫn tái lập, và **vẫn vô nghĩa**. Đây là **cái giá của plan-as-artifact** |
| **Vùng bị né path** trong selection | Luật selection chặn được việc *bị thuyết phục*, **không** chặn được việc *né path* — đó là việc của `floor` và của nightly full run |
| Bug ngoài trần ~65% activity coverage của exploration | Exploration là **bổ sung**, không phải **thay thế** regression suite |

Và bốn chỗ còn phụ thuộc phán đoán LLM, kèm cái gì chặn từng chỗ:

| Chỗ | Ở đâu | Cái gì chặn |
|---|---|---|
| `plan-gen` sinh plan từ spec | **Ngoài CI**, khi spec đổi | **Người duyệt DIFF** (không duyệt từng test); tự merge diff chỉ **thêm** task, **bắt buộc người** khi diff **xoá/thu hẹp** |
| `findings[].rationale` của Midscene | Discovery lane | `gating: false` + trần cứng finding/run + tự tắt lane nếu không ai triage |
| G-Eval của DeepEval | Advisory | Không gate theo điểm tuyệt đối; chỉ theo delta; judge ≠ model của SUT |
| `failure-analysis` khi gate đỏ | Chỉ khi đỏ | Không hành động tự động; ticket là **`tickets_draft[]`**, người bấm một nút cho cả lô |
