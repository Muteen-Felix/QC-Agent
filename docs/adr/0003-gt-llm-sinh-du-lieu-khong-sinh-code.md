# ADR 0003: Ground-Truth: LLM sinh dữ liệu test case, code test render tất định
- Trạng thái: Accepted
- Ngày: 2026-09-29 (chuyển từ `architecture.md` §2.1b)

## Bối cảnh
Nếu LLM vừa viết vừa chấm test thì gate xanh không còn nghĩa. Test sinh từ OpenAPI chỉ phản ánh
**code**, không phản ánh **yêu cầu**. Cần test sinh từ PRD mà người duyệt được.

## Quyết định
```
PRD → LLM sinh TC dạng JSON (không sinh code) → render tất định → tests_gt/ + suite gt-functional
    → QA đổi draft → approved (+ edge case origin: qa) → gate chạy TC approved (worker pytest)
```
- Cái QA duyệt là **dữ liệu** (`test-cases.yaml`, hoặc `test-cases.xlsx` hai chiều), không phải code do LLM viết.
- Code test được render từ template nên không có chỗ để LLM bịa.
- Nguồn gốc của test không quyết định quyền của nó: TC do LLM sinh, đã duyệt, chạy không cần LLM thì chặn merge như test người viết.
- Vòng ngoài không bao giờ tự merge; mọi thứ nó sinh đi qua PR `qc-agent/gt/<prd-id>` và QA duyệt.
- Bộ chấm coverage tất định (AC, technique, API) đòi 100% trên TC `approved`; giá trị kỳ vọng lấy từ PRD/OpenAPI, không từ code.

## Hệ quả
- Giới hạn: assertion đọc thẳng DB của SUT chỉ làm dạng hộp đen (gọi API rồi đọc lại qua API).
- PR chưa có test thì gate vẫn xanh: gate chỉ đảm bảo không làm hỏng cái đã duyệt, không đảm bảo cái mới được kiểm. Khoảng trống đóng ở đầu nguồn (PRD → `gt regen` → QA duyệt).
- Vận hành, lệnh, vòng đời TC: [../groundtruth.md](../groundtruth.md).
