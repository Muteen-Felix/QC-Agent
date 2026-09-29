# rules/semgrep — rule SAST được ghim trong image

Thư mục này được `COPY` vào image tại `/opt/qc-rules/semgrep` và là `inputs.rules_dir` của suite `sast` (worker `semgrep`). Semgrep chạy **offline** với đúng các file này:
không `--config=auto`, không tải rule từ registry, nên PR của người khác không bỗng đỏ vì có rule mới (gate mất tái lập là gate mất quyền chặn).

- Rule **do qc-agent tự viết** (bộ khởi điểm, thận trọng: chỉ bắt mẫu nguy hiểm rõ ràng để ít dương tính giả). Không chép từ registry/repo ngoài nên không kéo theo điều khoản giấy phép của bộ rule đó.
- Mức `ERROR` → metric `semgrep.high` (chặn merge), `WARNING` → `semgrep.medium`, `INFO` → `semgrep.low`. Ngưỡng nằm trong file suite, không nằm ở đây.
- Thêm/sửa rule = PR vào qc-agent (diff duyệt được), rồi build lại image. Rule hỏng ⇒ Semgrep báo `errors[]` ⇒ task `error` ⇒ gate đỏ, không bao giờ xanh.
- Đã kiểm bằng test cấu trúc (`tests/test_security_image_static.py`); **chưa được chạy bằng Semgrep thật** (máy dựng bộ này không có Semgrep). Chạy `semgrep --validate --config rules/semgrep` và thử trên vahan-rpa trước khi bật chặn.
- Bỏ qua một finding có lý do ở phía SUT: `# nosemgrep: <rule-id>` ngay dòng đó (hiện trong diff PR).
