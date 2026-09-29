# ĐẶC TẢ YÊU CẦU KỸ THUẬT (SRS) — HỆ THỐNG QC-AGENT
**Phiên bản:** 2.2-FINAL (Chuẩn hóa lộ trình triển khai theo tuần tự: Vòng Ngoài -> Orchestrator -> Output & Gatekeeper)  
**Mục tiêu:** Xây dựng một luồng hoàn chỉnh tối thiểu (End-to-End Flow) để đưa vào sử dụng thực tế.

---

## 1. TỔNG QUAN VÀ BỐI CẢNH NGHIỆP VỤ (BUSINESS CONTEXT)

### 1.1 Vấn đề thực tế cần giải quyết
1. **Thiếu một bộ mốc chuẩn mực (Ground-Truth):** Nếu không hoàn thiện Vòng ngoài trước, hệ thống sẽ không có tiêu chuẩn để đối chiếu xem code mới của Dev có đúng nghiệp vụ hay không.
2. **Orchestrator phải hoàn thiện lõi trước khi tính đến kết quả:** Orchestrator là trái tim của Vòng trong. Nó cần xử lý mượt mà cả 2 luồng: PR (dùng Diff Analysis Agent để chọn worker) và Manual (chạy thẳng). Nếu lõi điều phối chưa chuẩn, việc xử lý kết quả đầu ra sẽ bị phân mảnh.
3. **Cơ chế Output & Gatekeeper tại chỗ:** Bỏ hẳn khái niệm "Sổ nợ" lưu DB. Kết quả phải trả thẳng về PR: Critical/Medium chặn merge ngay lập tức; Low cho phép merge kèm inline code comment và ticket Jira cho sprint sau.
4. **Định nghĩa "Hoàn chỉnh tối thiểu" (Minimal Viable Flow):** Hệ thống chỉ được coi là hoàn tất khi khép kín toàn bộ một vòng: **BA đưa PRD -> LLM QC-Agent sinh Ground-Truth -> QA chốt -> Dev mở PR -> Orchestrator đọc diff bốc worker -> Chạy -> Trả kết quả chặn/báo cáo về PR.**

---

## 2. MA TRẬN PHÂN RÃ MODULE HỆ THỐNG

Hệ thống được chia thành 4 module độc lập tương ứng với các giai đoạn triển khai:

```
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE 1: VÒNG NGOÀI — GROUND-TRUTH ENGINE                             │
│ • Input: PRD từ BA                                                     │
│ • LLM QC-Agent đọc PRD -> Sinh Test Suites (API, Schema, Contract)     │
│ • QA/QC Review (HITL) -> Bổ sung edge cases -> Đóng băng nhánh default │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Cung cấp Test Suites
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE 2: VÒNG TRONG — ORCHESTRATOR ENGINE                             │
│ • Phân luồng Trigger: PR (kèm Git Diff) vs Manual (chỉ định worker)    │
│ • Diff Analysis Agent: LLM nhẹ đọc diff -> Chọn khâu & bốc worker      │
│ • Task Runner: Điều phối chạy các worker song song/tuần tự             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Trả về raw test results
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE 3: OUTPUT & GATEKEEPER ENGINE                                   │
│ • Phân loại Severity: Critical, Medium, Low                            │
│ • Verdict: Chặn PR (Critical/Medium) vs Cho phép merge (Low/Pass)       │
│ • Feedback: Inline PR Comments + Auto-sync Jira Ticket cho lỗi Low     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MODULE 4: END-TO-END FLOW INTEGRATION                                  │
│ • Kết nối toàn bộ 3 module trên vào GitHub Actions / CI Pipeline        │
│ • Tối ưu hóa chi phí token (Diff Pruning, Caching)                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. THIẾT KẾ CHI TIẾT THEO TỪNG GIAI ĐOẠN

### Giai đoạn 1 (Module 1): Vòng ngoài — Ground-Truth Engine
- **Input:** File PRD từ BA (Markdown, OpenAPI spec, hoặc text).
- **Thực thi:**
  - LLM của QC-Agent đọc PRD, bóc tách User Stories, Acceptance Criteria và Input Constraints.
  - Tự động sinh ra cấu trúc test: API contract tests (vd Schemathesis/Pytest), Database assertions, luồng logic chính.
- **Human-in-the-Loop (QA Review):**
  - QA kiểm tra độ bao phủ, thêm các edge-cases thực tế.
  - Commit bộ test vào thư mục `.qc-agent/ground-truth/` trên nhánh `main`/`develop`.
  - Cấu hình Branch Protection / CODEOWNERS: Chỉ QA mới được sửa thư mục này.

### Giai đoạn 2 (Module 2): Vòng trong — Orchestrator Engine
- **Nhận Trigger Payload:**
  - `trigger_type: "manual"` -> Nhận danh sách worker cụ thể -> Điều phối chạy thẳng, không gọi LLM.
  - `trigger_type: "pr"` -> Lấy `git_diff` -> Gửi vào **Diff Analysis Agent**.
- **Diff Analysis Agent:**
  - LLM nhẹ phân tích diff code vs các module trong Ground-Truth.
  - Trả về JSON: Danh sách worker cần chạy (`["semgrep", "schemathesis"]`).
  - **Fallback an toàn:** Nếu diff chạm file cấu hình cốt lõi (`Dockerfile`, `package.json`) hoặc LLM lỗi -> Chạy bộ test an toàn mặc định.
- **Worker Execution:** Kích hoạt các adapter chạy kiểm thử trong môi trường cô lập.

### Giai đoạn 3 (Module 3): Output & Gatekeeper Engine
- **Tổng hợp kết quả:** Tập hợp toàn bộ findings từ các worker thành một schema thống nhất.
- **Đánh giá mức độ nghiêm trọng (Severity):**
  - `Critical` & `Medium`: Lập tức đánh dấu `status = "BLOCKED"`, làm đỏ PR Check trên GitHub.
  - `Low`: Đánh dấu `status = "PASSED_WITH_WARNINGS"`, cho phép merge PR để kịp tiến độ.
- **Phản hồi tương tác (In-PR Feedback):**
  - Bắn **Inline Review Comment** vào đúng dòng code gây ra lỗi trên GitHub PR.
  - Đối với các lỗi `Low`, gọi Jira API tạo tự động ticket gán cho Dev mở PR để giải quyết ở sprint tiếp theo.

### Giai đoạn 4 (Module 4): Khép kín End-to-End Flow & Tối ưu chi phí
- Kết nối CI/CD (GitHub Action reusable).
- Tối ưu hóa chi phí: Loại bỏ comment/format khỏi diff (`git diff -w`), đặt cache theo hash code.

---

## 4. KẾ HOẠCH TRIỂN KHAI THEO SPRINT (ĐÚNG THỨ TỰ NGHIỆP VỤ)

| Sprint | Trọng tâm công việc | Kết quả bàn giao (Deliverables) | Tiêu chí nghiệm thu (DoD) |
|---|---|---|---|
| **Sprint 1** | **Hoàn thành Vòng Ngoài (Ground-Truth Generator)** | • CLI/Agent đọc PRD từ BA.<br>• Bộ sinh test suite tự động.<br>• Cơ chế cho QA review & chốt test lên nhánh `main`. | Đưa 1 file PRD mẫu vào, Agent sinh ra bộ test chuẩn xác; QA bổ sung edge case và commit khóa lên `main`. |
| **Sprint 2** | **Hoàn thành Lõi Orchestrator (Vòng Trong)** | • Bộ xử lý Payload (Manual vs PR).<br>• Diff Analysis Agent (LLM đọc git diff bốc worker).<br>• Fallback tất định khi LLM lỗi.<br>• Bộ điều phối Task Runner. | Chạy thử nghiệm 2 luồng: `manual` chạy đúng worker chỉ định; `pr` với git diff giả lập bốc đúng worker cần test. |
| **Sprint 3** | **Hoàn thành Output Trả về & Gatekeeper** | • Bộ chuẩn hóa schema kết quả.<br>• Gatekeeper phân loại Severity (Critical/Medium/Low).<br>• GitHub Inline Commenter.<br>• Jira Issue Synchronizer. | PR có lỗi Critical/Medium bị chặn đỏ; PR có lỗi Low được xanh kèm comment chỉ dòng lỗi và sinh ticket Jira. |
| **Sprint 4** | **Khép kín Toàn bộ Flow Tối thiểu & Vận hành** | • GitHub Action `.github/workflows/qc-gate.yml`.<br>• Diff Pruning & Token Caching.<br>• Test E2E từ PRD -> Sinh test -> Mở PR -> Chạy Gate -> Feedback. | Toàn bộ quy trình chạy mượt mà, tự động 100% trên môi trường CI thực tế; chi phí token tối ưu. |
