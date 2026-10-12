# S4-06: full-stack-fastapi-template

Sandbox public: `MinhDuc-IT/full-stack-fastapi-template`, nhánh `master`, QA `@MinhDuc-IT`.
Image: `ghcr.io/minhduc-it/qc-agent@sha256:95c55978ec52316e648c07c6f11ff780a930db3950d285504473d43965c6c785`.
Workflow ghim QC-Agent `4183af5c3365e493a1d339a1c73e9485856c0f55`. GitHub đã có secrets GHCR/app/DB. LLM chưa được phép; Jira chưa có.

## Trạng thái

| Mục | Đã kiểm chứng local | Chưa kiểm chứng |
|---|---|---|
| Baseline | Vá dependency; Trivy 0 finding; build; 20 test login/items pass; gate default exit 0 | GitHub trên commit chứa bản vá |
| S4-05 | Noteboard 5 test pass qua hai lượt; Node policy default 6 test pass | Image dùng kiểm nội dung Python (`content`), không chứng minh mọi đầu vào build của HEAD sạch; giới hạn Jira/inline Critical trong harness giữ nguyên |
| A | PRD DRAFT, OpenAPI snapshot, guard `allow_llm=false` | Quyền gửi dữ liệu, ngân sách/key, PR GT thật |
| B | CODEOWNERS | Chờ teammate non-QA; chưa bật/kiểm chứng protection |
| C | Patch health 500: BLOCKED, exit 1, Critical >=1 | PR/check/comment/review GitHub thật |
| D | Endpoint thiếu test: PASSED_WITH_WARNINGS, exit 0, 2 Low | Jira giả tạo 2 ticket thay vì 1; Jira sandbox/policy/PR thật |
| E | Chỉ semgrep, PASSED | Dispatch GitHub với workers=semgrep |
| 10 lượt/cache/chi phí | Ledger rỗng; 0 API LLM thật | A–E đủ, sửa số ticket D, phép/ngân sách LLM và 10 lượt đúng kỳ vọng |

Kết quả local không đóng DoD S4-06. Evidence local dùng source `local`, evidence thật dùng `github`.
Log local ở `runs/onboarding-fastapi/` của QC-Agent; report ở `.qc-agent/local/runs/` của SUT.
Patch/intake/ledger ở `.qc-agent/local/s4-06/`; tất cả local/env được Git bỏ qua.

## 1. Baseline

Đã sửa trong SUT:

- `package.json` overrides: brace-expansion 5.0.12, engine.io 6.6.10, fast-uri 3.1.8, source-map-js 1.2.2; Bun tái sinh `bun.lock`.
- `backend/app/api/deps.py`: tuple exception có ngoặc để parser Python 3.11 đọc được, runtime SUT vẫn Python 3.14.
- `.qc-agent/coverage.yaml`: chỉ định tests backend/frontend và suite/k6/UI/integration active; không tính tài liệu là test.
- `docs/prd/qc-sandbox.md`: DRAFT cần QA review; `docs/openapi.json`: snapshot thật.
- `qc-groundtruth.yml`: base_branch master, OpenAPI snapshot, secrets tường minh, checkbox allow_llm mặc định false.

Rà diff, rồi người dùng tự commit/push hoặc xác nhận cho agent. Không commit local/env:

```powershell
git diff
git add package.json bun.lock backend/app/api/deps.py .qc-agent/coverage.yaml .github/workflows/qc-groundtruth.yml docs/prd/qc-sandbox.md docs/openapi.json
git commit -m "Prepare green QC sandbox baseline and E2E inputs"
git push origin master
```

Actions > qc-gate > Run workflow > master, workers để trống. Xanh là exit 0 (`PASSED` hoặc `PASSED_WITH_WARNINGS`), không gate task error/skip.
Coverage discovery có thể còn báo endpoint email chưa test; không ignore chung hoặc giảm ngưỡng.
Lưu run ID/URL, SHA, job.json, report/llm_usage và điền SHA đã commit vào intake.sut.commit.

## 3. E và C trên GitHub, chưa LLM

**E:** Run workflow qc-gate trên master, workers=semgrep. Report chỉ có sast/semgrep, source manual, không diff-select LLM.
Job Verify GHCR access không phải worker QC; đối chiếu worker trong artifact.

**C:** sau baseline được commit và cây SUT sạch, dùng patch đã kiểm local:

```powershell
git switch master
git switch -c qc-sandbox/c-critical-01
git apply --check .qc-agent/local/s4-06/C.patch
git apply .qc-agent/local/s4-06/C.patch
git diff -- backend/app/api/routes/utils.py
git add backend/app/api/routes/utils.py
git commit -m "test(qc): deliberate HTTP 500 regression"
git push -u origin qc-sandbox/c-critical-01
```

Mở PR target master qua UI. Kỳ vọng Check failure/BLOCKED, exit 1, Critical >=1, comment dính và review ghi finding.
Finding API có thể không có vị trí inline; không coi review body là bằng chứng inline.
Không merge C; đóng/xóa PR/branch cần xác nhận riêng.

## 4. A/B: chuẩn bị, LLM vẫn tắt

QA review PRD DRAFT. Generate chỉ chạy khi dispatch bật allow_llm; PR sửa .qc-agent vẫn validate.
Để A đúng yêu cầu BA commit PRD kích hoạt GT: sau phép/ngân sách, thêm trigger push master/docs/prd/** và cho phép generate ở push. Hiện chưa có push trigger.
Chưa bật agent đọc source. Cần provider/model hợp lệ, key trong GitHub Secrets, trần ngân sách và xác nhận gửi PRD/diff/OpenAPI.
GT auth hiện chỉ login JSON, SUT login form-urlencoded: chưa tạo auth.yaml sai giao thức. Cần hỗ trợ form trong phiên sửa sản phẩm riêng, hoặc chốt phạm vi GT công khai trước phép đo.
Default policy không có gt-functional; muốn pytest approved chặn PR cần policy riêng trên QC-Agent@main.
PR bằng GITHUB_TOKEN không tự kích hoạt pull_request; QA push commit approved sẽ kích hoạt, hoặc cần bot token riêng.
Settings > Actions > General phải cho phép Actions tạo PR khi chạy A; thay đổi settings cần xác nhận.

B để PENDING đến khi teammate join. Bảo vệ **master**, yêu cầu PR/review CODEOWNERS và Check `qc-agent / full-stack-fastapi-template`.
Công cụ sẵn có (cần token admin trong env, không ghi vào file):

```powershell
python tools/protect_ground_truth.py --repo MinhDuc-IT/full-stack-fastapi-template --branch master --dry-run
```

Dry-run đọc state và in payload; bỏ --dry-run sẽ thay đổi GitHub, cần xác nhận riêng.
Công cụ giữ required status checks hiện có, không tự thêm QC check; thêm required check qua UI/API sau baseline xanh.
Không dùng tài khoản QA/admin làm bằng chứng non-QA.

## 5. Jira và D

1. Đăng ký Jira Cloud; tạo site sandbox và project/space Kanban `QC Sandbox`, key `QCSB`.
2. Lấy URL https://<site>.atlassian.net, accountId từ profile /jira/people/<accountId>.
3. Tạo API token không scopes tại id.atlassian.com/manage-profile/security/api-tokens. Adapter dùng Basic Auth qua URL site; scoped tokens cần gateway api.atlassian.com/ex/jira/<cloudId> chưa được hỗ trợ.
4. Tạo secrets JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN trong SUT. Không gửi token vào chat.
5. Gửi URL, project key, map MinhDuc-IT -> accountId; thêm ba secrets Jira vào mapping qc-gate khi sẵn sàng (hiện chưa truyền Jira).
6. Điền `.qc-agent/local/s4-06/jira-policy.example.yaml`, đưa policy riêng lên QC-Agent@main qua review/xác nhận. Default không có project_key nên bỏ qua Jira.

Nguồn: [tạo project/space](https://support.atlassian.com/jira-software-cloud/docs/create-edit-and-delete-team-managed-projects/), [account ID](https://support.atlassian.com/cloud-automation/docs/jira-smart-values-users/), [API tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account).

D tạo branch từ master tương tự C, dùng `.qc-agent/local/s4-06/D.patch`: thêm GET /api/v1/utils/qc-sandbox-status/ chưa có test.
Kỳ vọng exit 0, Low >=1, không Critical/Medium, inline đúng dòng, đúng 1 Jira ticket gán tác giả.

**Vấn đề đã tái hiện:** Low endpoint + Low assertion `debt.new` làm sync_low_findings tạo 2 ticket trên MockTransport (`D-jira-mock.json`). D Jira chưa đạt.
Không gọi Jira thật/10 lượt trước khi xử lý riêng. Prompt sửa: "Giữ cảnh báo trong report nhưng tránh ticket trùng nghĩa cho assertion tổng hợp coverage-debt; test 1 ticket/1 endpoint, assignee và rerun dedup bằng fake Jira. Không giảm severity/ngưỡng gate."

## 6. Thu kết quả, timing/cache/chi phí

Mỗi scenario cần meta.json source github, job.json, checks.json, comments.json, reviews.json, review-comments.json, artifact/; D thêm jira.json.
Lệnh gh dưới đây chỉ đọc, cần gh đã cài/đăng nhập; thay ID bằng giá trị thật:

```powershell
gh run view <RUN_ID> --repo MinhDuc-IT/full-stack-fastapi-template --json jobs,conclusion,event,createdAt,updatedAt
gh run download <RUN_ID> --repo MinhDuc-IT/full-stack-fastapi-template --name qc-runs-full-stack-fastapi-template-1 --dir <EVIDENCE_DIR>/artifact
gh api repos/MinhDuc-IT/full-stack-fastapi-template/commits/<HEAD_SHA>/check-runs
gh api repos/MinhDuc-IT/full-stack-fastapi-template/issues/<PR_NUMBER>/comments
gh api repos/MinhDuc-IT/full-stack-fastapi-template/pulls/<PR_NUMBER>/reviews
gh api repos/MinhDuc-IT/full-stack-fastapi-template/pulls/<PR_NUMBER>/comments
```

Lưu JSON UTF-8: PowerShell 5 `>` mặc định UTF-16; dùng Set-Content -Encoding UTF8 hoặc script API.
Thu Jira chỉ đọc. Các lệnh scenarios.prepare/commands/stability script hiện gắn noteboard/toyapp/main; không chạy nguyên xi cho SUT master. Dùng patch và runbook riêng này.

```powershell
python -m tools.e2e collect <EVIDENCE_DIR> --intake <INTAKE_YAML> --scenario C --scenario D --record <STABILITY_JSON> --iteration 1
python -m tools.e2e stability summary <STABILITY_JSON>
python -m tools.e2e timing --project full-stack-fastapi-template --with-select <PR_JOB_JSON> --baseline <FULL_SET_JOB_JSON>
python -m tools.e2e ledger <EVIDENCE_DIR> --cap <APPROVED_USD_CAP>
```

Ledger thật ban đầu `{"iterations": {}}`; không ghi PASS cho lượt chưa chạy. Dùng collector, mọi thiếu bằng chứng là PENDING.
Chỉ chạy 10 C+D sau A–E đủ, baseline SHA cố định, Jira đúng 1 ticket và được đồng ý ước tính chi phí.
C đỏ là đúng kỳ vọng; 9/10 đếm lượt đúng, không đếm job xanh. Hiện chi phí LLM local là 0, không chứng minh cache LLM.
Khi được phép LLM: rerun cùng commit cần source cache/cache_hit true, 0 lời gọi mới, không thêm comment/ticket.
So job gate có Select với FULL SET cùng SUT/SHA; không lấy baseline còn CVE hoặc SHA khác. Overhead <=60 giây.

| Lượt | C | D/Jira | Cache | Nguồn | Kết luận |
|---|---|---|---|---|---|
| 1 | PENDING | PENDING | PENDING | — | chưa chạy |
| 2 | PENDING | PENDING | PENDING | — | chưa chạy |
| 3 | PENDING | PENDING | PENDING | — | chưa chạy |
| 4 | PENDING | PENDING | PENDING | — | chưa chạy |
| 5 | PENDING | PENDING | PENDING | — | chưa chạy |
| 6 | PENDING | PENDING | PENDING | — | chưa chạy |
| 7 | PENDING | PENDING | PENDING | — | chưa chạy |
| 8 | PENDING | PENDING | PENDING | — | chưa chạy |
| 9 | PENDING | PENDING | PENDING | — | chưa chạy |
| 10 | PENDING | PENDING | PENDING | — | chưa chạy |

## Xác nhận thao tác ngoài

Agent chuẩn bị local và đọc kết quả. Push, mở/merge/đóng PR, đổi protection/secret, tạo ticket và gọi LLM thật cần xác nhận từng hành động theo S4-06.
Phiên chuẩn bị này chưa push, chưa tạo PR/ticket hay bật protection/LLM.
