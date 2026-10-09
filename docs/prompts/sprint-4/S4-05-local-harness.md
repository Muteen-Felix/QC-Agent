# S4-05 · Harness local chạy trọn chuỗi (plan S4.5)

- Branch: `feat/s4-05-local-harness`
- **Nhánh gốc: HEAD của `feat/s4-04-pruner-tuning`** (đã chốt): harness phải kiểm cả pruner và định dạng payload của S4-04. Hệ quả: mang theo S4-04 chưa nghiệm thu; harness dùng LLM giả nên không nói gì về recall.
- Tiền điều kiện: S4-01 đến S4-03 và **S4-05b** đã có trên nhánh làm việc (S4-05b cho `tools/run_reusable_locally.py` truyền SHA thật; bước này xây tiếp trên đó, không làm song song vì cùng sửa một file). Máy cần Docker.
- **Chưa implement**: prompt này đi kèm `PLAN.md` (L2, rev.2). Spike bước 0 đã chạy (2026-10-07) và đã sửa plan/prompt theo kết quả; **không viết code harness trước khi rev.2 được review và duyệt**.
- **Kết quả spike đã áp vào prompt** (số liệu gốc: bảng S1–S8 cuối `PLAN.md`): N3 viết lại (mục 3, nợ test chỉ có khi operation nằm trong `exclude_path`); lockfile của fixture phải có **một dependency prod** (`left-pad@1.3.0`, Trivy trả `Results` rỗng với lockfile trống nên `deps` ra error); DB Trivy trong image dựng từ cache có thể cũ (9 ngày ở lần build thử) nên có preflight (mục 4).

## Đọc trước

- `docs/prompts/_common.md`; plan S4.5, S4.6 (5 kịch bản A–E)
- `tools/run_reusable_locally.py` (`run_workflow`, `--policy-dir`, `--runner-temp`, `--github-api`, `--base-sha`, `step_env`, cách bỏ qua step "Pull qc-agent image")
- `tests/fakes.py` (`FakeGitHub`, `FakeJira`, `FakeAnthropic`: hiện bind `127.0.0.1`), `tests/test_reusable_workflow.py`, `tests/test_select_success_container.py` (mẫu repo git thật đặt trong `tests/.docker-workspaces/`), `tests/test_gt_workflow_local.py`
- `docs/prompts/README.md`, hiệu chỉnh #15 (harness chỉ chạy được job `validate` của workflow GT) và #16 (`docker build` lỗi TLS trên mạng công ty)
- `.github/workflows/qc-gate.reusable.yml`, `.github/workflows/qc-groundtruth.reusable.yml`
- `Dockerfile` (có sẵn `ARG QC_AGENT_GIT_SHA` → `ENV QC_AGENT_GIT_SHA`), `configs/projects/_default.yaml`, `tests/fixtures/selector-datasets/node-api/` (điểm xuất phát của fixture thứ hai, **không sửa**)
- Kết quả S4-04 về `_default.yaml` (mục "Phát hiện cần tôn trọng" bên dưới)

## Mục tiêu

Chạy **cùng một chuỗi** với E2E thật ngay trên máy local, không cần GitHub, Jira hay Anthropic thật:

```
PRD → gt generate (fake LLM) → thân PR → (giả lập QA duyệt) → PR của dev → Select → Gate → Review → Jira
```

Harness này là lưới an toàn cho thay đổi workflow sau này. Nó kiểm **luồng workflow** (các bước shell, truyền biến, mạng docker, báo cáo), **không** kiểm chất lượng Selector (LLM là fake), không thay S4-06 (GitHub, Jira, Anthropic thật).

## Quyết định đã chốt (không cần hỏi lại)

- **Kịch bản A theo phương án (b)**: local chỉ chạy `qc-agent gt generate` và `python -m qc_agent.groundtruth.pr_body` trong container rồi assert file GT sinh ra và thân PR. **Không** gọi đây là bằng chứng đã chạy nguyên job tạo PR: job `generate` (checkout, `gh`, push nhánh `qc-agent/gt/<prd-id>`, mở PR) chỉ S4-06 kiểm được.
- Giữ kịch bản **A–E trên noteboard** (mục 2).
- Thêm **fixture SUT thứ hai** `tests/fixtures/sut/node-api-harness/` (mục 3) để kiểm Select → Gate → Report cho một repo khác stack và đi theo **policy mặc định**.
- Harness phải **kiểm image qc-agent được build từ commit đang thử** (mục 4).
- Không sửa `_default.yaml`, không sửa workflow; chỗ nào cần lệch khỏi workflow thật phải nằm trong harness, có tên, có test và được ghi trong docstring.

## Việc cần làm

Thứ tự làm và thứ tự test ở `PLAN.md`; dưới đây là phạm vi.

1. **`tools/run_reusable_locally.py`**
   - `--anthropic-api URL` và `--jira-api URL` (đặt `ANTHROPIC_BASE_URL` / `JIRA_BASE_URL` cho các bước cần).
   - **Điểm lệch có tên**: bước "Select (PR)" của workflow chỉ chuyển `-e ANTHROPIC_API_KEY` vào container, **không** chuyển `ANTHROPIC_BASE_URL`, nên fake không nhận được lời gọi. Thêm cơ chế `step_rewrites` (đổi chuỗi trong `run:` của một bước theo tên): chuỗi cũ phải khớp **đúng một lần**, nếu không harness dừng với lỗi (workflow đổi thì harness không âm thầm chạy sai). Dùng nó để thêm `-e ANTHROPIC_BASE_URL`. Một test tĩnh khẳng định chuỗi cần đổi còn tồn tại trong workflow. Ghi vào docstring rằng đây là điểm duy nhất harness khác workflow thật.
   - `event_name` cấu hình được (cần cho kịch bản E, `workflow_dispatch`).
   - Server giả phải truy cập được từ container: dùng `host.docker.internal` (Docker Desktop), và `QC_TEST_DOCKER_HOST` cho Linux (IP của `docker0`) như `tests/test_reusable_workflow.py`. **Không** dùng `--add-host` vì lệnh `docker run` nằm trong workflow và không được sửa. Fake phải bind `0.0.0.0` (thêm tham số `host` cho `FakeAnthropic`, `FakeJira`).
   - `HOME` của bước Select được trỏ vào thư mục tạm của test: cache Select của workflow nằm ở `$HOME/.cache/qc-agent/select`, nếu không sẽ lẫn cache giữa các lần chạy và với máy người dùng.
   - `--scenario full-chain` chạy tuần tự các kịch bản ở mục 2 trên một workspace dựng từ noteboard và in một bảng kết quả (mỗi kịch bản: bước, kỳ vọng, đạt/không, lý do).
2. **Kịch bản noteboard (A–E)**: workspace là bản sao `tests/fixtures/sut/noteboard` đặt trong `tests/.docker-workspaces/<uuid>/` (repo git thật, có `base`/`head`), policy `configs/projects/` (project `noteboard` đã đăng ký, có `gt-functional`, `ai-eval` và Jira).

   | # | Kịch bản | Kỳ vọng |
   |---|---|---|
   | A | Sinh GT từ PRD mẫu, **phương án (b)** | `gt generate` exit 0 (`FakeAnthropic` phát `tests/fixtures/llm/gt_noteboard_response.json`), file GT đúng cấu trúc, thân PR dựng được và đã làm sạch (`clean_md`). Không assert gì về nhánh hay PR thật |
   | B | Áp bản GT "đã duyệt" (fixture của S1-08) | `gt validate` exit 0; nếu còn draft thì exit 1 |
   | C | PR của dev có lỗi Critical (`QC_BUGS=1`) | Gate exit 1, Check `failure`, 1 review liệt kê finding. **Inline comment: không có** (đã đo: finding của schemathesis/pytest không có path/line nên review chỉ liệt kê chúng là "ngoài diff"); inline Critical được kiểm ở N2. Bảng ghi dòng "CHƯA KIỂM CHỨNG" |
   | D | PR của dev chỉ có lỗi Low (endpoint mới `GET /notes/{note_id}/word-count` chưa có test, finding Low `coverage-debt`) | Exit 0, Check `success` với tiêu đề có số cảnh báo, inline đúng dòng. **Ticket Jira: chưa kiểm chứng được** (đã đo: `integrations/jira.py` chỉ nhận `JIRA_BASE_URL` https hoặc http tới loopback, mà FakeJira từ container chỉ tới được qua `http://host.docker.internal`, nên bước Jira báo `skipped` và không tạo ticket). Bảng ghi dòng "CHƯA KIỂM CHỨNG"; dedup/tạo ticket vẫn do `tests/test_jira_sync.py` kiểm. Cách kiểm trong harness cần quyết định riêng |
   | E | `workflow_dispatch` với `workers=semgrep` | Chỉ suite `sast` chạy; `FakeAnthropic` đếm được 0 lời gọi |

   Chạy lại D → 0 comment mới, 0 ticket mới, và Select cache hit (0 lời gọi LLM). Các kịch bản này **không** phụ thuộc `QC_DATABASE_URL` (bỏ `qc_api_url`); test khẳng định chạy được khi biến này không đặt.
3. **Fixture thứ hai `tests/fixtures/sut/node-api-harness/`**
   - Điểm xuất phát là **layout** của `tests/fixtures/selector-datasets/node-api/sut/` (`src/routes`, `src/middleware`, `src/db/migrations`, `openapi`, `test/`). Không sửa fixture S4-04: nó dùng Express và không chạy được (không có dependency, health, server hợp lệ), nên fixture mới là bản viết lại.
   - Chạy được trong Docker **không cần npm**: `node:22-bookworm-slim` ghim **cùng digest** với `Dockerfile` gốc chạy `.ts` trực tiếp (type stripping), chỉ dùng `node:http`; không build, không tải gói. Đã thăm dò: `node src/server.ts` chạy được trong image đó (v22.23.2); `npm install` trong `docker build` cũng chạy được ở máy phát triển nhưng sẽ gặp lỗi TLS ở mạng công ty (hiệu chỉnh #16), nên không dùng.
   - Có `GET /health`, `GET /openapi.json` (đọc file `openapi.json` đã commit, đúng tên vì `coverage-debt` chỉ nhận `openapi.json`), route người dùng, và env `PORT`. `.qc-agent/suites/` gồm `api-contract`, `sast`, `secrets`, `deps`, `coverage-debt`, đúng những gì **policy mặc định** dùng được; `module-map.yaml` đã duyệt. `deps` là **bắt buộc** dù PR thường không chạy nó: `_default.yaml` đặt `deps` trong `blocking_suites`, và suite blocking vắng trong repo là `PlanError` (chỉ suite advisory vắng mới được bỏ qua, xem `core/project.py: build_plan`). Dùng đúng dạng `init` sinh (`scaffold/tmpl/deps.yaml.tmpl`), kèm `package.json` + `package-lock.json` khai **một dependency prod** `left-pad@1.3.0` (không CVE, không cài, server không import; ghi rõ trong README fixture). Lockfile không dependency làm Trivy trả `Results` rỗng nên `deps` ra **error** và mọi gate của fixture đỏ; dependency `dev` bị Trivy bỏ qua (đã đo ở spike). Test fixture khẳng định lockfile có ≥ 1 package prod.
   - Project là slug **chưa đăng ký** (`node-api-harness`), policy dir tạm chỉ chứa bản chép của `_default.yaml`.
   - Kịch bản (mỗi cái assert **từng bước**: Start SUT, Select, Gate, Report):

     | # | PR | Kỳ vọng |
     |---|---|---|
     | N1 | sửa route `src/routes/users.ts` + `openapi.json` | Start SUT sẵn sàng; Select `ok=true`, `source=llm`, `FakeAnthropic` đúng 1 lời gọi, request chứa đường dẫn file và không chứa bí mật; suites chạy đúng `api-contract`, `sast`, `secrets`; gate exit 0; Check Run `qc-agent / node-api-harness` `success`; chạy lại cùng SHA → `source=cache`, vẫn 1 lời gọi |
     | N2 | thêm `src/routes/debug.ts` có `eval(...)`, fake LLM trả **rỗng** | Floor (`semgrep`, `gitleaks`) vẫn chạy dù LLM chọn rỗng; gate exit 1; Check `failure`; inline comment đúng file và dòng |
     | N3 | fixture gốc **đã có sẵn** `exclude_path: /users/{id}/profile` trong `.qc-agent/suites/api-contract.yaml`. PR chỉ thêm `GET /users/{id}/profile` vào `openapi.json` và handler (`src/routes/profile.ts`, `server.ts`); **không đụng `.qc-agent/**`** | Không FULL SET; suite `deps` **không** chạy; `coverage-debt` ra đúng 1 finding Low `debt:api_contract:GET /users/{id}/profile` (`debt.new=1`); mọi suite chặn xanh; exit 0; Check `success` có cảnh báo; Jira **bỏ qua** (`_default.yaml` không có `jira.project_key`), không ticket. Assert HTTP riêng sau Start SUT: `GET /users/1/profile` = 200 đúng schema, `GET /users/2000/profile` = 404. Đối chứng (workspace phụ có suite gốc **không** có `exclude_path`, cùng PR): `debt.new=0` và schemathesis gọi 3/3 operation |

     Ý nghĩa của N3 (đã đo ở spike và sửa theo review, không phải giả định): kịch bản chỉ kiểm **đường Low-only với policy mặc định**, **không** kiểm schemathesis. `exclude_path` có sẵn mới là lý do phát sinh Low. Cơ chế: `CoverageIndex.covered` coi operation là đã có test khi path có trong `openapi.json` và còn ít nhất một task `api.property` không loại nó; nợ chỉ có khi mọi task loại nó. **Vì sao PR không được sửa suite:** `_default.yaml` đặt `.qc-agent/**` trong `full_set_paths`, nên sửa file suite là FULL SET và chạy cả `deps`, khiến N3 phụ thuộc tuổi DB Trivy. Handler không ảnh hưởng gate (operation khai báo `404` mà SUT không có handler thì gate vẫn xanh, schemathesis chỉ cảnh báo "Missing test data"), nên handler được kiểm bằng assert HTTP riêng để một API không tồn tại không bị coi là "xanh". Phiên bản N3 cũ ("có handler thật, `coverage-debt` Low, `api-contract` xanh vì API phục vụ operation") và bản rev.2 (PR sửa suite) đều **sai**, đừng viết lại. N3 chạy **riêng** qua Select → Gate **chưa được đo**: là điều kiện nghiệm thu, và chạy chung N2+N3 ở spike không chứng minh gì. Còn một điểm chưa đo: schemathesis có chấp nhận `--exclude-path` cho path vắng trong spec (N1/N2/N4); lỗi thì dừng và báo.
     | N4 | đổi `package-lock.json` ở gốc | FULL SET theo rules (khớp glob gốc), 0 lời gọi LLM, mọi suite của fixture chạy, gồm `deps`: suite này chặn theo `trivy.db_age_days <= 14` nên **phụ thuộc tuổi DB CVE trong image** (image cũ hơn 14 ngày làm N4 đỏ vì lý do đúng của nó). preflight `trivy_db_age_days(image)` (`tools/image_check.py`) đọc `/opt/trivy-cache/db/metadata.json`, tính giống adapter (`UpdatedAt`, làm tròn lên) và làm việc theo hai bước: **trước Gate**, kịch bản khai `expect_deps` (N4 `True`, còn lại `False`); `True` mà DB quá 14 ngày thì lỗi cứng, không chạy Gate; `False` chỉ cảnh báo. **Sau Gate**, test đối chiếu `expect_deps` với suite thực sự chạy (task `deps.vuln`); lệch thì test fail có tên |

   - Không kỳ vọng `pytest`/`gt-functional`/Jira ticket/FULL SET cho lockfile lồng ở fixture này: policy mặc định không cho (xem "Phát hiện cần tôn trọng").
4. **Kiểm image build từ commit đang thử** (`tools/image_check.py`, dùng ở mọi test Docker)
   - Lớp 1 (mặc định, chặt): `docker run --rm --entrypoint printenv <image> QC_AGENT_GIT_SHA` phải bằng `git rev-parse HEAD`; `unknown`/rỗng (build không truyền `--build-arg QC_AGENT_GIT_SHA=…`) hoặc khác HEAD là `StaleImage`; cây làm việc có thay đổi ở các đường dẫn đầu vào của image (`src schemas workers configs web rules docker pyproject.toml uv.lock package*.json Dockerfile`) cũng là `StaleImage` vì SHA không chứng minh được bản chưa commit.
   - Lớp 2 (tuỳ chọn `QC_HARNESS_IMAGE_CHECK=content`): so băm nội dung `qc_agent/**/*.py` trong image với `src/qc_agent/**/*.py` trên máy, cho vòng lặp phát triển khi cây chưa commit; ghi rõ giới hạn (chỉ mã Python của gói).
   - Lối thoát **tường minh**: `QC_HARNESS_ALLOW_STALE_IMAGE=1` cho phép chạy, in cảnh báo to và đánh dấu kết quả `IMAGE CHƯA XÁC MINH`; không dùng cho nghiệm thu.
   - **DB Trivy có thể cũ dù image vừa build** (spike: DB 9 ngày vì stage `trivy` lấy từ cache; hết hạn 14 ngày vào 2026-10-12). Thông báo của preflight kèm lệnh build làm mới DB: `docker build --no-cache --build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD) -t qc-agent:harness-<sha7> .`. **Đã đo (2026-10-07):** `--no-cache-filter trivy` chạy lại stage nhưng image cuối vẫn giữ DB cũ (hai lần), còn `--no-cache` cho DB 1 ngày; nên dùng `--no-cache`.
   - Image dựng sẵn vẫn dùng được qua `QC_TEST_DOCKER_IMAGE` khi `docker build` lỗi TLS (cũng bị kiểm); lỗi mạng khi tải Chromium (`ENOTFOUND cdn.playwright.dev`, `UNABLE_TO_GET_ISSUER_CERT_LOCALLY`) từng xảy ra ở lần build thử đầu và hết ở lần thử lại, nên thông báo gợi ý **thử lại một lần** trước khi dùng `QC_TEST_DOCKER_IMAGE`; không có biến này thì `ensure_image()` thử `qc-agent:harness-<sha7>`, rồi build với `--build-arg QC_AGENT_GIT_SHA=$(git rev-parse HEAD)`; build lỗi TLS thì **dừng với hướng dẫn** (không skip im lặng).
   - Test âm: unit (không Docker) với `docker` giả trả SHA cũ, `unknown`, rỗng, và cây bẩn; test Docker dùng image không có biến (vd. `node:22-bookworm-slim`) và image dẫn xuất `ENV QC_AGENT_GIT_SHA=000…`: cả hai phải `StaleImage`.
5. **Tests** (thứ tự và điều kiện skip ở `PLAN.md`)
   - Không Docker: `tests/test_image_check.py`, `tests/test_harness_rewrites.py` (khớp đúng một lần, workflow còn chuỗi cần đổi), `tests/test_node_api_harness_fixture.py` (suite hợp lệ dưới `_default.yaml`, `suite_map` đúng, module-map, lockfile có ≥ 1 package prod, digest node trùng `Dockerfile` gốc), và test `trivy_db_age_days` (DB > 14 ngày → `StaleTrivyDb`, cùng cách làm tròn với adapter).
   - Docker: `tests/test_full_chain_local.py` (A–E trên noteboard, mark `docker`), `tests/test_node_api_harness_local.py` (N1–N4). Skip kèm lý do khi thiếu Docker hoặc `QC_TEST_DOCKER_IMAGE`; đặt `QC_HARNESS_REQUIRE_DOCKER=1` thì thiếu là **lỗi**, để lượt kiểm bắt buộc không xanh vì skip. Test Docker không chạy song song (tên container `sut`/`ui`/`db`, mạng `qc-net` cố định).
6. **Tài liệu**: phần "Chạy harness local" trong `docs/e2e-runbook.md` (file được tạo ở bước này hoặc S4-06, bước nào tới trước thì tạo): cách có image đúng commit, biến môi trường, và **phạm vi kết luận** (bảng cuối prompt).

## Phát hiện cần tôn trọng (S4-04), không sửa trong S4-05

Đây là việc riêng, đổi hành vi mọi project; test **không** được viết kỳ vọng trái policy hiện tại:

- Glob của `_default.yaml` chỉ khớp lockfile/manifest **ở gốc**: `apps/web/pnpm-lock.yaml` không phải FULL SET.
- `**/Dockerfile*` không khớp `deploy/docker/api.Dockerfile`.
- `gt-functional` không nằm trong `_default.yaml` nên repo chưa đăng ký **không bao giờ chọn được `pytest`**; `_default.yaml` cũng không có `jira.project_key` nên Jira bị bỏ qua.
- `_default.yaml` đặt `deps` trong `blocking_suites`: repo chưa đăng ký **phải** có suite `deps`, thiếu thì gate dừng với `PlanError` (exit 3); và suite đó ép DB CVE trong image không quá 14 ngày. Đây là điều kiện onboarding, không phải lỗi của harness.
- **Giới hạn của QC-Agent (R13), harness chỉ né, không giải quyết:** app **không có dependency prod** làm `deps` ra error (Trivy `Results` rỗng) nên gate BLOCKED. Fixture dùng `left-pad@1.3.0` để có dữ liệu quét. Đây phải thành **việc sửa QC-Agent riêng**, xong hoặc có quyết định chính thức trước nghiệm thu SUT cuối (S4-06 trở đi); S4-05 không sửa adapter hay policy, và báo cáo cuối không được coi nó là đã xử lý.

## Kết luận đã chốt sau khi implement (2026-10-07)

- **D** chọn phương án B: không thêm rewrite thứ hai cho bước Jira; ticket Jira ở D là CHƯA KIỂM CHỨNG trong harness (kiểm ở `tests/test_jira_sync.py`).
- **C** chấp nhận giới hạn: không có inline comment cho lỗi `QC_BUGS=1`; inline Critical được kiểm ở N2.
- Ba dòng CHƯA KIỂM CHỨNG của `--scenario full-chain` (A nhánh/PR thật, C inline, D ticket Jira) là trạng thái cuối của S4-05. S4-06 làm sau S4-07 và đóng gói.

## Ngoài phạm vi

Repo, Jira và key thật (S4-06); job `generate` của workflow GT chạy nguyên (checkout, `gh`, push, mở PR); chất lượng chọn worker của model thật; sửa workflow hay `_default.yaml`; đường Select thành công với SHA thật ở noteboard (S4-05b, đã xong trước bước này).

## Phạm vi kết luận

| Việc | Kết luận được rút ra | Không được rút ra |
|---|---|---|
| Noteboard A–E | Workflow chạy đúng cho project **đã đăng ký** (policy riêng có `gt-functional`, `ai-eval`, Jira), stack Python | Gì về repo dùng policy mặc định hay stack khác |
| `node-api-harness` N1–N4 | Select → Gate → Report chạy cho repo **Node, chưa đăng ký, policy mặc định** | `pytest`/GT/Jira (policy mặc định không có); chất lượng Selector; hiệu quả trên SUT sản phẩm |
| Cả hai | Luồng shell và truyền biến của workflow, mạng docker, Check Run/comment/review ở GitHub **giả** | Hành vi thật của GitHub Actions (checkout, cache, quyền), Anthropic/Jira thật, tạo PR thật (S4-06), image GHCR theo digest |
| Kiểm image | Image dùng để chạy được build từ HEAD sạch (hoặc nội dung Python khớp ở chế độ `content`) | Image trên GHCR đúng với commit đã merge |
