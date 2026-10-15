# Cài đặt và vận hành GitHub App/service

Nếu bạn chỉ muốn cài App đã được vận hành sẵn vào repo của mình, dùng [User Guide](user-guide.md). Tài liệu này dành cho người vận hành App/service và phần cấu hình kỹ thuật tham chiếu.

Luồng đã thử trên Windows PowerShell + Docker Desktop. App chỉ thay thao tác `init`; sau đó Actions của SUT pull image QC-Agent và chạy QC. Service không chạy worker hoặc sinh test bằng LLM.

## 1. Chọn vai trò

| Vai trò | Cần làm |
|---|---|
| Người vận hành App/service | Phần 2–6: tạo App, giữ private key, chạy service và HTTPS endpoint |
| Thành viên dùng App đã được vận hành chung | Phần 7–10: install vào SUT, review cấu hình, đặt secrets và kiểm chứng |

Thành viên không cần private key của App và không cần chạy service riêng nếu đã có service chung hoạt động. Với triển khai local, máy chạy service và Docker Desktop phải tiếp tục hoạt động để nhận installation mới. CI của SUT đã onboard không phụ thuộc service đó.

## 2. Chuẩn bị của người vận hành

- Git, Python 3, Docker Desktop ở chế độ Linux containers; PowerShell.
- Checkout phiên bản QC-Agent đã publish có service và generator mới. Bản đã thử: commit `241bfb0c18289df33617a044360d25d9a4bc6713` trên `app-llm` của `Muteen-Felix/QC-Agent`.
- Quyền tạo GitHub App; quyền install App vào SUT hoặc người quản trị chấp nhận yêu cầu cài.
- Image QC-Agent đã publish và quyền kéo image nếu private. App không cấp quyền GHCR tự động.

Ví dụ thư mục làm việc: `C:\VSF\QC-Agent`. Nếu dùng đường dẫn khác, thay đường dẫn trong các lệnh dưới đây.

## 3. Tạo GitHub App

GitHub Settings → Developer settings → GitHub Apps → New GitHub App:

1. Đặt tên duy nhất; Homepage có thể là URL repo QC-Agent. Nếu App đã có thì mở trang cấu hình App đó.
2. Repository permissions: **Contents / Pull requests / Workflows: Read & write**; Metadata đọc mặc định. Không cần Actions/Checks cho onboarding.
3. Không cần user OAuth callback/device flow. Muốn cho account khác cài App thì cấu hình phạm vi cài đặt phù hợp, không chỉ account sở hữu App.
4. Ghi lại **App ID**, không phải Client ID.
5. General → Private keys → Generate a private key. Lưu PEM ngoài Git, ví dụ `C:\VSF\Secrets\qc-agent-app.pem`.
6. Tạm chưa install vào SUT. Chạy service và cấu hình webhook trước, để bắt được sự kiện install đầu tiên.

Private key chỉ thuộc người vận hành. Không chia sẻ PEM/PAT qua PR, chat hoặc commit. Định dạng PEM GitHub tải về dùng trực tiếp được với service Python này.

## 4. Cấu hình và build service

```powershell
Set-Location C:\VSF\QC-Agent
New-Item -ItemType Directory -Force .\runs | Out-Null
Copy-Item .\deploy\github-app\service.env.example .\runs\github-app-service.env
python -c "import secrets; print(secrets.token_hex(32))"
notepad .\runs\github-app-service.env
```

Điền các giá trị sau. Kết quả lệnh `secrets` là webhook secret: lưu vào env và dùng cùng giá trị ở trang App.

```dotenv
QC_APP_ID=<APP_ID>
QC_APP_PRIVATE_KEY_FILE=/run/secrets/github-app-key.pem
QC_WEBHOOK_SECRET=<RANDOM_SECRET>
QC_WORKFLOW_REPOSITORY=Muteen-Felix/QC-Agent
QC_WORKFLOW_REF=241bfb0c18289df33617a044360d25d9a4bc6713
QC_IMAGE=ghcr.io/minhduc-it/qc-agent@sha256:8d87867a641fd38f6e2ea274599d9e5d512a56d4c39a5bccc2c9f30d8aa65c5c
QC_QA_OWNER=@<QA_USER_OR_ORG/TEAM>
GHCR_USERNAME=<PAT_OWNER_IF_IMAGE_PRIVATE>
QC_ONBOARD_DB=/data/onboarding.sqlite
```

`QC_APP_PRIVATE_KEY_FILE` là **đường dẫn trong container**, không phải đường dẫn Windows. QA owner phải hợp lệ và có quyền phù hợp trong SUT. Hiện QA owner và GHCR username là cấu hình chung của service; chưa có UI/config mapping riêng theo repo. Người vận hành phải xác nhận các giá trị này phù hợp với từng SUT, hoặc dùng instance riêng khi cần cấu hình khác.

Image/digest ở trên là bản tham chiếu đã thử, không phải cam kết baseline dependency xanh. Với lượt cài sau, người vận hành chọn image mới và ghim digest; Trivy mặc định giới hạn tuổi DB 14 ngày. Repo chứa reusable workflow cần public hoặc có cơ chế chia sẻ reusable workflow phù hợp; install App không tự cấp quyền sử dụng workflow private khác repo.

Build (nếu registry yêu cầu login, dùng PAT của tài khoản có quyền package):

```powershell
docker login ghcr.io -u <PACKAGE_USER>
docker build -f deploy/github-app/Dockerfile --build-arg QC_RUNTIME=ghcr.io/minhduc-it/qc-agent@sha256:8d87867a641fd38f6e2ea274599d9e5d512a56d4c39a5bccc2c9f30d8aa65c5c -t qc-agent-onboarding:local .
```

Service dùng source generator của checkout hiện tại; image runtime cung cấp dependencies. `QC_WORKFLOW_REF` phải là SHA đã có trên GitHub và hỗ trợ các input được generator sinh, gồm `ghcr_username`. Không cần install App vào repo QC-Agent.

## 5. Chạy service và kiểm tra local

Các lệnh sau dành cho lần chạy đầu, khi chưa có container cùng tên. Đổi đường dẫn PEM cho đúng file của bạn.

```powershell
docker network create qc-agent-onboarding-net
docker volume create qc-agent-onboarding-data
docker run -d `
  --name qc-agent-onboarding `
  --restart unless-stopped `
  --network qc-agent-onboarding-net `
  -p 127.0.0.1:8080:8080 `
  --env-file C:\VSF\QC-Agent\runs\github-app-service.env `
  --mount "type=bind,source=C:\VSF\Secrets\qc-agent-app.pem,target=/run/secrets/github-app-key.pem,readonly" `
  --mount "type=volume,source=qc-agent-onboarding-data,target=/data" `
  qc-agent-onboarding:local
Invoke-RestMethod http://127.0.0.1:8080/healthz
```

Mong đợi `ok: True`. Đây chỉ là health của service; chưa chứng minh App ID/key hay quyền SUT đã đúng. Xem lỗi startup bằng `docker logs --tail 100 qc-agent-onboarding`. Chỉ dùng một replica/process cho SQLite volume này.

## 6. HTTPS tunnel và webhook

Cho thử nghiệm local, dùng Cloudflare Quick Tunnel qua Docker, không cần cài binary Windows:

```powershell
docker run -d --name qc-agent-onboarding-tunnel `
  --network qc-agent-onboarding-net `
  cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://qc-agent-onboarding:8080
docker logs qc-agent-onboarding-tunnel
```

Tìm URL `https://<random>.trycloudflare.com`. Mở `<URL>/healthz`, phải thấy `{"ok":true}`. Quay lại GitHub App → General:

- Webhook URL: `<URL>/webhook`.
- Webhook secret: trùng `QC_WEBHOOK_SECRET` trong env local.
- **Enable SSL verification**, bật Active, Save.

Quick Tunnel là môi trường test: hostname có thể đổi khi tạo lại tunnel, không có uptime guarantee. Không dùng URL tạm của một thành viên làm endpoint dùng chung lâu dài. Production cần HTTPS hostname ổn định, lưu queue bền, quản lý secrets và theo dõi failed jobs. Xem [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## 7. Install App vào SUT — phần của thành viên

1. Người vận hành cung cấp link install của App đang hoạt động. Thành viên mở link, chọn account/org và **Only select repositories**, chọn đúng SUT sandbox.
2. Chấp nhận các quyền đã nêu. Không cần cài vào QC-Agent.
3. Service tạo draft PR `chore(qc): onboard QC-Agent`, nhánh `qc-agent/onboarding`.
4. Nếu App đã install trước khi webhook sẵn sàng, người quản lý App vào Advanced → Recent Deliveries → redeliver sự kiện `installation`; hoặc thêm repo mới vào installation để nhận `installation_repositories`.

Nên thử với repo sạch chưa có cấu hình QC. Existing files được giữ; PR mở sẵn không bị ghi đè QA. Nhánh onboarding tồn tại nhưng không có PR mở cần người vận hành kiểm tra, service không tự reset nhánh. Không xoá cấu hình SUT đang dùng chỉ để thử lại.

## 8. Review PR và thêm secrets

**PR onboarding có thể chạy QC ngay**, chưa cần merge, vì App token tạo PR kích hoạt `pull_request` và PR chứa workflow mới. Thiếu secrets thì lượt đầu có thể đỏ; sau khi thêm secrets chạy lại workflow.

Trong SUT → Settings → Secrets and variables → Actions → New repository secret:

| Name | Khi cần | Nội dung |
|---|---|---|
| `GHCR_PULL_TOKEN` | Image private và GITHUB_TOKEN chưa có quyền package | PAT classic có `read:packages`, của tài khoản thực sự được quyền đọc package |
| `SUT_SECRET_ENV` | App yêu cầu secrets | Nhiều dòng `KEY=VALUE`, chỉ thông tin test |
| `SUT_DB_SECRET_ENV` | DB test yêu cầu password | Nhiều dòng `KEY=VALUE` cho DB |

PAT tạo tại GitHub Settings → Developer settings → Personal access tokens → Tokens (classic). Scope `read:packages` **không tự cấp** quyền đọc package của người khác: cần package owner cấp quyền, cấp quyền Actions cho repo phù hợp, hoặc dùng image public. Không chia sẻ PAT của người vận hành cho mọi thành viên. `GHCR_USERNAME` trong workflow phải khớp tài khoản PAT đang dùng. Xem [GitHub Container Registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Đọc mọi `qc-agent:todo`, xác nhận Dockerfile/context, port, health/OpenAPI URL, DB và QA owner. Generator không tự tạo secrets, không bảo đảm DB/migration hay test nghiệp vụ đã sẵn sàng.

Ví dụ FastAPI cần PostgreSQL: `SUT_SECRET_ENV` chứa `SECRET_KEY`, `DATABASE_URL`, `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`; `SUT_DB_SECRET_ENV` chứa `POSTGRES_PASSWORD`. Password trong URL và DB phải khớp. Dùng hostname `db` nếu workflow tạo container DB tên đó. Không copy password ví dụ sang môi trường sản phẩm.

Có thể sinh giá trị mới bằng `python -c "import secrets; print(secrets.token_hex(32))"`, lưu local ngoài Git rồi copy vào UI secrets. Không đặt secrets thật vào `sut_env`, Dockerfile hoặc PR.

## 9. FastAPI reference đã thử và giới hạn của generator

Ví dụ thật: [PR onboarding #4](https://github.com/MinhDuc-IT/full-stack-fastapi-template-3/pull/4).

- Generator mới nhận diện `/api/v1/utils/health-check/` và `/api/v1/openapi.json` bằng AST cho router/settings chuỗi tĩnh; không import/chạy SUT.
- Workflow truyền secrets rõ tên thay vì `secrets: inherit`; với repo dùng crate-ci/typos và chưa có policy spelling riêng, sinh dictionary hẹp `.typos.toml` cho comment tiếng Việt.
- **DB, migration và seed admin trong PR #4 được bổ sung riêng cho SUT này**, không được generator tự suy ra đầy đủ. `.qc-agent/Dockerfile.sut` chạy `scripts/prestart.sh` trước server; workflow khai DB image ghim digest, readiness và `PROJECT_NAME`.
- Không lấy cấu hình này áp nguyên văn cho project khác. Kiểm tra scripts startup và biến bắt buộc của SUT đó.
- Codeowner không tự chặn merge: cần cấu hình branch protection/ruleset. PR draft cũng chưa merge được, dù CI xanh.

## 10. Nghiệm thu và test PR tiếp theo

Sau khi bổ sung cấu hình/secrets, rerun workflow QC của PR. Kiểm tra lần lượt:

1. Pull image pass.
2. DB/SUT startup và readiness pass.
3. Select/gate chạy được, có artifact và comment/review.
4. Phân biệt infrastructure error với finding: Trivy báo CVE không phải lỗi khởi tạo; worker skipped không phải đã chạy pass.
5. Chỉ merge onboarding sau review và quyết định xử lý baseline. Với dependency lỗi có sẵn, xử lý hoặc chốt policy riêng; không bỏ kiểm tra để tạo xanh giả.
6. Tạo PR sandbox riêng để kiểm tra detector; không merge fixture lỗi cố ý.

Evidence đã có trên `full-stack-fastapi-template-3`: install → PR #4 thành công; sau sửa cấu hình và thêm secrets, API/Semgrep/Gitleaks pass, Trivy chặn CVE sẵn có. [PR sandbox #5](https://github.com/MinhDuc-IT/full-stack-fastapi-template-3/pull/5) xác nhận API 500, eval, token giả và dependency bị phát hiện; gate BLOCKED, review/inline và artifact được tạo, 0 LLM calls. Điều này **không** chứng minh baseline xanh hay luồng sinh test bằng OpenAI đã hoạt động.

## 11. Chẩn đoán và vận hành local

| Hiện tượng | Kiểm tra |
|---|---|
| Không có PR | URL tunnel còn sống, Active, delivery HTTP status, App permissions, installation có đúng repo |
| Webhook HTTP 401 | Secret trên App và service phải giống nhau |
| `/healthz` được nhưng không tạo PR | Health không xác nhận GitHub auth; xem log/queue, App ID khác Client ID, PEM đúng App, quyền Workflows |
| `manifest unknown`/401 khi pull | Kiểm tra digest còn tồn tại, token/package ACL, đúng PAT owner; log này chưa đủ kết luận image bị xoá |
| DB/SUT không lên | Secrets test đầy đủ, password URL/DB khớp, startup/migration/port/health đúng |
| Zizmor `secrets-inherit` | Đang dùng workflow cũ; cập nhật generator/reusable ref và truyền secrets rõ tên |
| Trivy fail nhưng startup/API pass | Đọc CVE và fixed version; đây có thể là debt sẵn có trên main |
| GT workflow xanh | Kiểm tra có catalog thật không: khi chưa có GT, validate có thể chỉ skip nội dung; chưa chứng minh sinh TC |

```powershell
docker logs --tail 100 qc-agent-onboarding
docker logs --tail 30 qc-agent-onboarding-tunnel
docker exec qc-agent-onboarding python -c "import sqlite3; db=sqlite3.connect('/data/onboarding.sqlite'); print(db.execute('SELECT repo,status,attempts FROM jobs').fetchall())"
```

Queue retry tối đa 10 lần. Khi `failed`, người vận hành sửa nguyên nhân và chủ động kiểm tra/requeue; redelivery cùng delivery/repo không tự reset record đã có. Không sửa SQL queue khi còn job đang chạy. Chưa có UI quản lý/re-onboard. Trước nâng cấp: giữ volume `/data`, chờ job đang chạy hoàn tất, rebuild image rồi recreate service với cùng volume/env/key mount. `docker restart` không cập nhật env hoặc code image đã đổi.

Service/tunnel dừng thì install mới không tự onboarding; Actions của SUT đã cấu hình vẫn chạy. Để dừng thử nghiệm mà giữ queue: `docker stop qc-agent-onboarding-tunnel qc-agent-onboarding`. Không xoá volume nếu muốn giữ trạng thái chống lặp.

## 12. Sinh test bằng LLM là luồng khác

App onboarding hiện không gọi LLM. GT chạy qua `qc-groundtruth` theo PRD/push hoặc dispatch; QA phải duyệt test case. OpenAI cho GT/Selector và hai key riêng của SUT chưa được triển khai trong mốc này. Xem [groundtruth.md](groundtruth.md), đồng thời phân biệt evidence Claude trước đây ở [eval/vahan/EVIDENCE.md](../eval/vahan/EVIDENCE.md) với SUT FastAPI hiện tại.
