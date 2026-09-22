# 8. Giới hạn và điều chưa chắc

Mục này là biên của bằng chứng: PoC chứng minh cơ chế contract và policy, không chứng minh hệ thống đã sẵn sàng production hay bao phủ mọi loại QC.

## Những loại lỗi hệ thống không bảo đảm bắt được

- Test đúng theo một requirement sai; oracle không thể sửa nguồn yêu cầu sai.
- Logic nghiệp vụ không được viết thành spec/invariant.
- Hành vi sai đã bị record–replay đóng băng thành “chuẩn”.
- Chất lượng cảm nhận của output AI khi output vẫn đúng schema và invariant cơ bản.
- Provider đổi model phía sau cùng một alias; PoC chưa pin snapshot đầy đủ.
- Vùng không có trong `plan.yaml`, hoặc bị né bởi path selection.
- Bug ngoài vùng activity mà exploration đi qua; discovery bổ sung chứ không thay regression suite.

## Giới hạn của LLM và discovery

- `findings[].rationale` của Midscene có thể sai; nó luôn non-gating.
- G-Eval có position bias và self-preference; baseline hiện là giá trị viết tay, chưa có baseline store hay judge calibration.
- JSON Schema chưa ép `confidence` cho mọi finding `llm_judgment` (G4); luật này hiện được kiểm ở `check_result_against_spec`.
- Discovery có thể thành “nghĩa địa finding” nếu không ai triage. Chính sách production cần giới hạn số finding và tự tắt lane khi backlog không được xử lý.
- Chi phí VLM có thể tăng khi UI đổi dù verdict vẫn pass; budget phải là trần cứng và cost phải xuất hiện cả ở run xanh.

## Giới hạn governance

- DG-1 phát hiện xóa/hạ task nhưng không hiểu ngữ nghĩa việc nới threshold, ví dụ `p95 < 300` thành `< 3000`.
- DG-2 dựa trên trailer `Agent-Patch`: đây là **quy ước** của PoC. Agent quên trailer thì guard không nhận ra nguồn patch; guard chỉ phát hiện, quyền phê duyệt thật vẫn nằm ở PR review.
- DG-3 chặn từng thay đổi golden/baseline nhưng không phát hiện việc “làm mềm” dần qua nhiều PR đã được duyệt.
- DG-4 heal-rate monitor chưa cài vì PoC không có healer thật.
- AST diff không làm; chỉ mở lại khi hệ thống thực sự có máy tự tạo PR sửa test/spec.
- Selection theo path không ngăn tác giả cố tình né path. Cần `floor` và nightly full run.

## Giới hạn cụ thể của PoC

- **SUT giả lập**: toy app dùng summarizer stub; kết quả không đại diện một hệ production.
- Ba lỗi được demo là lỗi **cài sẵn**, dùng để chứng minh gate bắt/không bắt theo thiết kế, không phải lỗi agent tự khám phá trong sản phẩm thật.
- “Lọc” trong tiêu chí tái lập nghĩa là so phần verdict tất định; không tuyên bố output LLM giữa hai run giống nhau.
- Baseline G-Eval viết tay và chưa được hiệu chuẩn trên tập nhãn human.
- Midscene live không đạt 3/3: các lượt STEP 52 gặp 503 high demand và 429 quota. Bản demo phải dùng `recordings/demo-good-run` với nhãn **RECORDED/REPLAY**, không trình bày như live run.
- Số đo wallclock, token, USD và mutation chỉ của toy app; không ngoại suy ra suite thật.
- Evidence chỉ lưu local; chưa có retention, phân quyền hay cơ chế dọn dữ liệu.
- Orchestrator giữ credential của nhiều worker trong `.env`, tạo điểm tập trung rủi ro chưa được harden.
- Exit code YELLOW mặc định là 0; CI chỉ nhìn exit code có thể nhầm skipped gate với xanh nếu không đọc report.
- PoC chứng minh thêm worker `mock2` không sửa `core/`, nhưng chưa chứng minh chi phí tích hợp một surface hoàn toàn mới.

## Câu hỏi còn mở

- Consumer JUnit chuẩn có thực sự không thể gate trên provenance tùy biến hay không?
- Assertion node của Hercules là LLM hay tất định ở phiên bản được cân nhắc?
- Ngưỡng “lặp 3/3 để promote” là lựa chọn thiết kế, chưa phải con số hiệu chuẩn.
- Chi phí và độ ổn định ở quy mô suite thật chưa đo.
- Khi nào chuyển execution sang Testkube và report/triage sang ReportPortal?

## [KHÔNG KỊP SPRINT NÀY]

- Judge calibration bằng tập nhãn human.
- Mobile và device farm/emulator.
- Keploy/eBPF trên WSL2 hoặc Docker/Linux.
- Auto-promote finding thành PR có guard đầy đủ.
- Lưu trữ evidence dài hạn, retention và dọn rác.
- Bảo mật orchestrator và secret isolation.
- DG-4 heal-rate monitor và AST semantic diff.

Nguồn tổng hợp: `docs/architecture.md` §7.3–§9, `docs/decisions.md` (đặc biệt STEP 52), và giới hạn sprint trong `docs/plan-execution.md`.
