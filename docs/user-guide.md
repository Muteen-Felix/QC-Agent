# Hướng dẫn cho team SUT: đo Sprint 4 và demo QC-Agent

Tài liệu này dành cho team cho phép QC-Agent kiểm thử **sản phẩm thật của mình** trên một repo GitHub sandbox. Có hai giai đoạn:

1. **Đo Sprint 4:** chạy luồng PRD → QA duyệt test → kiểm PR → báo cáo → Jira trên GitHub/Jira thật, có LLM API key cho những bước cần LLM.
2. **Demo khi không có LLM API key:** dùng chính SUT và các test đã được QA duyệt ở giai đoạn 1 để chứng minh cổng kiểm chất lượng vẫn chạy và chặn lỗi.

**SUT** là ứng dụng được kiểm thử. **PR** là đề nghị đưa thay đổi vào nhánh chính. **Gate** là lượt kiểm tự động trên PR; chỉ khi GitHub yêu cầu check này xanh thì kết quả của nó mới thực sự chặn merge. Team SUT không cần biết code nội bộ QC-Agent: team cung cấp môi trường chạy, dữ liệu thử và người duyệt; team QC cấu hình QC-Agent và thu bằng chứng.

## 1. Team SUT cần chuẩn bị gì?

Điền các thông tin dưới đây và gửi **tên hoặc đường dẫn, không gửi giá trị secret** cho team QC.

| Cần chốt | Team SUT cung cấp |
|---|---|
| Repo sandbox | `OWNER/REPO`, nhánh chính (ví dụ `main`), người có quyền Admin. Repo phải chứa mã SUT đủ để dựng và chạy từ một PR; không dùng dữ liệu production. |
| Người tham gia | Tài khoản QA có quyền Write để duyệt test; một tài khoản dev không thuộc nhóm QA để thử quy tắc bảo vệ nhánh. |
| Cách chạy API | Vị trí Dockerfile, thư mục build, cổng, đường dẫn health trả **2xx**, biến môi trường cần thiết. Nếu cần database, nêu loại DB, cách khởi tạo dữ liệu thử và cách biết DB đã sẵn sàng. |
| Phạm vi kiểm | Một luồng nghiệp vụ HTTP/JSON nhỏ, ổn định; PRD có từng tiêu chí chấp nhận rõ ràng; file OpenAPI trong repo nếu có. Chỉ định dữ liệu và tài khoản test riêng nếu API cần đăng nhập. |
| Jira sandbox | URL, project key, loại ticket được phép tạo (ví dụ `Task`), tài khoản có quyền tạo/gán ticket, Jira `accountId` của tác giả PR thử. |
| Gửi dữ liệu tới LLM | Xác nhận PRD, OpenAPI và **diff mã nguồn** của sandbox được phép gửi tới nhà cung cấp LLM nào. Nếu chưa được phép, các phép đo LLM thật sẽ chờ; các bước không dùng LLM vẫn làm được. |
| Ngân sách | Trần chi tiêu API cho lần đo; team QC sẽ báo ước tính và đo một lượt nhỏ trước chuỗi chạy lặp. |

**Chọn repo GitHub:** kịch bản QA ngăn merge cần branch protection. GitHub Free hỗ trợ tính năng này cho repo public; nếu sandbox là repo private, hãy kiểm tra gói GitHub có hỗ trợ trước khi bắt đầu. Repo nên nằm **cùng tổ chức/chủ sở hữu** với những người sẽ cấu hình quyền Actions và QA.

**Chọn SUT:** ưu tiên API chạy được bằng Docker từ mã của PR. Nếu dùng một địa chỉ SUT đã chạy sẵn, hãy xác nhận đó là đúng phiên bản của PR; nếu không, kết quả kiểm không chứng minh thay đổi trong PR đã được thử. Team QC sẽ cùng team SUT chọn cách cấp DB và secret cho container thử nghiệm.

## 2. Giai đoạn 1 — đo Sprint 4 trên GitHub và Jira thật

### Bước 1. Kết nối repo SUT với QC-Agent

Team QC cung cấp **commit SHA** của workflow và **image digest** của phiên bản cần đo; hai giá trị này dùng để cố định phiên bản trong suốt lượt đo. Team QC tạo PR cấu hình trên repo SUT. Team SUT kiểm tra rồi duyệt PR đó, đặc biệt là:

- QC-Agent dựng và gọi được API của PR, health path trả 2xx, database và tài khoản test hoạt động.
- Các file `.qc-agent/suites/` mô tả đúng bài kiểm muốn chạy. File chưa hoàn thiện có dấu `qc-agent:todo`; cần xử lý trước khi coi lượt gate là kết quả kiểm sản phẩm.
- `.github/workflows/qc-gate.yml` chạy trên PR; `.github/workflows/qc-groundtruth.yml` dùng để đề xuất test từ PRD. Repo có workflow `qc.yml` cũ thì team QC sẽ kiểm tra để tránh hai gate cùng chạy.
- Chính sách project do team QC giữ trên nhánh `main` của QC-Agent. Để test QA duyệt tham gia chặn merge, chính sách phải có suite `gt-functional` trong `blocking_suites`; chỉ có file test trong repo SUT là chưa đủ.

Team QC chạy `qc-agent init --dry-run` để xem file sẽ được tạo, sau đó `init` và `validate` trên bản sao repo SUT. Team SUT không cần chạy lệnh này nếu đã giao việc tích hợp cho team QC. Phần giải thích từng input và lỗi cấu hình nằm ở [hướng dẫn CI](usage-ci.md).

### Bước 2. Bật quyền GitHub và Jira

Người có quyền Admin của repo SUT cùng team QC kiểm tra:

1. GitHub Actions được phép chạy workflow từ QC-Agent và kéo image. Nếu repo hoặc image QC-Agent là private, cần cấp quyền đọc tương ứng.
2. Team hoặc tài khoản QA được ghi đúng trong `.github/CODEOWNERS`, có quyền Write, và file này đã nằm trên nhánh chính.
3. Workflow được phép tạo PR Ground-Truth. Nên dùng tài khoản bot/GitHub App riêng nếu muốn check trên PR do bot mở chạy ngay.
4. Sau khi check xuất hiện ở PR đầu tiên, bật bảo vệ nhánh chính: yêu cầu PR, QA Code Owner duyệt khi sửa `.qc-agent/**`, và yêu cầu các check `gt validate` cùng `qc-agent / <project>` xanh. Team QC đối chiếu **tên check hiển thị thực tế** trước khi chọn.
5. Jira sandbox cho phép tạo và gán ticket. Team QC đưa `project_key`, `issue_type` và ánh xạ GitHub login → Jira `accountId` vào policy project.

API key LLM, Jira token, mật khẩu test và thông tin DB chỉ đặt trong **GitHub Actions secrets** do người có quyền quản lý. Không ghi giá trị vào PRD, file cấu hình, PR, ảnh chụp hoặc tin nhắn cho team QC. Nếu API có đăng nhập, team QC sẽ kiểm tra kiểu đăng nhập có được suite hỗ trợ trước khi hứa kiểm các endpoint đó.

### Bước 3. Chạy năm kịch bản A–E

Mỗi thay đổi gây lỗi dưới đây chỉ làm trên **nhánh thử trong sandbox**. Team SUT chọn tình huống phù hợp sản phẩm; team QC ghi trước kết quả mong đợi để tránh coi lỗi hạ tầng là lỗi sản phẩm.

| Kịch bản | Team thực hiện | Kết quả cần thấy |
|---|---|---|
| **A — Từ yêu cầu đến test** | BA/team SUT đưa một PRD nhỏ vào nhánh chính hoặc chạy workflow cho PRD đó. | Workflow dùng LLM thật mở PR Ground-Truth; test mới ở trạng thái `draft`, chưa tự được dùng để chặn merge. |
| **B — QA duyệt** | QA xem từng test, sửa/loại test sai, chuyển test đúng sang `approved`, rồi duyệt PR. Một tài khoản không phải QA thử sửa `.qc-agent/**`. | `gt validate` xanh sau khi QA hoàn tất; người không phải QA không thể merge thay đổi test khi thiếu QA duyệt, cũng không push thẳng vào nhánh chính. |
| **C — Lỗi phải chặn** | Dev mở PR chứa một lỗi đã chọn trước, có test hoặc worker xác định được mức Critical/Medium. | Gate trả `BLOCKED`, check đỏ, PR không merge được; báo cáo chỉ ra finding hoặc test thất bại. |
| **D — Chỉ có cảnh báo Low** | Dev mở PR chỉ tạo finding Low đã chọn trước, các kiểm tra chặn khác đều xanh. | Gate trả `PASSED_WITH_WARNINGS`, check xanh kèm cảnh báo; có inline comment khi finding nằm trên dòng thay đổi và Jira tạo/gán ticket trong sandbox. |
| **E — Chạy theo yêu cầu** | Team QC chạy workflow thủ công và chỉ định danh sách worker. | Artifact cho thấy đúng worker được yêu cầu; đây là đường chạy thủ công, không dùng LLM để chọn worker. |

Trong kịch bản A, LLM **đề xuất** test; QA quyết định test nào đúng. Trong C/D, kết quả xanh/đỏ do test và quy tắc xác định, không do LLM phán. PR chỉ đổi tài liệu hoặc file cấu hình có thể khiến bước chọn worker đi theo quy tắc có sẵn; team QC sẽ chọn diff phù hợp khi cần đo LLM Select.

### Bước 4. Thu bằng chứng và chốt số đo

Team QC lưu cho từng kịch bản: link PR và Actions run, SHA của commit, ảnh hoặc kết quả check/QA review, artifact `qc-runs-<project>-<attempt>`, kết quả Jira (nếu có) và thời điểm chạy. `selection.json` cho biết vì sao worker được chọn; `llm_usage.json` cho biết số token/chi phí ước tính. Secret và nội dung nhạy cảm không nằm trong bảng bằng chứng gửi rộng rãi.

Sau lượt đầu, team QC ước tính chi phí rồi mới chạy chuỗi **C + D mười lần**, đối chiếu mục tiêu ít nhất **9/10** lượt đúng. Team QC còn đo mức giảm token của Diff Agent, cache khi chạy lại cùng commit, chi phí và thời gian gate so với baseline. **Một lần chạy xanh không có nghĩa Sprint 4 đã hoàn tất**: chỉ đánh dấu đạt từng mục khi có bằng chứng; mục nào chưa chạy được ghi `PENDING` và lý do. [Tiêu chí Sprint 4](implementation-plan.md#dod-sprint-4) do team QC chịu trách nhiệm tổng hợp.

## 3. Giai đoạn 2 — demo trên cùng SUT khi gate không có LLM key

Giai đoạn này bắt đầu **sau khi** có test case đã được QA duyệt và merge vào nhánh chính. Không chạy bước sinh test mới từ PRD trong buổi demo: bước đó cần LLM API key. Nếu catalog được tạo bằng fake LLM để thử tích hợp, phải nói rõ đó là mô phỏng; nó không chứng minh chất lượng model trên PRD mới.

### Trước buổi demo

1. Team QC xác nhận policy có `gt-functional` và các suite chặn merge cần thiết; các suite được dùng trong demo phải chạy được **không cần LLM**. Một worker bắt buộc cần model mà thiếu key có thể gây lỗi, không phải bằng chứng cho cơ chế fallback của Select.
2. Team QC làm cho **workflow gate không nhận LLM API key**. Caller hiện dùng `secrets: inherit`, tức truyền mọi secret của repo sang gate. Trên sandbox, người quản lý secret tạm gỡ **chỉ các key LLM** khỏi repo (và ngừng cấp key từ tổ chức cho repo đó, nếu có). Giữ nguyên `secrets: inherit` để secret Jira, DB, image và tài khoản test vẫn tới gate. Trước khi gỡ, xác nhận key gốc đã được lưu ở nơi quản lý secret đáng tin cậy hoặc chuẩn bị key thay thế: GitHub không cho xem lại giá trị secret. Sau demo đặt lại key. Team QC sẽ kiểm workflow Ground-Truth không được kích hoạt trong lúc key bị rút.
3. Dùng một **PR đổi mã nguồn chưa từng chạy Select với key**. Kết quả LLM cũ có thể nằm trong cache ngay cả khi key đã bị bỏ. Một diff mã nguồn mới làm khóa cache khác đi. PR chỉ đổi docs có thể chỉ chạy floor; PR sửa `.qc-agent/**` có thể FULL SET do quy tắc, nên không chứng minh được nhánh `missing_api_key`.
4. Trước khi cố ý tạo lỗi, chạy một PR sạch để biết SUT, database và test đã duyệt hoạt động bình thường. Lỗi dựng SUT hoặc thiếu DB không phải là lỗi sản phẩm được gate phát hiện.

### Trình tự trình diễn

1. Dev mở PR mã nguồn trên repo SUT. Trong artifact, `selection.json` phải ghi `source: "fallback"`, `full_set: true`, `fallback_reason: "missing_api_key"`. Nếu thấy `source: "cache"` hoặc `source: "rules"`, chọn diff mới phù hợp rồi chạy lại; chưa đạt bằng chứng fallback thiếu key.
2. Cho thấy các worker thực sự chạy trên phiên bản SUT của PR, gồm test QA duyệt và các suite đã cấu hình. Thiếu key làm Select chọn **FULL SET**, không làm gate bỏ qua kiểm tra.
3. Cho thấy ba kết quả bằng các PR thử đã chuẩn bị: lỗi Critical/Medium → `BLOCKED` và check đỏ; PR sạch → `PASSED` và check xanh; chỉ Low → `PASSED_WITH_WARNINGS`, check xanh kèm cảnh báo. Mở Check Run, comment trên PR và artifact để đối chiếu kết quả. Nếu đã nối Jira sandbox, cho thấy ticket Low là phần bổ sung.
4. Sau demo, team quản lý secret khôi phục quyền truy cập LLM key theo cấu hình Sprint 4 và kiểm lại workflow bằng một lượt chạy nhỏ.

**Điều demo chứng minh:** khi LLM thiếu key hoặc không sẵn sàng, gate vẫn chạy các bài kiểm đã được duyệt và vẫn chặn lỗi theo chính sách. **Điều demo không chứng minh:** LLM sinh test đúng từ PRD mới, Diff Agent chọn worker tối ưu hay các worker đánh giá bằng model chạy được khi không có key.

## 4. Khi kết quả khác dự kiến

| Hiện tượng | Kiểm tra đầu tiên |
|---|---|
| PR đỏ trước khi worker chạy | Log bước dựng SUT, health path, DB và secret môi trường thử. |
| Test đã duyệt không chạy | `gt-functional` đã nằm trong `blocking_suites` chưa; catalog đã merge, có test `approved` chưa. |
| Không thấy `missing_api_key` | Key còn được truyền, cache Select đã có kết quả, hoặc diff đã được quy tắc xử lý trước khi cần LLM. |
| Check xanh dù có lỗi Critical/Medium | Finding có thật nằm trong suite chặn merge chưa; GitHub đã yêu cầu đúng check `qc-agent / <project>` chưa. |
| Không có inline comment | Finding có gắn được vào dòng thuộc diff của PR không; xem Check Run và artifact trước. |
| Không có Jira ticket | Kiểm project key, quyền tạo/gán issue, ánh xạ tác giả PR và log bước Jira; lỗi Jira không đổi verdict của gate. |

Các bước cấu hình sâu và cách đọc artifact: [gate trên CI](usage-ci.md), [QA duyệt Ground-Truth](groundtruth.md), [đo test trên SUT thật](groundtruth-real-sut.md).
