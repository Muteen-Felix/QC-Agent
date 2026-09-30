---
id: noteboard
title: Noteboard — sổ ghi chú
---

# PRD: Noteboard — sổ ghi chú

Noteboard là dịch vụ ghi chú nhỏ: tạo, xem, xoá ghi chú và tóm tắt nội dung một ghi chú. Tài liệu này mô tả hành vi **đúng** của phiên bản hiện tại,
làm chuẩn để đối chiếu mọi thay đổi mã nguồn về sau.

## Phạm vi

- Dịch vụ HTTP/JSON, không cần đăng nhập. Mọi đường dẫn tính từ địa chỉ gốc của dịch vụ.
- Ghi chú có `id` (số nguyên do server cấp), `title` (chuỗi) và `body` (chuỗi). Dữ liệu chỉ nằm trong bộ nhớ của tiến trình.
- Lỗi luôn trả JSON có trường `detail`. Đầu vào sai không bao giờ được làm server trả lỗi 5xx.
- Ngoài phạm vi: sửa ghi chú, tìm kiếm, phân trang, xác thực, tóm tắt bằng mô hình ngôn ngữ thật (bộ tóm tắt hiện là luật cố định).

## US-1: Tạo ghi chú

Là người dùng, tôi muốn tạo một ghi chú gồm tiêu đề và nội dung để lưu lại ý tưởng.

### Tiêu chí chấp nhận

- AC-1.1: `POST /notes` với JSON `{"title": "<chuỗi>", "body": "<chuỗi>"}` hợp lệ trả 201. Response là JSON có `id` (số nguyên), `title` và `body` đúng bằng giá trị đã gửi.
- AC-1.2: Server lưu `title` và `body` nguyên văn, kể cả tiếng Việt có dấu, emoji và khoảng trắng đầu/cuối (không cắt khoảng trắng, không đổi chữ hoa/thường). `GET /notes/{id}` ngay sau đó trả lại đúng nguyên văn.
- AC-1.3: `title` là chuỗi rỗng, hoặc thiếu trong request, thì trả 422.
- AC-1.4: `body` là chuỗi rỗng, hoặc thiếu trong request, thì trả 422.
- AC-1.5: `title` dài hơn 200 ký tự thì trả 422. `title` dài đúng 200 ký tự vẫn hợp lệ (201).
- AC-1.6: `body` dài hơn 5000 ký tự thì trả 422. `body` dài đúng 5000 ký tự vẫn hợp lệ (201).
- AC-1.7: `title` hoặc `body` không phải chuỗi (số, boolean, null, mảng, đối tượng) thì trả 422.
- AC-1.8: Trên trang chủ, nhập tiêu đề và nội dung rồi bấm **Thêm** thì ghi chú mới hiện ngay trong danh sách, không cần tải lại trang.

## US-2: Xem ghi chú

Là người dùng, tôi muốn xem danh sách và từng ghi chú để đọc lại nội dung đã lưu.

### Tiêu chí chấp nhận

- AC-2.1: `GET /notes` luôn trả 200 và một mảng JSON. Sau khi đã tạo ít nhất một ghi chú, mảng có ít nhất 1 phần tử.
- AC-2.2: `GET /notes/{id}` với `id` của ghi chú đang tồn tại trả 200. Response có đúng `id`, `title` và `body` của ghi chú đó.
- AC-2.3: `GET /notes/{id}` với `id` là số nhưng không có ghi chú tương ứng (ví dụ `999999`) trả 404. Response là JSON có trường `detail`.
- AC-2.4: `GET /notes/{id}` với `id` không phải số (ví dụ `abc`, `-1`) trả 404, không phải 422 hay 5xx.
- AC-2.5: `GET /notes/{id}` với `id` không phải số và rất dài (40 ký tự chữ) trả 404, không bao giờ trả 5xx.
- AC-2.6: `GET /notes/{id}` với `id` gồm 17 chữ số (ví dụ `11111111111111111`) trả 404, không bao giờ trả 5xx.

## US-3: Xoá ghi chú

Là người dùng, tôi muốn xoá ghi chú không còn cần để danh sách gọn gàng.

### Tiêu chí chấp nhận

- AC-3.1: `DELETE /notes/{id}` với ghi chú đang tồn tại trả 204 và không có body.
- AC-3.2: Sau khi xoá, `GET /notes/{id}` của ghi chú đó trả 404.
- AC-3.3: Xoá lần thứ hai cùng một `id` trả 404.
- AC-3.4: `DELETE /notes/{id}` với `id` chưa từng tồn tại (ví dụ `999999` hoặc `abc`) trả 404.
- AC-3.5: Trên trang chủ, bấm **Xoá** cạnh một ghi chú thì ghi chú đó biến mất khỏi danh sách ngay, không cần tải lại trang.

## US-4: Tóm tắt ghi chú

Là người dùng, tôi muốn xem bản tóm tắt ngắn của một ghi chú dài để nắm ý chính nhanh hơn.

### Tiêu chí chấp nhận

- AC-4.1: `POST /notes/{id}/summarize` (không có body) với ghi chú đang tồn tại trả 200. Response là JSON có `summary` (chuỗi), `model` bằng `stub-rule-v1` và `prompt_hash` gồm đúng 12 ký tự.
- AC-4.2: `summary` là câu đầu tiên của `body`: phần đứng trước dấu chấm đầu tiên, đã bỏ khoảng trắng đầu/cuối, không chứa dấu chấm. Ví dụ `body` là `Hôm nay trời đẹp. Ngày mai mưa.` thì `summary` là `Hôm nay trời đẹp`.
- AC-4.3: Khi `body` không có dấu chấm, `summary` là `body` bỏ đi ký tự cuối cùng. Ví dụ `body` là `abcdef` thì `summary` là `abcde`. Nói chung `summary` luôn ngắn hơn `body` ít nhất 1 ký tự khi `body` dài từ 2 ký tự trở lên.
- AC-4.4: `body` chứa cụm `[[long]]` vẫn theo đúng AC-4.2, `summary` không bao giờ dài hơn `body`. Ví dụ `body` là `Ghi chú [[long]]. Chi tiết.` thì `summary` là `Ghi chú [[long]]`.
- AC-4.5: `POST /notes/{id}/summarize` với `id` không tồn tại trả 404.
- AC-4.6: Tóm tắt không làm đổi ghi chú: sau khi gọi, `GET /notes/{id}` vẫn trả đúng `title` và `body` cũ.
