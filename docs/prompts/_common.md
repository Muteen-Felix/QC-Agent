# Ngữ cảnh chung cho mọi prompt triển khai

Đọc file này TRƯỚC khi làm bất kỳ bước nào. Bạn đang triển khai QC-Agent v2 theo `docs/implementation-plan.md` (gọi tắt là *plan*).
Mỗi prompt trong `docs/prompts/` = một phiên làm việc mới = một PR. File này là phần chung, còn prompt của từng bước chỉ nói phần riêng của bước đó.

---

## 1. Nguồn sự thật (theo thứ tự ưu tiên)

1. **Code hiện tại trên nhánh của bạn**: đọc code trước khi tin tài liệu.
2. **`docs/prompts/README.md`, mục "Hiệu chỉnh so với plan"**: những chỗ plan sai so với API hoặc code thật đã được sửa ở đó, và mục này được ưu tiên hơn plan.
3. **Plan**: phạm vi, task con, DoD.
4. `docs/requirement-spec.md` (SRS), `CLAUDE.md`, `docs/core-rules.md`, `docs/architecture.md`.

Khi thấy plan mâu thuẫn với code mà prompt chưa giải quyết thì **dừng lại**. Nêu mâu thuẫn, đưa ra 2 phương án kèm đề xuất, rồi chờ tôi chọn. Không tự ứng biến thay đổi thiết kế lớn.

## 2. Luật cứng (vi phạm thì PR bị trả lại)

- `core/` không gọi LLM, không có `if worker == ...`, và không import `qc_agent.llm`, `qc_agent.groundtruth`, `qc_agent.selector` ở top-level. Lệnh CLI mới trong `core/cli.py` dùng **import lười**, giống cách `init`/`validate`/`doctor` đang làm trong `core/cli.py: main`.
- LLM chỉ được xuất hiện ở ba chỗ:
  - **Worker**: chỉ cho finding không chặn.
  - **`groundtruth/`**: sản phẩm là file, đi qua PR và QA duyệt.
  - **`selector/`**: sản phẩm duy nhất là `selection.json`. `core/` luôn gộp floor lại lần hai.
- Không sửa file contract (`schemas/task_spec.json`, `schemas/result.json`, `workers/_template.yaml`), trừ ở bước S3-01 theo quy trình SemVer. Sau mọi bước khác, `python tools/freeze_contract.py --check` phải exit 0.
- Dữ liệu đến từ PR, PRD, diff hay SUT đều là **KHÔNG TIN CẬY**. Cách xử lý:
  - đặt trong vùng phân cách khi đưa vào prompt;
  - ép output của LLM vào schema và enum allowlist;
  - làm sạch trước khi đưa vào Markdown (`integrations/github.clean_md`);
  - không đưa vào lệnh shell: truyền dạng argv, không dùng `shell=True`;
  - không nhúng vào code Python được sinh ra.
- Log là JSON một dòng trên stderr, ghi qua `logging_setup.event`. **Không bao giờ** log nội dung PRD, diff, prompt, response của LLM, rationale do LLM viết, API key hay token. Exception không được kèm body response hay URL có chứa khoá (mẫu: `scaffold/suggest.py: call_llm`).
- Mọi lời gọi ra ngoài (LLM, `count_tokens`, Jira) phải gọi `core/egress.record(...)` **trước khi gửi**. Nếu quyết định là `deny` thì không được có request nào (mẫu: `scaffold/suggest.py: suggest_flows`).
- Retry: chỉ retry `error` (lỗi hạ tầng), đúng 1 lần. Không bao giờ retry `fail`.
- Gate không bao giờ đỏ vì LLM, chi phí hay Jira:
  - LLM lỗi thì chạy FULL SET;
  - Jira hoặc GitHub lỗi thì chỉ ghi cảnh báo, verdict giữ nguyên.
- Không viết migration drop. Luồng PR và luồng manual không được phụ thuộc `QC_DATABASE_URL`.
- Không thêm dependency mới (`httpx`, `PyYAML`, `jsonschema` đã có sẵn), trừ khi prompt cho phép. Nếu thấy cần thì hỏi.
- Trong YAML plan/suite, quote khoá `"on":`. Action và image ghim theo SHA/digest; tái dùng các SHA đã có trong `.github/workflows/qc-gate.reusable.yml`.
- Tài liệu và comment viết tiếng Việt, identifier viết tiếng Anh. Chỉ comment khi cần giải thích "vì sao".
- Chạy mọi lệnh từ gốc repo. Adapter được spawn bằng `python -m`, và đường dẫn trong plan là tương đối.

## 3. Hợp đồng giữa các bước (tên và đường dẫn chuẩn)

Các phiên làm việc không nhìn thấy nhau, nên mọi bước phải dùng đúng các tên dưới đây. Nếu bước của bạn buộc phải đổi một mục, hãy sửa bảng này **trong cùng PR** và nêu rõ trong báo cáo.

| Thứ | Giá trị chuẩn | Tạo ở | Dùng ở |
|---|---|---|---|
| LLM client | `qc_agent.llm.client.call_tool(...) -> ToolCall` (`.data`, `.usage`, `.model`, `.stop_reason`, `.duration_s`) · `LLMError.kind ∈ {missing_key, egress_denied, timeout, unavailable, bad_request, bad_output, refused}` | S1-01 | S1-04, S2-04, S4-03 |
| Env LLM | `ANTHROPIC_API_KEY` · `ANTHROPIC_BASE_URL` (tuỳ chọn, trỏ vào fake server khi test) · `QC_GT_MODEL=claude-sonnet-5` · `QC_SELECTOR_MODEL=claude-haiku-4-5-20251001` · `QC_LLM_TIMEOUT_S` | S1-01 | mọi bước có LLM |
| Env Gemini | provider chọn theo tiền tố `gemini-*` · `GEMINI_API_KEY` · `GEMINI_BASE_URL` · `QC_LLM_MAX_RETRIES` · `QC_LLM_MIN_INTERVAL_S` · `QC_LLM_FALLBACK_MODELS` · `QC_GEMINI_THINKING_LEVEL` (chỉ Gemini) | sau S1 | S4-03, S4-07 |
| GT agent | `qc_agent.llm.agent_loop.run_agent(...) -> AgentRun` · `groundtruth/agent.py` · `gt generate/regen --agent` hoặc `QC_GT_GENERATOR=agent` · `QC_GT_AGENT_MODEL` · `QC_GT_AGENT_MAX_COST_USD` / `_MAX_TURNS` / `_MAX_WALL_S` · egress `source_code` | sau S1 | S4-02, S4-03, S4-07 |
| Fake Anthropic | `tests/fakes.py: FakeAnthropic` (HTTP server, phát response từ fixture, đếm số lời gọi) | S1-06 | S1-08, S2-04/05/07, S4-02/05 |
| GT catalog | `<sut>/.qc-agent/ground-truth/test-cases.yaml`, theo schema `schemas/ground_truth.json` (gốc = catalog QA đọc/sửa; `$defs/emit_test_cases` = dạng LLM phát ra) | S1-02 / S1-05 | S1-06, S1-08 |
| GT schema API | `qc_agent.groundtruth.schema`: `catalog_schema()`, `emit_schema()` (đã inline `$ref`), `validate_catalog(data) -> list[str]`, `validate_module_map(data)`. Lỗi chỉ có "đường/dẫn (từ-khoá)", không kèm giá trị | S1-02 | S1-04, S1-05, S1-06 |
| PRD đã parse | `qc_agent.groundtruth.prd.parse_prd(path, *, openapi_source=None) -> ParsedPRD(prd_id, sha256, format, stories, endpoints, warnings, text)`; `GTInputError` → exit 3. `endpoints` = `scaffold.openapi.endpoints()` | S1-02 | S1-04, S1-06 |
| GT tests | `<sut>/.qc-agent/ground-truth/tests_gt/` (`conftest.py` runtime + `test_<story>.py`) | S1-05 | worker `pytest` |
| Module map | `<sut>/.qc-agent/ground-truth/module-map.yaml`, theo schema `schemas/module_map.json` | S1-05 | S2-02, S2-04 |
| Suite GT | `<sut>/.qc-agent/suites/gt-functional.yaml` · task `t-030` · capability `api.functional` · worker `pytest` | S1-05 | S1-08 |
| Worker pytest | `workers/pytest.yaml` · metric `pytest.tests / .passed / .failures / .errors / .skipped` (luôn đủ key) | S1-03 | S1-05 |
| Dải `task_id` | `t-001` api-contract · `t-010…t-019` security (`t-010` sast, `t-011` secrets, `t-012` deps) · `t-020…t-029` integration · `t-030…t-039` ground-truth | — | — |
| Selection | schema `schemas/selection.json` · file `selection.json` (`qc-agent select --out`) · bản chép ở `runs/<run_id>/selection.json` · nội dung nằm trong plan text (key `selection`) để được hash vào `plan_id` | S2-01 | S2-04…S4 |
| Policy keys | S2: `modes.<mode>.floor_workers`, `.full_set_paths`, `.docs_paths`, `.max_parallel` · S3: `severity`, `jira` | S2-02, S3-02, S3-06 | — |
| CLI | `qc-agent gt generate/validate/regen` · `qc-agent select` · `qc-agent run --trigger pr/manual --workers a,b --selection FILE` · `--warn-exit` (S3; `--yellow-exit` là alias deprecated) | — | — |
| Verdict (từ S3) | `BLOCKED` (exit 1) · `PASSED_WITH_WARNINGS` (exit = `--warn-exit`, mặc định 0) · `PASSED` (0) · lỗi hệ thống/plan: 3 | S3-03 | S3-05…S4 |
| Finding chuẩn hoá (từ S3) | `report.json.findings[]` = `{fingerprint, severity, task_id, suite, worker, rule_id, title, path, line, end_line, lane, verdict_source}` | S3-02 / S3-03 | S3-05, S3-06 |
| Log event | `llm.*`, `gt.*`, `selector.*`, `review.*`, `jira.*`, ghi qua `logging_setup.event` | — | — |
| Cache (S4) | `selector/cache.py` · `QC_SELECT_CACHE_DIR` (mặc định `~/.cache/qc-agent/select`) · `QC_GT_CACHE_DIR` · `selection.json.llm.cache_hit` · event `selector.cache`, `gt.cache` | S4-02 | S4-05, S4-06, S4-07 |
| Chi phí LLM (S4) | `src/qc_agent/llm/prices.py` (bảng giá DUY NHẤT, chuyển từ `agent_loop.PRICES`) · `QC_LLM_MAX_INPUT_TOKENS` · `fallback_reason: token_cap` · `runs/<run_id>/llm_usage.json` · `client.count_tokens(...)` (chỉ Claude) | S4-03 | S4-04, S4-06, S4-07 |
| SUT có DB (S4) | input DB của `qc-gate.reusable.yml` + một secret gom `KEY=VALUE` cho SUT/DB (tên chốt ở S4-09); container `db` trong mạng `qc-net` | S4-09 | S4-01, S4-06, S4-07 |
| Harness (S4) | `tools/run_reusable_locally.py --base-sha` (SHA thật) · `--anthropic-api` · `--jira-api` · `--scenario full-chain` | S4-05b, S4-05 | S4-06 |

## 4. Quy trình mỗi bước

1. Kiểm **tiền điều kiện** ghi ở đầu prompt: file hoặc lệnh của bước trước phải có trên nhánh hiện tại. Nếu thiếu thì dừng và báo.
2. Chạy `git status`; cây làm việc phải sạch. Tạo branch với tên ghi trong prompt, từ `main` mới nhất.
3. Đọc các file ở mục "Đọc trước" của prompt.
   - Nếu bước có quyết định thiết kế, trình bày kế hoạch trong tối đa 15 dòng (file sẽ sửa, interface, test) rồi mới code.
   - Mục nào ghi **HỎI TRƯỚC** thì phải hỏi và chờ tôi trả lời.
4. Viết test trước hoặc cùng lúc với code. Không làm việc của bước khác (xem mục "Ngoài phạm vi").
5. Chạy cổng kiểm ở mục 5 và sửa tới khi xanh. Không tắt hay skip test để được xanh.
6. Trong cùng PR, cập nhật tài liệu bị ảnh hưởng:
   - `CLAUDE.md` (phần Commands/Architecture) nếu thêm lệnh hoặc env;
   - `.env.example`;
   - các file trong `docs/` liên quan.
7. Đánh dấu `[x]` cho các task con đã xong trong plan. Chỉ sửa checkbox, không sửa nội dung plan. Checkbox DoD chỉ được tick ở phiên nghiệm thu (`dod-verify.md`).
8. Commit trên branch theo conventional commit, mô tả bằng tiếng Việt, ví dụ `feat(llm): client Messages API dùng chung`. **Không push, không mở PR** khi chưa hỏi tôi.

## 5. Cổng kiểm chung (bắt buộc ở mọi bước)

Bash (Linux/CI, dấu phân cách là `:`):

```bash
pytest -q
python tools/freeze_contract.py --check
QC_WORKERS_PATH="workers:tests/fixtures/workers" qc-agent --plan tests/fixtures/plans/demo.yaml; echo $?        # phải 0
QC_WORKERS_PATH="workers:tests/fixtures/workers" qc-agent --plan tests/fixtures/plans/demo_fail.yaml; echo $?   # phải 1
```

PowerShell (Windows, dấu phân cách là `;`):

```powershell
$env:QC_WORKERS_PATH = "workers;tests/fixtures/workers"
qc-agent --plan tests/fixtures/plans/demo.yaml; $LASTEXITCODE        # phải 0
qc-agent --plan tests/fixtures/plans/demo_fail.yaml; $LASTEXITCODE   # phải 1
```

- Test cần Postgres (`test_jobs_db.py`, `test_executor.py`, `test_api.py`, `test_web.py`) phải có `QC_TEST_DATABASE_URL` (bật bằng `docker compose up -d postgres`). Nếu không có thì ghi rõ trong báo cáo là đã skip.
- Bước nào đụng tới log phải có test chứng minh log không chứa nội dung nhạy cảm. Mẫu: `tests/test_logging.py::test_secrets_and_spec_inputs_never_appear_in_logs`. Cách làm: đặt một chuỗi đánh dấu duy nhất vào PRD/diff/key giả, rồi assert chuỗi đó không có trong stderr.
- **Không gọi LLM thật trong test.** Chỉ chạy LLM thật (tốn tiền) khi prompt yêu cầu VÀ tôi đồng ý ngay trong phiên, sau khi bạn đã báo ước tính chi phí.
- Không đụng tới hệ thống bên ngoài (push, mở PR, tạo issue Jira thật, sửa branch protection thật) khi chưa được tôi xác nhận cho từng hành động.

## 6. Báo cáo cuối phiên (đúng mẫu này, ngắn gọn)

```
## <mã bước> — <tên>
Branch: … · Commit: <sha ngắn> · Chưa push
Đã làm: 3–6 gạch đầu dòng
Quyết định tự chọn (cần anh/chị duyệt): …
Kiểm tra: lệnh → kết quả (exit code / số test pass-skip)
DoD của plan liên quan: mục → đạt / chưa (lý do)
Việc để lại cho bước sau / rủi ro: …
```
