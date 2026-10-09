# node-api-harness

SUT mẫu thứ hai của harness local (S4-05, `tools/run_reusable_locally.py`): API Node nhỏ, project **chưa đăng ký** nên đi theo `configs/projects/_default.yaml`.
Layout lấy từ `tests/fixtures/selector-datasets/node-api/sut` nhưng viết lại để chạy được: `node:http`, TypeScript chạy trực tiếp bằng Node 22, không Express, không build.

- `GET /health`, `GET /openapi.json` (đọc `openapi.json` đã commit), `GET /users/{id}`.
- **`left-pad@1.3.0` chỉ có trong `package.json` / `package-lock.json`**, để Trivy có thứ để quét: lockfile không có dependency prod làm suite `deps` ra *error*
  (Trivy trả `Results` rỗng) và dependency `dev` bị Trivy bỏ qua. Image không cài nó, server không import nó. Đây là cách né giới hạn của QC-Agent, không phải
  cách một app thật nên làm (xem PLAN S4-05, rủi ro R13).
- `.qc-agent/suites/api-contract.yaml` có sẵn `exclude_path: /users/{id}/profile` (chỗ giữ cho operation chưa tồn tại): kịch bản N3 thêm operation đó mà không đụng `.qc-agent/**`.
