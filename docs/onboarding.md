# Onboard một repo vào qc-agent (tự phục vụ)

Dành cho team SUT bất kỳ. Nguyên tắc: **một lệnh, một PR, duyệt comment refine, xoá TODO.** Bạn không cần quyền ghi vào repo qc-agent,
và qc-agent không có quyền ghi vào repo của bạn: mọi thứ chạy trong CI của **chính repo bạn**.

## Bạn cần
- Repo có **Dockerfile của API** (SUT chạy được bằng `docker build` + `docker run`).
- Docker trên máy dev. Không cần Python, không cần SUT đang chạy.
- Một **image qc-agent ghim theo digest** (`ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>`, hiện ở Job Summary của workflow `image` bên qc-agent).

## Bước 1 — một lệnh, ở gốc repo của bạn

```
docker run --rm -v "$PWD:/sut" ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> init
```
Linux: thêm `--user "$(id -u):$(id -g)" -e HOME=/tmp` để file mới thuộc về bạn. Git Bash (Windows): đặt `MSYS_NO_PATHCONV=1`.

Lệnh này chỉ **đọc** cây thư mục và **ghi trong repo của bạn**: `.github/workflows/qc.yml`, `.qc-agent/suites/*.yaml`, `.qc-agent/perf/smoke.js`, `.qc-agent/midscene/*.yaml`, và
`.qc-agent/Dockerfile.ui` nếu UI là SPA tĩnh. Nó cho biết đã đoán gì và còn việc gì (`--dry-run` để xem trước, không ghi gì).

Nếu báo *không thấy Dockerfile API*: thêm `--sut-dockerfile PATH`. Nếu UI là Next.js SSR / monorepo workspace / không có lockfile: tự viết Dockerfile cho UI và thêm `--ui-dockerfile PATH`
(khi đó `init` không tự sinh `Dockerfile.ui`).

## Bước 2 — commit và mở PR vào **chính repo của bạn**
Trước khi đẩy có thể kiểm nhanh: `qc-agent validate --sut-root .` (cũng chạy được qua `docker run … validate`). Việc còn lại được đánh dấu bằng `# qc-agent:todo`, bốn dạng:

| Dấu | Nghĩa | Bạn làm gì |
|---|---|---|
| `qc-agent:todo …` | việc của người | ghim digest ở `qc.yml`, viết bước UI thật ở `explore.yaml`, rồi xoá dòng |
| `qc-agent:todo VERIFY: chọn X trong [X, Y] vì …` | scanner gặp ≥ 2 ứng viên và chọn theo luật ưu tiên | xác nhận (hoặc sửa) rồi xoá dòng |
| `qc-agent:todo REFINE` | cần SUT đang chạy | **đợi comment refine trên PR** (bước 3), hoặc chạy lại `init --openapi <file|url>` |
| `qc-agent:todo SUGGESTED …` | bước UI do LLM gợi ý | duyệt từng bước, sửa cho đúng sản phẩm rồi xoá dòng |

PR onboarding **sẽ đỏ lúc đầu** — có chủ ý: `validate` chặn mọi TODO, và gate có thể đỏ khi chưa có `exclude_path` (vd. endpoint upload không nên bị fuzz).

## Bước 3 — CI đề xuất phần cần dữ liệu sống (Pha 2)
Trong cùng workflow gate, sau khi build và chạy SUT của PR, nếu còn marker REFINE/SUGGESTED thì qc-agent chạy `init --refine` trên **bản sao chỉ-đọc** của workspace:
- đọc OpenAPI sống của SUT → điền `exclude_path` (loại upload/multipart/`DELETE`) và endpoint k6 (≤ 3 `GET` không tham số);
- so `sut_health_path` với OpenAPI và đề xuất sửa nếu lệch (vd. `/health` → `/api/health`);
- có secret `MIDSCENE_MODEL_*` và UI đang chạy thì gợi ý flow UI (chỉ khi `explore.yaml` còn là khung TODO).

Kết quả là **một review** trên PR với các comment ```suggestion``` cho những dòng nằm trong diff của PR (bấm *Commit suggestion*), cộng artifact `qc-refine-<run>` chứa `refine.patch` cho phần còn lại
(`git apply refine.patch`). Không có commit tự động, không cần quyền `contents: write`. Review không đăng lại nếu patch không đổi. PR từ **fork** không có secret và token chỉ-đọc nên chỉ còn artifact.
Đánh đổi: bạn chờ một vòng CI (vài phút) để có gợi ý có dữ liệu sống.

## Bước 4 — duyệt, xoá TODO, xanh
Áp gợi ý, xác nhận các dòng `VERIFY`, ghim digest, xoá TODO còn sót → `validate` sạch → gate chạy thật. Bật *Require status checks* cho job `qc-agent / <project>` trong branch protection.
(Đây là chỗ chặn merge thật; chính sách gate không do file `qc.yml` của bạn quyết định, xem mục dưới.)

## Chính sách gate lấy ở đâu
Gate đọc chính sách từ nhánh **`main` của qc-agent** lúc chạy (`configs/projects/_default.yaml` + `<slug>.yaml` nếu có); lấy không được thì job đỏ. Repo chưa đăng ký dùng `_default`:
suite chặn merge là **`api-contract`** (Schemathesis), còn `perf-smoke`/`ui-explore` chỉ tham khảo. Repo **không có API** và không có suite DeepEval deterministic thì chưa đủ điều kiện mode `pr`
(exit 3), chỉ dùng được mode `manual`. Chi tiết: `docs/usage-ci.md` mục 1b.

## Muốn dashboard / `manual` từ web: đăng ký bằng PR 3 dòng vào qc-agent
Không đăng ký thì bạn vẫn có gate PR đầy đủ (Check Run, comment, artifact). Muốn lịch sử trên dashboard, `--report-to` hoặc chạy `manual` từ web thì mở PR vào qc-agent thêm
`configs/projects/<slug>.yaml`:

```yaml
slug: my-app
name: "My App"
repo: "my-org/my-app"     # phải trùng repo chạy gate, sai thì exit 3
```
Phần policy bỏ trống thì kế thừa `_default` (dict gộp theo key, **list thay thế**, `modes.pr.blocking_suites` không được rỗng). Đổi đăng ký thì server dashboard cần redeploy để thấy.
Không có cách tự đăng ký từ repo của bạn: chủ ý, để phòng QC review thay đổi chính sách.

## Giới hạn cần biết
- Scanner là heuristic: lựa chọn không chắc thì có `VERIFY`, nhưng **một ứng viên duy nhất mà sai thì không có VERIFY** (vd. health route nằm dưới router có prefix `/api` được đọc là `/health`).
  Pha 2 bắt được lỗi health nhờ OpenAPI sống; cổng và biến API của UI chỉ lộ ra khi gate chạy.
- `Dockerfile.ui` chỉ cho SPA tĩnh (Vite, CRA, Next `output: 'export'`) và cần lockfile cạnh `package.json`.
- Suggestion của GitHub chỉ gắn được vào dòng trong diff của PR và không tạo được file mới; phần còn lại ở `refine.patch`. Số dòng tính theo bản merge của PR, nếu nhánh đích đã đổi thì có thể lệch.
- Tin cậy: *tin team SUT, chỉ chống sơ suất*. Sửa `qc.yml` để né gate vẫn làm được; chặn thật là branch protection + review thay đổi `.github/workflows/`.
- Midscene tốn tiền và chậm; số đo k6 trên runner dùng chung không đại diện cho môi trường thật.
