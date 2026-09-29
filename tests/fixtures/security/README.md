# Fixture Security (Làn A)

**Các file JSON ở đây là output THẬT** — chạy semgrep/gitleaks/trivy thật bên trong image `qc-agent:verify` (build từ Dockerfile của repo, semgrep 1.178.0,
gitleaks v8.30.1, trivy 0.74.0; DB CVE nướng lúc build, `UpdatedAt` ghi trong `trivy-db-metadata.json`). Nguồn quét là `scan-src/` (dưới đây), commit kèm
theo để tái tạo lại được. Đây chính là bước 1 của khuôn A-2..A-4 ("chạy công cụ thật trong image một lần, lưu output làm fixture").

Nhờ chạy thật, đã bắt được một lỗi thật trong adapter: `semgrep_adapter.py` từng truyền `--error=false`, nhưng `--error` là flag boolean của Semgrep
1.178.0 và không nhận `=false` (`option '--error' is a flag, it cannot take the argument 'false'`) → exit code 2 → mọi task semgrep sẽ luôn `error`.
Đã sửa: bỏ hẳn cờ `--error` (mặc định semgrep đã exit 0 dù có finding).

## Cách tái tạo

```bash
# semgrep — cwd = scan-src/sast hoặc scan-src/sast-empty
semgrep scan --json --output=semgrep-sample.json --metrics=off --disable-version-check --config /opt/qc-rules/semgrep .

# gitleaks — cwd = scan-src/secrets hoặc scan-src/secrets-empty
gitleaks detect --source . --report-format json --report-path gitleaks-sample.json --exit-code 0 --redact --no-banner --no-git

# trivy — cwd = scan-src/deps hoặc scan-src/deps-empty
trivy fs --scanners vuln --skip-db-update --offline-scan --cache-dir /opt/trivy-cache --format json --output trivy-sample.json --exit-code 0 .

# metadata DB (đổi theo lần build image)
cp /opt/trivy-cache/db/metadata.json trivy-db-metadata.json
```

Chạy trong chính image: `docker run --rm -v "$PWD/scan-src/sast:/scan:ro" -v "$PWD/out:/out" -w /scan --entrypoint semgrep qc-agent:verify scan --json --output=/out/semgrep-sample.json ...` (tương tự cho gitleaks/trivy).

## Nội dung từng file

| File | Nguồn (`scan-src/…`) | Kết quả thật |
|---|---|---|
| `semgrep-sample.json` | `sast/` — `apps/api-server/app/api/jobs.py` (`subprocess.run(cmd, shell=True)`), `apps/api-server/app/config.py` (`pickle.load`) | 2 finding: `opt.qc-rules.semgrep.python-subprocess-shell-true` (ERROR, jobs.py:6), `opt.qc-rules.semgrep.python-pickle-load` (WARNING, config.py:7) |
| `semgrep-empty.json` | `sast-empty/` — mã sạch | 0 finding, `paths.scanned` = 1 file |
| `gitleaks-sample.json` | `secrets/` — khoá GCP giả trong `config.py`, token Slack giả trong `deploy.sh` (giá trị GIẢ, không phải secret thật) | 2 finding: `gcp-api-key` (config.py:2), `slack-bot-token` (deploy.sh:2); cả `Secret` và `Match` đều đã `REDACTED` |
| `gitleaks-empty.json` | `secrets-empty/` — mã sạch | `[]` |
| `trivy-sample.json` | `deps/` — `package-lock.json` (lodash 4.17.20, minimist 0.0.8), `requirements.txt` (PyYAML 5.3) | 9 CVE trên 2 target: `package-lock.json` 7 (2 HIGH + 3 MEDIUM từ lodash, 1 CRITICAL + 1 MEDIUM từ minimist), `requirements.txt` 2 (2 CRITICAL từ PyYAML) |
| `trivy-empty.json` | `deps-empty/` — package giả không tồn tại trong DB nào | 1 target (`package-lock.json`) có `Packages`, **không có khoá `Vulnerabilities`** (không phải `[]` — chú ý khi parse) |
| `trivy-db-metadata.json` | `/opt/trivy-cache/db/metadata.json` trong image | `UpdatedAt` dạng RFC3339 với 9 chữ số phần giây (`2026-09-28T13:05:44.359328237Z`) |

## Ghi chú khi đọc test

- **`check_id` của Semgrep có tiền tố đường dẫn mount** (`opt.qc-rules.semgrep.<rule-id>`) vì Semgrep suy tên từ cấu trúc thư mục của `--config`.
  Đây cũng là hình dạng thật trong gate (suite mẫu dùng đúng `rules_dir: /opt/qc-rules/semgrep`). Adapter coi `check_id` là chuỗi mờ (không parse cấu trúc
  của nó) nên không cần sửa code, chỉ cần test không giả định tên rule "gọn".
- **`AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE`** (giá trị mẫu chính thức của AWS trong tài liệu) **không** bị gitleaks phát hiện — bộ rule mặc định có
  allowlist cho giá trị này. Vì vậy fixture dùng token Slack/GCP giả thay vì AWS.
- Trivy DB của bản build này (mốc 2026-09-28) đã có CVE công bố sau cả `4.17.21` (bản "đã vá" của lodash) và sau `5.3.1`/`5.4` của PyYAML — dữ liệu CVE
  luôn tiếp tục phát sinh, không có phiên bản nào "sạch vĩnh viễn". Fixture `deps-empty` dùng một tên package không tồn tại trong bất kỳ DB nào để chắc chắn sạch.
- File JSON này là ảnh chụp **tĩnh**: test không gọi lại semgrep/gitleaks/trivy, chỉ đọc file. DB có cập nhật sau này cũng không làm test hiện tại đổi kết quả;
  tái tạo lại (script ở trên) chỉ cần khi muốn làm mới ảnh chụp.
