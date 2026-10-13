# Rà soát S4.6 trên full-stack-fastapi-template — 2026-10-12

Kết luận: pipeline QC hiện tại đã chạy thật từ build SUT/PostgreSQL, gate tới report/comment, Enforce và Jira. Baseline, C và E đạt phạm vi không LLM. D chạy đủ flow nhưng lệch kỳ vọng số ticket. B chưa đủ điều kiện; A sinh GT chưa kiểm chứng. **Chưa đóng S4.6 và không tick DoD.** Không sửa architecture/workflow, không gọi LLM thật, không tự tạo thêm PR/ticket trong lượt rà soát này.

## SUT và phạm vi

- Sandbox public: [MinhDuc-IT/full-stack-fastapi-template](https://github.com/MinhDuc-IT/full-stack-fastapi-template), nhánh gốc `master`; QA `MinhDuc-IT`.
- FastAPI + PostgreSQL có Dockerfile CI, suite và OpenAPI đã chuẩn bị; tận dụng các lượt thật C/D/E, không đổi sang toy app.
- Baseline `97e2034`; C `6c40297`; D `7845304`.
- Reusable workflow ghim `4183af5c3365e493a1d339a1c73e9485856c0f55`; image `ghcr.io/minhduc-it/qc-agent@sha256:95c55978ec52316e648c07c6f11ff780a930db3950d285504473d43965c6c785`.
- C dùng default policy; D dùng policy đăng ký tại `2e9c5bc529fce1891dcfec1f2e94ff623ddf9b65`, Jira `QCSB` và user_map đã cấu hình. Các lượt không dùng cùng policy, không đem so thời gian như một thí nghiệm chuẩn.
- Đây là kiểm chứng QC trên PR/dispatch; chưa kiểm chứng deploy/CD hay mọi workflow CI khác của SUT.

## Preflight/offline

Đã chạy `python -m tools.e2e preflight --intake … --out …`, `intake check` và collector E trong Docker có dependencies của QC-Agent. SUT mount chỉ đọc; không sửa repo. Preflight kiểm checkout D hiện tại; baseline được đối chiếu độc lập qua artifact GitHub của đúng commit baseline.

- Preflight: 11 mục cục bộ, **0 LOCAL_FAIL**. Dockerfile, 6 suite, PRD, OpenAPI, CODEOWNERS, policy Jira và contract có.
- Contract: 3 file, `v2.0.0`, nguyên vẹn.
- Offline tests của tools/collector: **160 passed**; chỉ chứng minh công cụ offline, không chứng minh scenario thật hoặc metric LLM.
- Intake check exit 1: thiếu `sut.test_command`, gói GitHub chưa khai, tài khoản non-QA, quyền/ngân sách LLM. Các mục LLM được giữ PENDING theo scope. Repo đã xác nhận public; không suy ra gói thanh toán của tài khoản.
- Chưa chạy lại lệnh unit test riêng của SUT trong preflight do intake chưa có command phù hợp môi trường DB/Python. Lượt gate thật đã build/chạy SUT và kiểm tra workers; đó không thay thế toàn bộ unit test.
- `gh` không có trong PATH; thu evidence bằng GitHub API chỉ đọc thay cho CLI. Không thêm dependency hay sửa tools.
- Preflight hiện in banner cố định “chưa có evidence GitHub thật”; đó là cảnh báo của lệnh offline, không phủ nhận artifact thật trong bộ audit này. `blocking_suites=None` trong preflight chỉ do file override không khai danh sách: policy thực tế kế thừa default, và report chứng minh 4 blocking task đã chạy.

## Ma trận scenario

| Scenario | Input/setup | Kỳ vọng | Actual | Kết luận và blocker |
|---|---|---|---|---|
| Baseline | `master@97e2034`, workers để trống | Gate exit 0; mọi blocking task pass | GitHub success; `PASSED_WITH_WARNINGS`, exit 0; 0 Critical/Medium, 3 Low; API/SAST/secrets/deps pass | **PASS** baseline QC; xanh có cảnh báo, không phải zero-finding |
| A: PRD → GT | PRD/OpenAPI có; LLM chưa bật | Sinh GT và bot mở PR | Chưa chạy | **PENDING**; LLM ngoài scope, không dùng import làm bằng chứng sinh GT |
| A: downstream từ TC nhập | TC health public soạn tay, `origin: qa`, metadata `manual-yaml-no-llm`; catalog/TC/module-map vẫn draft | Import → validate → QA duyệt → GT được chạy | Local schema/render hợp lệ; export XLSX exit 0; import dry-run exit 0, không thay đổi; validate draft exit 1 đúng kỳ vọng | **PENDING trên GitHub**. Local chỉ PASS các bước format/guard; chưa có PR/QA approve. Policy PR hiện không bật `gt-functional` |
| B | CODEOWNERS có; chưa có tài khoản ngoài QA | Thiếu QA approve không merge; không push thẳng master; QA review/merge được | `master.protected=false`; protection API 404; rules áp dụng `[]`; chỉ thấy collaborator `MinhDuc-IT` | **PENDING scenario; FAIL điều kiện setup**. Chưa thử push/merge non-QA và chưa kiểm QA merge; không tự bật protection |
| C: không LLM | [PR #1](https://github.com/MinhDuc-IT/full-stack-fastapi-template/pull/1): health trả HTTP 500; readiness riêng dùng OpenAPI để lỗi tới worker | Critical → đỏ, BLOCKED, exit 1, report | Check failure; 1 Critical/1 Medium/1 Low; Schemathesis fail, các blocking task khác pass; sticky comment BLOCKED; Enforce fail; 0 LLM call | **PASS** gate/report/enforce. Check đỏ chưa chứng minh branch protection chặn merge |
| D: flow không LLM | [PR #2](https://github.com/MinhDuc-IT/full-stack-fastapi-template/pull/2): endpoint public mới không có test, Jira secrets và policy | Low → gate xanh, inline đúng vị trí, Jira đúng người nhận | Gate success/exit 0, 0 Critical/Medium, 2 Low; inline `utils.py:34`; Jira `ok, created=2`; người dùng xác nhận QCSB-4/QCSB-5 gán đúng tài khoản | **PASS flow**. Assignee/key dựa trên xác nhận của người dùng, chưa đọc độc lập Jira API |
| D: kỳ vọng ticket S4-06 | Một endpoint mới chưa có test | Một ticket cho khoản nợ cụ thể | Một finding `api_endpoint` và một finding tổng hợp `debt.new`, tạo 2 ticket | **FAIL kỳ vọng số ticket**. Ghi nhận lỗi sản phẩm; giữ nguyên logic theo scope |
| E | Dispatch `master@97e2034`, `workers=semgrep` | Chỉ semgrep/sast, không LLM | GitHub success; `PASSED`; task `t-010` pass; selection manual, workers `[semgrep]`, suites `[sast]` | **PASS**, collector E đối chiếu artifact thật cũng pass |

Evidence chính:

- [Baseline run 38228367161](https://github.com/MinhDuc-IT/full-stack-fastapi-template/actions/runs/38228367161).
- [C run 38230089312](https://github.com/MinhDuc-IT/full-stack-fastapi-template/actions/runs/38230089312), [comment BLOCKED](https://github.com/MinhDuc-IT/full-stack-fastapi-template/pull/1#issuecomment-6121542643).
- [D run 38236297164](https://github.com/MinhDuc-IT/full-stack-fastapi-template/actions/runs/38236297164), [inline](https://github.com/MinhDuc-IT/full-stack-fastapi-template/pull/2#discussion_r4246992946), [QCSB-4](https://minhduc-qc-agent.atlassian.net/browse/QCSB-4), [QCSB-5](https://minhduc-qc-agent.atlassian.net/browse/QCSB-5).
- [E run 38229004345](https://github.com/MinhDuc-IT/full-stack-fastapi-template/actions/runs/38229004345).
- [PR policy Jira đã merge #52](https://github.com/Muteen-Felix/QC-Agent/pull/52).

## Metric/DoD chưa thể kết luận

| Metric | Trạng thái | Thiếu gì |
|---|---|---|
| Selector recall ≥90% | PENDING | Dataset nhãn và đánh giá thật; C/D dùng rules + FULL SET |
| Token reduction | PENDING | So sánh lời gọi thật với baseline đo phù hợp |
| Cache token/cost LLM thật | PENDING | C/D/E không có LLM call; source rules/manual không chứng minh provider cache |
| Chất lượng/coverage sinh GT | PENDING | Không chạy PRD → GT; TC health nhập tay không đại diện toàn bộ SUT |
| 10 chuỗi C+D liên tiếp, ≥9/10 | PENDING | Chưa có 10 lượt; C đỏ đúng kỳ vọng phải tính là detection thành công, không phải flaky |
| Chậm thêm ≤1 phút | PENDING | Chưa có cặp with-Select/FULL SET cùng SUT, commit, policy và workload; thời gian các scenario khác nhau không đủ |
| Rerun không thêm comment/ticket | PENDING | Chưa thu bằng chứng rerun cùng commit |
| Điều kiện đầy đủ S4-05 | PENDING nghiệm thu | Có lịch sử smoke local, không coi đó là fresh full DoD cho mọi build input của image ghim |
| Deploy/CD | PENDING | Chỉ chạy QC workflows; chưa test deploy |

Dòng chi phí `~$0.00` và usage 0 trong report là bằng chứng các lượt đã kiểm không gọi LLM, không phải bằng chứng tiết kiệm token hoặc cache hit.

## Bộ evidence và cách verify cùng nhau

Trong workspace, bộ evidence nằm ở `runs/s4-06-review/`; bản ZIP chia sẻ nằm ở `runs/s4-06-review-evidence.zip`. Script thu dữ liệu nằm cùng thư mục, chỉ đọc GitHub; không tạo PR, ticket hay dispatch. Không chứa API key.

1. Mở `results.json`: từng input/expected/actual/status và metric PENDING.
2. Xem `preflight.md`, `intake-check.txt`, `checkout.json`, `E-collector.md`.
3. Trong `baseline/`, `C/`, `D/`, `E/`: `job.json`, `jobs.json`, `checks.json`, raw `job.log` và `artifact/` có report/selection/usage. Baseline/E cùng SHA, API checks là dữ liệu hiện tại của commit; dùng run ID và artifact riêng để tránh gán check của lượt E cho baseline.
4. Trong `B/`: bằng chứng protection/rules/collaborators và HTTP status. Không có file giả về push/merge bị từ chối.
5. Trong `D/jira-user-confirmation.json`: provenance của xác nhận Jira, tách khỏi status API tạo ticket trong artifact.
6. Trong `A-import/.qc-agent/ground-truth/` và `A-import/.qc-agent/suites/gt-functional.yaml`: TC nhập tay, XLSX và test render; PRD ở `A-import/docs/prd/s4-06-import-health.md`. `A-*.log` ghi kết quả offline, draft chưa approve.
7. `manifest.json` có SHA256 các file evidence để đối chiếu khi chia sẻ. Không tick checkbox DoD trong phiên này.

Giới hạn tooling hiện tại: script dựng/mutate/stability trong `tools/e2e/` còn giả định toy app Noteboard/nhánh main. Không dùng chúng để mutate FastAPI/master. Collector A còn yêu cầu một LLM call, collector D gộp rerun với LLM cache; do đó không dùng các dòng đó để PASS phần import/no-LLM. Không sửa tools hoặc kiến trúc trong task này.

## Bước tiếp theo đề xuất

1. Hai anh em review bộ evidence này và thống nhất phạm vi đã PASS/FAIL/PENDING.
2. B: có teammate non-QA; cùng duyệt cấu hình protection/ruleset cho `master`, yêu cầu QC check và CODEOWNERS review. Chỉ thử push/merge sau khi có xác nhận cho từng hành động.
3. A downstream: duyệt TC nhập tay, rồi xác nhận push/mở PR thử nghiệm. Kiểm workflow `gt validate` và QA review; muốn chạy GT như blocking suite thì cần bàn việc bật `gt-functional` trong policy trước. Chưa sửa policy/workflow tự động.
4. D: giữ discrepancy hai ticket trong nghiệm thu; sửa logic ở task riêng nếu quyết định đóng kỳ vọng một ticket. Có thể kiểm rerun/idempotency riêng, không gọi nó là kiểm cache LLM.
5. Sau khi thống nhất trên, chạy chuỗi C+D không LLM để đo ổn định CI; không dùng chuỗi này để đóng các metric LLM.
