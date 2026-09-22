# 06 — Tự động hóa đến đâu, con người giữ quyền ở đâu

## Kết luận

Vòng đời QC có **7/10 bước tự động hoàn toàn**. **3 bước còn human** nằm ở nơi quyết định thay đổi chuẩn mực hoặc tạo hành động khó đảo ngược. Đây là ranh giới an toàn, không phải phần việc bị bỏ sót.

| # | Bước vòng đời | Trạng thái | Cơ chế trong PoC |
|---|---|---|---|
| 1 | Chuẩn bị test data | Tự động | Golden case được worker đọc; test run tạo và thu evidence. |
| 2 | Thực thi worker | Tự động | Registry probe và route theo capability/lane/oracle; runner spawn adapter. |
| 3 | Phát hiện failure | Tự động | Oracle, schema validation, timeout và retry tối đa một lần sau `error`. |
| 4 | Chọn regression coverage | Tự động trong plan đã duyệt | `plan.yaml` và luật selection quyết định tập task; runtime không dùng LLM. |
| 5 | Tổng hợp verdict | Tự động | AND chỉ trên `gating=true`, mà schema chỉ cho phép `deterministic_assert` có `gating=true`. |
| 6 | Báo cáo và evidence | Tự động | Report tách ba nguồn verdict; evidence có hash và replay command. |
| 7 | Soạn nội dung bug report | Tự động | Hệ có thể tạo finding và `tickets_draft[]`; chưa tự gửi ra tracker. |
| 8 | Duyệt thay đổi chuẩn mực/coverage | Human | Người duyệt diff plan; sửa golden set hoặc baseline luôn qua PR review. |
| 9 | Quyết định action bên ngoài | Human | Promote finding thành gate test, nộp ticket hoặc đóng ticket cần người chịu trách nhiệm. |
| 10 | Chấp nhận rủi ro | Human | Cấp credential, đặt ngân sách và kết luận bất đồng LLM–assert là quyết định có blast radius. |

## Ba bước còn human

### 1. Duyệt chuẩn mực và coverage

Xóa hoặc thu hẹp task trong `plan.yaml` có thể làm PR xanh vì worker đúng không được gọi. Sửa golden set/baseline có thể biến test đỏ thành xanh mà không sửa sản phẩm. Hai thao tác này thay đổi chuẩn mực nên không được tự động hóa.

### 2. Chấp thuận action bên ngoài

Finding có thể bị trùng, thiếu bằng chứng hoặc không tái lập. Ticket bị gửi nhầm và PR test bị merge nhầm đều không đảo ngược rẻ. Hệ chỉ được soạn, dedup và mở PR; người quyết định bước nộp hoặc merge.

### 3. Chấp nhận rủi ro và diễn giải mâu thuẫn

Credential và budget quyết định dữ liệu nào rời máy và chi phí có thể phát sinh. Khi LLM judgment bất đồng deterministic assertion, hệ chỉ đếm và mở review; con người mới được kết luận LLM đúng, LLM sai hay vấn đề mơ hồ.

## Bảy điểm human cụ thể

| Điểm chạm | Rủi ro nếu bỏ human | Mức tự động cao nhất còn an toàn |
|---|---|---|
| Duyệt diff `plan.yaml` | Coverage bị thu hẹp âm thầm | Tự merge diff chỉ thêm; xoá/thu hẹp phải review. |
| Promote candidate thành gate | Test nhiễu làm gate đỏ vô cớ | Chỉ tự mở PR khi finding deterministic, tái lập ≥3/3 và có suggested assertion. |
| Nộp ticket | Spam hoặc đóng nhầm issue | Soạn và dedup tự động; người bấm nộp. |
| Sửa golden/baseline | “Làm mềm chuẩn” để test pass | Không tự động; bắt buộc PR review. |
| Quarantine flaky test | Tắt tiếng bug thật | Có hạn 14 ngày, tự mở lại và tạo task điều tra. |
| Cấp credential và budget | Blast radius không kiểm soát | Cấu hình một lần, review định kỳ. |
| Kết luận bất đồng LLM | Mất tín hiệu thật hoặc đưa nhiễu vào gate | Tự gom/đếm và mở review khi lặp ≥3 lần; kết luận vẫn do người. |

## Escalation tự động tới người

Hệ không nhờ người cho mọi lỗi. Nó chỉ escalation khi một trong bốn tín hiệu xuất hiện: cùng worker có `error` ở ít nhất hai run liên tiếp; LLM judgment mâu thuẫn deterministic assertion ở ít nhất ba run; discovery finding có deterministic signal và tái lập được; hoặc tổng chi phí vượt trần run.

Nguyên tắc vận hành là: tự động phần có thể kiểm chứng và đảo ngược, giữ người ở phần thay đổi chuẩn mực hoặc phát sinh hành động bên ngoài.
