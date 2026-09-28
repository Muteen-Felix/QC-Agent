# Fixture Security (Làn A)

**Các file JSON ở đây do tay soạn theo định dạng đầu ra đã biết của công cụ — KHÔNG phải output chạy thật.** Máy phát triển lúc soạn không có
semgrep/gitleaks/trivy (và daemon Docker không chạy), nên bước 1 của khuôn A-2..A-4 ("chạy công cụ thật trong image một lần, lưu output làm fixture")
chưa làm được. Test adapter chứng minh adapter đọc đúng **định dạng mà tài liệu mô tả**; chưa chứng minh đó là định dạng của phiên bản đã ghim.
Việc còn lại: chạy thật trong image (bước 7), thay/so lại từng file, đặc biệt các điểm đánh dấu `[EXTERNAL GAP]` dưới đây.

| File | Mô phỏng | Điểm cần kiểm với công cụ thật |
|---|---|---|
| `semgrep-sample.json` / `semgrep-empty.json` | `semgrep scan --json --output F --metrics=off --error=false --config rules/` | có `paths.scanned` (danh sách) ở output JSON; tên trường `extra.severity` ∈ INFO/WARNING/ERROR; `errors[]` |
| `gitleaks-sample.json` / `gitleaks-empty.json` | `gitleaks detect --report-format json --redact` | `--redact` cho `Secret: "REDACTED"` và `Match` chứa `REDACTED`; báo cáo có được ghi khi sạch không (adapter xử lý cả hai) |
| `trivy-sample.json` / `trivy-empty.json` | `trivy fs --scanners vuln --format json` | `Results[]` có mặt (chỉ Target/Class/Type, không Vulnerabilities) cho lockfile sạch; hoàn toàn vắng khi không có lockfile |
| `trivy-db-metadata.json` | `<cache-dir>/db/metadata.json` của DB đã nướng | tên trường `UpdatedAt` (RFC3339, có thể 9 chữ số phần giây); vị trí `db/metadata.json` |

Secret trong fixture là giả và đã "REDACTED" như output thật với `--redact`; test rò secret dựng biến thể có giá trị giả ngay trong code test.
