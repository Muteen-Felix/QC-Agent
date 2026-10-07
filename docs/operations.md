# Vận hành qc-agent

Dành cho người trực gate: xoay khoá, đọc chi phí, xử lý khi Select/Jira/GT agent có chuyện, đổi nhà cung cấp LLM. Mỗi mục: **tình huống → lệnh → kỳ vọng → khi nào dừng**.
Chạy harness local và các kịch bản kiểm: [e2e-runbook.md](e2e-runbook.md). Cách dùng gate trong CI của repo SUT: [usage-ci.md](usage-ci.md).

Lệnh bên dưới chạy từ thư mục gốc repo qc-agent (hoặc trong image qc-agent). Lệnh có chữ `<...>` là chỗ bạn điền. Mục nào nói tới GitHub/Jira/LLM thật thì **chưa được kiểm chứng ở S4-07** (S4-06 nghiệm thu bằng sandbox thật).

## Event log

Log là **một dòng JSON trên stderr**, ghi qua `logging_setup.event`; không bao giờ có nội dung PRD, diff, prompt, response, rationale do LLM viết, mã nguồn SUT, key hay token. Chỉ có định danh, số lượng, token, thời gian và lý do ngắn (cắt 200 ký tự).
Đọc: `qc-agent select ... 2>&1 >/dev/null | jq 'select(.event=="selector.decision") | .fallback_reason'`; log của executor trộn với báo cáo: `jq -R 'fromjson? | select(.event)'`.
Bảng dưới là **nguồn của test**: `tests/test_log_events.py` quét mã nguồn bằng AST và đỏ nếu event hay trường lệch bảng này, hoặc nếu một trường có tên gợi nội dung (`prompt`, `body`, `diff`, `rationale`, `token`, ...) hoặc dựng bằng f-string/`str()`.

| Event | Khi nào | Trường |
|---|---|---|
| `llm.call` | Một lời gọi LLM đã gửi (INFO; WARNING khi lỗi) | `purpose`, `model`, `stop_reason`, `kind`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `duration_s` |
| `llm.agent` | Hết một lần chạy agent GT nhiều lượt | `purpose`, `model`, `stop`, `turns`, `nudges`, `tool_calls`, `cost_usd_est`, `duration_s`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` |
| `llm.retry` | Gemini thử lại sau 429/5xx/lỗi mạng (WARNING) | `purpose`, `model`, `retry`, `status`, `wait_s` |
| `llm.fallback` | Gemini chuyển sang model dự phòng (WARNING) | `purpose`, `model`, `kind` |
| `gt.cli` | Xong một lệnh `gt` (`generate`, `regen`, `import-xlsx`) | `command`, `test_cases`, `orphans`, `kept`, `added`, `removed`, `lost`, `changed`, `converted` |
| `gt.generate` | Xong bộ sinh một lời gọi | `stories`, `acs`, `test_cases`, `orphans`, `uncovered`, `dropped`, `merged`, `attempts`, `cache_hit`, `unknown_calls`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` |
| `gt.agent` | Xong bộ sinh agent | `stories`, `acs`, `test_cases`, `orphans`, `dropped`, `turns`, `stop`, `completed`, `files_read`, `bytes_read`, `submissions`, `dropped_in_loop`, `finish_rejections`, `waivers`, `spec_conflicts` |
| `gt.validate` | Xong `gt validate` | `errors`, `warnings` |
| `gt.cache` | Cache kết quả GT: `outcome` ∈ `hit`, `miss`, `store`, `store_failed`, `skipped` (agent không dùng cache, kèm `reason=agent`) | `outcome`, `key`, `reason` |
| `gt.token_cap` | Đầu vào GT vượt `QC_LLM_MAX_INPUT_TOKENS` nên không gọi LLM (WARNING) | `estimate`, `cap`, `acs` |
| `selector.prune` | Diff đã rút gọn trước khi gửi Diff Agent | `files`, `approx_tokens`, `truncated`, `kinds` |
| `selector.decision` | Quyết định cuối của Select | `source`, `full_set`, `fallback_reason`, `workers`, `suites`, `files` |
| `selector.cache` | Cache Select: `outcome` ∈ `hit`, `miss`, `store`, `store_failed` | `outcome`, `key` |
| `selector.token_cap` | Đầu vào Select vượt trần nên chạy FULL SET | `estimate`, `cap`, `files` |
| `review.posted` | Đã đăng review inline lên PR | `comments`, `outside_diff` |
| `review.skipped` | Không đăng: trùng (`reason=duplicate`) hoặc lỗi (`reason=<tên lớp lỗi>`) | `reason`, `findings` |
| `jira.sync` | Xong đồng bộ finding Low sang Jira | `created`, `duplicates`, `remaining`, `errors`, `status` |

`key` của event cache là **8 ký tự đầu của mã băm** (không phải nội dung). Ngoài bảng còn event vòng đời `run.*`, `task.*`, `job.*` (tên, trạng thái, lý do ngắn).
`pr_review` và `jira` chạy bằng `python -m` trong workflow nên tự cấu hình log ở điểm vào; trước S4-07 hai điểm vào này không cấu hình nên event `review.*` và `jira.sync` không bao giờ hiện ra ở CI.
Không có event khi review/Jira bị bỏ qua vì **không có finding** hay **thiếu cấu hình/secret**: kết quả nằm ở `jira-status.json` trong thư mục run và ở dòng JSON in trên stdout.

**Đổi tên đã làm ở S4-07 (cũ → mới):** `gt.cache` `skipped="agent"` → `outcome="skipped"`, `reason="agent"`. Không đổi tên event nào khác. Ai đang lọc `jq 'select(.skipped)'` phải đổi sang `select(.outcome=="skipped")`.

### File nào được chứa gì

Test chuỗi đánh dấu (`tests/test_log_no_content_chain.py`) chạy trọn chuỗi gt generate → gt agent → select (Claude và Gemini) → run → pr_review → jira → ci với server giả và mỗi kênh một chuỗi đánh dấu riêng, rồi đòi: **không marker nào ở stderr của bất kỳ bước nào**, và trong `runs/` + thư mục egress marker chỉ được nằm ở file cho phép dưới đây.

| Dữ liệu | Được nằm ở | Không được nằm ở |
|---|---|---|
| Rationale của Select (đã làm sạch bằng `selector.agent._clean`) | `selection.json`, `plan.yaml` (selection là một phần plan text để hash vào `plan_id`), `report.md` | stderr, `report.json`, `results/`, `specs/`, `egress.jsonl`, `llm_usage.json` |
| Tiêu đề finding (đã làm sạch bằng `clean_md`) | `report.md`, `report.json`, Check Run/comment/review ở GitHub | stderr, `egress.jsonl` |
| Nội dung PRD, diff, mã nguồn SUT | Không file nào dưới `runs/` hay thư mục egress (chỉ rời máy tới LLM, và egress ghi **loại dữ liệu**, ví dụ `source_code`, không ghi nội dung) | stderr, mọi file dưới `runs/`, `egress.jsonl` |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GITHUB_TOKEN`, `JIRA_API_TOKEN` | Không đâu (chỉ biến môi trường của tiến trình) | stderr, stdout, mọi file |
| Body lỗi của Jira/Gemini | Không đâu (chỉ `HTTP <mã>` hoặc tên lớp lỗi) | stderr, mọi file |

Nếu thêm một file dưới `runs/` có chứa rationale hoặc nội dung, phải sửa `ALLOWED` trong test **và** bảng này trong cùng PR (có lý do), không được nới lặng lẽ.

## Xoay khoá

| Khoá | Đặt ở đâu | Xoay thế nào |
|---|---|---|
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | Secret của repo (hoặc org) SUT; caller dùng `secrets: inherit` | 1. Tạo khoá mới ở console nhà cung cấp. 2. `gh secret set ANTHROPIC_API_KEY --repo <chủ>/<repo-sut>` (hoặc `--org`). 3. Chạy lại một PR và xem Select: `jq 'select(.event=="llm.call") | .kind'` phải rỗng (không lỗi). 4. Thu hồi khoá cũ. |
| `JIRA_API_TOKEN` (cùng `JIRA_BASE_URL`, `JIRA_EMAIL`) | Như trên | Tạo token mới ở Atlassian, `gh secret set JIRA_API_TOKEN ...`, chạy lại PR có finding Low, xem `jira.sync`: `status` là `ok`, `errors` là 0. Thu hồi token cũ. |
| Khoá GitHub của bot GT (`qc_bot_token`, tuỳ chọn) | Secret repo SUT | Như trên; thử bằng `workflow_dispatch` của workflow GT. |

Khoá đọc từ `os.environ` lúc gọi, không vào `Settings` hay log. Khoá thiếu: Select ra `fallback_reason=missing_api_key`; khoá sai (HTTP 401/400) ra `bad_output`; hết hạn mức/5xx ra `unavailable`. Cả ba đều **chạy FULL SET, gate không đỏ vì khoá** (xem mục "FULL SET tăng đột biến"). Jira sai khoá chỉ làm `jira.sync` có `errors=1`, verdict giữ nguyên.

## Đọc chi phí LLM

Một run có `runs/<run_id>/llm_usage.json`: **mảng JSON, mỗi lời gọi đã gửi một phần tử, không có nội dung** (`purpose`, `model`, `prompt_version`, các số token, `est_usd`, `cache_hit`, `duration_s`). `report.md`, Check Run và comment PR dùng chung **một** dòng chi phí, dạng `LLM: 1 200 in (0 từ cache) / 40 out · ~$0.0014`.
- `null` nghĩa là **không có dữ liệu**, không phải 0: `est_usd` của mọi `gemini-*` là `null`; `usage_known: false` khi đã gửi nhưng không có response (timeout: server có thể đã tính phí).
- Dòng `cache_hit: true` giữ số của lần tạo nhưng **không cộng vào tổng**.
- Giá là **ước tính** theo bảng cache, không phải hoá đơn: đối chiếu hoá đơn ở console nhà cung cấp.
- Xem nhanh: `python -c "import json,sys; [print(r['purpose'], r['model'], r['input_tokens'], r['output_tokens'], r['est_usd'], r['cache_hit']) for r in json.load(open(sys.argv[1]))]" runs/<run_id>/llm_usage.json`.
- Chi phí GT agent: dòng `llm.agent` có `cost_usd_est` và `turns`; trần ở `QC_GT_AGENT_MAX_COST_USD`.

## FULL SET tăng đột biến

Triệu chứng: nhiều PR đột nhiên chạy mọi suite (lâu hơn, nhiều `skipped`). Nguyên nhân nằm ở `selector.decision.fallback_reason` (đều **không** làm gate đỏ):

| `fallback_reason` | Nghĩa | Làm gì |
|---|---|---|
| `missing_api_key` | Select không có `ANTHROPIC_API_KEY` | Kiểm secret có truyền không (mục "Xoay khoá"). Cố ý tắt Select cũng ra lý do này. |
| `unavailable` | 429/529/5xx/hết quota | Xem `llm.call` có `kind`; chờ nhà cung cấp hoặc kiểm hạn mức. |
| `timeout` | Quá `QC_LLM_TIMEOUT_S` (mặc định 120 giây) | Kiểm mạng; tăng timeout chỉ khi diff thật sự lớn. |
| `bad_output` | LLM trả sai schema/bị từ chối, hoặc API trả 400/401 (khoá sai) | Có `llm.call` với `kind` đi kèm: nếu là 401 thì xoay khoá; nếu không thì hiếm, gom `selector.decision` để xem tần suất và báo người giữ prompt. |
| `unknown_worker` | LLM chọn worker ngoài allowlist của policy | Như `bad_output`. |
| `egress_denied` | Chính sách egress từ chối | Kiểm `data_egress` trong manifest/chính sách. |
| `token_cap` | Đầu vào ước lượng vượt `QC_LLM_MAX_INPUT_TOKENS` (mặc định 100000) | Diff quá lớn: chấp nhận FULL SET, hoặc nâng trần có chủ đích (tốn tiền). |

Đếm theo lý do trong một lô log: `jq -r 'select(.event=="selector.decision") | .fallback_reason // "none"' | sort | uniq -c`. Event `selector.token_cap` kèm `estimate`/`cap` cho biết vượt bao nhiêu.
Diff chạm `full_set_paths` của policy, hoặc diff không có `base`/`head`, cũng ra FULL SET mà **không** phải sự cố.

## Khi Jira hỏng

Jira không bao giờ đổi verdict. Xem `runs/<run_id>/jira-status.json` hoặc event `jira.sync` (`status`, `errors`):
- `skipped: thiếu cấu hình Jira hoặc secret`: thiếu `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`, hoặc policy thiếu `jira.project_key`, hoặc URL không phải `https` (`http` chỉ nhận tới loopback). Policy mặc định `_default.yaml` **không có** `jira.project_key`, nên repo chưa đăng ký không có Jira.
- `error: HTTP 401/403`: token/quyền sai, xoay khoá. `HTTP 5xx`: Jira đang lỗi, lần chạy sau tự tra trùng bằng nhãn `qcagent-<fingerprint>` rồi mới tạo.
- `remaining` > 0: vượt `jira.max_new_per_run` (mặc định 20); phần còn lại được tạo ở lần sau.
- Tắt hẳn: bỏ ba secret Jira; không cần sửa workflow.

## Làm sạch cache

Cache chỉ lưu kết quả LLM đã qua kiểm schema; floor, rules và FULL SET luôn tính lại, và lỗi (529, timeout...) không bao giờ được lưu.
- **Local/CLI:** thư mục `QC_SELECT_CACHE_DIR` (mặc định `~/.cache/qc-agent/select`) và `QC_GT_CACHE_DIR` (mặc định `~/.cache/qc-agent/gt`). Xoá thư mục, hoặc đặt `none` (hoặc `off`, rỗng) để tắt cache.
- **CI:** cache là `actions/cache` với khoá `qc-select-<project>-<sha>-...` và `qc-gt-<project>-<prd_id>-<prd_sha256>-...`. Xem: `gh cache list --repo <chủ>/<repo-sut> --key qc-select-<project>`; xoá: `gh cache delete <id>`. Đổi `prompt_version` hoặc model làm khoá cache đổi nên không cần xoá tay.
- Agent GT **không dùng** cache (event `gt.cache` ra `outcome=skipped`, `reason=agent`).

## Rollback: tắt Select

Không truyền `ANTHROPIC_API_KEY` cho gate. Select ra `fallback_reason=missing_api_key` và gate chạy **FULL SET** (đúng kết quả của lúc chưa có Select, chỉ chậm hơn). PR chỉ sửa tài liệu vẫn được chọn bằng rules mà không cần khoá. Muốn bật lại: đặt lại secret. Không cần sửa workflow hay `selection.json`; `core/` luôn gộp lại floor (secrets + sast) lần hai nên không có đường nào bỏ được floor.

## Cập nhật bảng giá

Bảng giá **duy nhất** nằm ở `src/qc_agent/llm/prices.py` (`PRICES` theo USD/1 triệu token: đầu vào, đầu ra, đọc cache; ghi cache = 1,25 × đầu vào; `PRICES_DATE`). Khoá dài xếp trước để `claude-sonnet-5-5` không bị bắt bởi `claude-sonnet-5`.
1. Sửa `PRICES` và `PRICES_DATE` theo tài liệu giá của nhà cung cấp (ghi nguồn trong commit).
2. `pytest tests/test_llm_usage.py -q` (có test "chỉ có một bảng giá").
3. Model không có trong bảng (mọi `gemini-*`) cho `est_usd=null`, không đoán: muốn có giá Gemini phải thêm khoá thật vào bảng.

## Đổi nhà cung cấp LLM (Claude ↔ Gemini)

**Chỉ một công tắc: tiền tố tên model.** `gemini-*` → Gemini (khoá `GEMINI_API_KEY`, tuỳ chọn `GEMINI_BASE_URL`); còn lại → Claude (khoá `ANTHROPIC_API_KEY`). Đặt bằng `QC_GT_MODEL` (sinh GT) và `QC_SELECTOR_MODEL` (Diff Agent). Không có biến thứ hai.
- Mặc định trong code: `QC_GT_MODEL=claude-sonnet-5`, `QC_SELECTOR_MODEL=claude-haiku-4-5-20251001`. `.env.example` đang đặt mẫu Gemini (bản miễn phí) nên **bản copy `.env` không phải mặc định của code**.
- Biến **chỉ Gemini** (Claude bỏ qua): `QC_LLM_MAX_RETRIES` (mặc định 5), `QC_LLM_MIN_INTERVAL_S` (mặc định 0; 12 ≈ 5 RPM, 6 ≈ 10 RPM), `QC_LLM_FALLBACK_MODELS` (danh sách phân tách bằng dấu phẩy), `QC_GEMINI_THINKING_LEVEL` (`minimal|low|medium|high`). Quan sát: `llm.retry` và `llm.fallback`. Claude không tự retry.
- `est_usd` của Gemini là `null` (xem "Đọc chi phí LLM").
- **Trong CI:** workflow GT có input `model` (`gemini-*` đòi secret `GEMINI_API_KEY`) cùng `llm_min_interval_s`, `llm_max_retries`, `llm_fallback_models`. Bước **Select (PR)** của workflow gate chỉ truyền `ANTHROPIC_API_KEY` và `QC_SELECT_CACHE_DIR` vào container, **không** truyền `QC_SELECTOR_MODEL` hay `GEMINI_API_KEY`: ở CI Select dùng Claude (mặc định của code). Đổi Select sang Gemini ở CI cần sửa workflow, việc ngoài phạm vi vận hành này.

## GT agent

Agent đọc PRD + **mã nguồn SUT** + OpenAPI qua nhiều lượt và **gửi mã nguồn ra ngoài máy** (egress `source_code`, tới Anthropic): **chỉ bật khi câu hỏi #3 của dự án đã cho phép**. Agent **chưa từng chạy với API thật** (chỉ test với server giả): lần đầu chạy thật phải theo dõi ngân sách và `llm.agent`.
- Bật/tắt: `qc-agent gt generate|regen --agent` cho một lần; `QC_GT_GENERATOR=agent` làm mặc định; `--no-agent` ép về bộ sinh một lời gọi. Trong CI: input `agent: true` của workflow GT.
- Ngân sách (hết là dừng và trả bộ dở kèm cảnh báo, không raise): `QC_GT_AGENT_MAX_COST_USD` (3), `QC_GT_AGENT_MAX_TURNS` (40), `QC_GT_AGENT_MAX_WALL_S` (1800), `QC_GT_AGENT_MAX_READ_BYTES` (3000000), `QC_GT_AGENT_TIMEOUT_S` (600). Model: `QC_GT_AGENT_MODEL` (`claude-sonnet-5-5`); chỉ nhận model Claude. CI có `agent_model`, `agent_max_turns`, `agent_max_cost_usd`.
- Theo dõi: `gt.agent` (`turns`, `stop`, `completed`, `files_read`, `bytes_read`, `finish_rejections`...) và `llm.agent` (`cost_usd_est`). `stop` khác `finished` hoặc `completed=false` nghĩa là bộ test case chưa đủ: QA phải xem `uncovered`/cảnh báo trước khi duyệt.
- Thư mục egress `egress.jsonl` ghi **loại dữ liệu** đã rời máy (`source_code`) cho từng lời gọi; không ghi nội dung.

## SUT cần database (S4-09)

Workflow gate có DB phụ khi khai `sut_db_image` (**ghim digest**), `sut_db_env`, và `sut_db_ready_cmd` hoặc `sut_db_port`: container `db` chạy trong mạng `qc-net` trước SUT, không publish cổng ra runner. Bí mật nằm ở hai secret `KEY=VALUE` nhiều dòng: `SUT_SECRET_ENV` (cho SUT, ví dụ `DATABASE_URL=postgresql://app:<mật khẩu>@db:5432/app`) và `SUT_DB_SECRET_ENV` (chỉ cho DB, ví dụ `POSTGRES_PASSWORD=<mật khẩu>`). Hai file env tạm (quyền 600) bị xoá ở bước cleanup.
**Xoay mật khẩu DB:** đổi **cả hai** secret cùng lúc và cùng giá trị (mật khẩu trong `DATABASE_URL` phải bằng `POSTGRES_PASSWORD`), rồi chạy lại một PR. Đổi một nửa làm SUT không kết nối được: Start SUT đỏ với lỗi sẵn sàng, gate không chạy.
DB là dịch vụ của **gate**; luồng PR và manual không phụ thuộc `QC_DATABASE_URL` (đó là DB của dịch vụ API/executor, khác hẳn).

## Image và kiểm tra gói

- Image đọc/ghi được `test-cases.xlsx` và mang đủ prompt, template, schema, bảng giá; `tests/test_packaging_resources.py` kiểm bản wheel, `tests/test_packaging_image.py` kiểm image (cần Docker).
- Job Summary của workflow `image` in digest và lệnh ghim cho hai caller (`qc.yml` và `qc-groundtruth.yml`): ghim theo **digest**, không theo tag.
- Không có cờ `qc-agent --version`; xem phiên bản: `docker run --rm --entrypoint python <image> -c "import importlib.metadata as m; print(m.version('qc-agent'))"`.
