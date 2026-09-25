# Dùng qc-agent làm gate cho một repo SUT

Chế độ tự động (mỗi PR) chạy bằng workflow tái sử dụng `qc-gate.reusable.yml`. Repo SUT chỉ cần một file gọi nó.

## 1. Điều kiện
1. **Project (slug)**: repo **chưa đăng ký** dùng luôn policy mặc định `configs/projects/_default.yaml` (gate PR đầy đủ: Check Run, comment, artifact). Muốn dashboard, `--report-to` hoặc chạy `manual` từ web thì đăng ký bằng PR vào qc-agent với file `configs/projects/<slug>.yaml` gồm `slug` + `repo` (xem 1b).
2. **Suite** nằm trong repo SUT tại `.qc-agent/suites/<tên>.yaml`. Đường dẫn trong suite tương đối theo gốc repo SUT (worker chạy với `cwd` = gốc repo SUT).
3. **SUT chạy được bằng Dockerfile** (mặc định `./Dockerfile`, cổng `8000`). Ví dụ tham chiếu: `tests/fixtures/sut/noteboard/Dockerfile`.
4. **Image qc-agent**: `ghcr.io/muteen-felix/qc-agent@sha256:<digest>` (digest hiện ở Job Summary của workflow `image`). Bắt buộc ghim theo digest. Nếu package ở chế độ private, repo SUT cần quyền đọc package (Package settings → *Manage Actions access*) hoặc secret `GHCR_PULL_TOKEN`.

## 1b. Policy: gate đọc từ `main` của qc-agent

Gate **không** đọc policy trong image: bước `Fetch policy` của workflow tải `configs/projects/_default.yaml` và `<slug>.yaml` từ nhánh `main` của repo qc-agent
(tên repo ghi cứng ở `env.QC_AGENT_REPO` đầu workflow; fork hoặc đổi tên repo thì sửa đúng chỗ đó), đặt ngoài workspace SUT, mount **chỉ-đọc** vào container và truyền `--projects-dir /policy`.
Tải không được thì **job đỏ**, không có fallback về snapshot. `report.json` và comment PR ghi `policy: <slug|_default> @ main <7 ký tự commit>`.

- Hiệu lực = `deep_merge(_default, <slug>.yaml)`: dict gộp theo key, **list thay thế** (không cộng dồn). Sau merge, `modes.pr.blocking_suites` phải có ≥ 1 phần tử.
- Suite *advisory* (`advisory_suites`) không có trong repo thì bị bỏ qua (validate ghi chú); suite *blocking* không có thì lỗi.
- Project đã đăng ký thì `repo` trong file phải trùng `github.repository` của repo đang chạy (không phân biệt hoa/thường), sai thì exit 3.
- Mô hình tin cậy: **tin team SUT, chỉ chống sơ suất**. PR của SUT vẫn sửa được `qc.yml` để né gate (đổi `project:` sang slug chưa đăng ký, thêm `suites:`, xoá job);
  chỗ chặn thật là branch protection (required check) và review thay đổi `.github/workflows/`, nằm ngoài qc-agent.
- Một merge vào `main` của qc-agent đổi policy của PR **mọi team ngay lập tức**: review `_default.yaml` và `configs/projects/*` như code của gate. Mọi thay đổi schema policy phải tương thích ngược ít nhất một bản image
  (image ghim cũ mà gặp field lạ thì exit 3 ở mọi repo cùng lúc).
- Chạy lại một PR cũ dùng policy `main` *hiện tại*; `policy_ref` + `policy_sha256` trong report cho biết lần chạy đó đã dùng bản nào.

**Khi repo qc-agent hoặc image chuyển private** (hiện cả hai đang public):
1. Settings → Actions → General → *Access* của repo qc-agent: chọn “Accessible from repositories owned by Muteen-Felix” (`uses:` từ repo private cần cài đặt này, token không thay được).
2. Tạo Org Secret `QC_READ_TOKEN` (`contents:read` trên qc-agent + `read:packages`).
3. Trong `qc.yml` của repo SUT thêm `secrets: inherit` (hoặc truyền tường minh `qc_read_token: ${{ secrets.QC_READ_TOKEN }}`). Workflow dùng nó để fetch policy và đăng nhập GHCR.

Ở máy dev, `qc-agent validate` lấy policy theo thứ tự `--projects-dir` > fetch `main` (đọc `QC_READ_TOKEN` nếu có) > snapshot trong image (kèm cảnh báo `using bundled policy snapshot from build <sha>`). Snapshot chỉ để `validate` chạy offline; gate CI không bao giờ dùng nó.
API/Dashboard vẫn đọc `projects_dir` của bản deploy: đổi đăng ký thì phải redeploy, nên dashboard có thể lệch policy gate cho tới lúc đó.

## 2. File gọi (trong repo SUT: `.github/workflows/qc.yml`)

```yaml
name: qc
on:
  pull_request:
    branches: [main]

jobs:
  qc:
    permissions:               # quyền TỐI THIỂU; workflow tái sử dụng không xin thêm
      contents: read
      checks: write            # Check Run "qc-agent / <project>"
      pull-requests: write     # comment dính trên PR
      packages: read           # kéo image từ GHCR
    uses: Muteen-Felix/QC-Agent/.github/workflows/qc-gate.reusable.yml@<COMMIT-SHA>
    with:
      project: noteboard
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      # sut_env: |               # biến môi trường cho container SUT (KHÔNG đặt bí mật)
      #   QC_BUGS=none
      # qc_api_url: https://qc.example.com   # lưu lịch sử tập trung (cần secret QC_API_TOKEN)
    secrets: inherit
```

Ghim `@<COMMIT-SHA>` (không dùng `@main`) để một thay đổi ở qc-agent không tự động đổi gate của bạn.

### Web UI (tuỳ chọn, cho suite UI/Midscene)
SUT có giao diện web dựng riêng khỏi API thì khai thêm; không khai thì workflow chạy như trước.

```yaml
    with:
      project: myapp
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      sut_ui_dockerfile: apps/web-ui/Dockerfile
      sut_ui_context: apps/web-ui
      sut_ui_port: "8080"                       # mặc định 8080
      sut_ui_health_path: "/"                   # mặc định /
      sut_ui_build_args: |                      # KHÔNG đặt bí mật: build-arg nằm trong lớp image
        VITE_API_URL=http://sut:8000
      sut_env: |
        MYAPP_CORS_ORIGINS=http://ui:8080       # cho phép origin của UI gọi API (tên biến do SUT quy định)
```

Workflow dựng container `ui` cùng mạng docker với `sut`, rồi gate nhận `APP_UI_URL=http://ui:<port>` (suite UI dùng `${env.APP_UI_URL}`). Trình duyệt của Midscene chạy trong container gate nên phân giải được cả `sut` và `ui`; UI nhúng địa chỉ API lúc build thì dùng `http://sut:<cổng>`.

## 2b. Sinh sẵn cấu hình (khuyến nghị) và kiểm trước khi đẩy lên CI

Thay vì viết tay suite/`qc.yml`/Dockerfile UI, chạy **một lệnh** ở gốc repo SUT (Pha 1 của onboarding, offline, không cần Python, không cần SUT đang chạy):

```
docker run --rm -v "$PWD:/sut" ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> init
```

Trên Linux, image chạy bằng user không phải root nên thêm `--user "$(id -u):$(id -g)" -e HOME=/tmp` để file mới thuộc về bạn (chạy bằng root thì `init` tự `chown` các file vừa tạo theo chủ của `/sut`). Trên Git Bash (Windows) đặt `MSYS_NO_PATHCONV=1`.

`init` **chỉ đọc** cây thư mục (scanner tất định, không mạng, không chạy code SUT, không LLM) và **chỉ ghi trong repo SUT**: `.github/workflows/qc.yml`, `.qc-agent/suites/*.yaml`, `.qc-agent/perf/smoke.js`, `.qc-agent/midscene/{explore,canary}.yaml` và
`.qc-agent/Dockerfile.ui` (chỉ cho **SPA tĩnh**: Vite → `dist/`, CRA → `build/`, Next.js `output: 'export'` → `out/`; build bằng đúng lockfile, phục vụ bằng nginx cổng 8080). Không sinh config bên qc-agent: repo chưa đăng ký dùng `_default` (xem 1b).
Không ghi đè file đã có (trừ `--force`); `--dry-run` chỉ in kế hoạch. `--slug` mặc định lấy từ `origin` rồi tên thư mục. `qc.yml` được ghim sẵn `uses:` theo commit build của image; **digest** thì image không tự biết nên vẫn là TODO.

Scanner đọc: Dockerfile API (gốc > `*/Dockerfile` > `docker/*Dockerfile*`), cổng (`EXPOSE`/`--port`), route health (`/api/health` > `/health` > `/healthz`), có FastAPI hay không, biến CORS (`*CORS*`), thư mục UI, package manager (theo lockfile), Node (`engines.node`/`.nvmrc`, mặc định 22), biến URL API của UI (`VITE_*`/`REACT_APP_*`/`NEXT_PUBLIC_*`). Bỏ qua `.git`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `tests`... sâu tối đa 4.
Không thấy Dockerfile API thì **lỗi** kèm hướng dẫn `--sut-dockerfile`. Next.js SSR, workspace monorepo (lockfile ở gốc) hoặc UI không có lockfile: không tự sinh `Dockerfile.ui`, dùng `--ui-dockerfile PATH` với Dockerfile của bạn.

Dấu chưa hoàn tất có bốn dạng (`validate` chặn tất cả, mỗi dạng kèm hướng dẫn):
- `# qc-agent:todo …` — việc cho người (ghim digest, viết bước UI thật).
- `# qc-agent:todo VERIFY: chọn X trong [X, Y] vì …` — scanner gặp ≥ 2 ứng viên và chọn theo luật ưu tiên; xác nhận rồi xoá dòng. (Một ứng viên duy nhất thì không có VERIFY, kể cả khi sai: ví dụ `/health` của router có prefix `/api`; Pha 2 đối chiếu OpenAPI sống.)
- `# qc-agent:todo REFINE: …` — chỗ cần SUT sống (`exclude_path` của Schemathesis, endpoint k6), nằm giữa `# qc-agent:begin refine <tên>` và `# qc-agent:end`. Không có `--openapi` thì `api-contract`/`perf-smoke` vẫn được sinh với vùng này; có `--openapi FILE|URL` thì không có REFINE.
- `# qc-agent:todo SUGGESTED …` — bước do LLM gợi ý, phải duyệt.

`--no-api` chỉ sinh phần UI, nhưng project như vậy **không đủ điều kiện mode `pr`** (cần ít nhất một suite chặn merge: Schemathesis hoặc DeepEval deterministic) và chỉ dùng được `manual`.

**Gợi ý flow UI bằng LLM (tuỳ chọn, chỉ cho `ui-explore`, không chặn merge):** thêm `--suggest-ui --ui-url http://127.0.0.1:5173/` (UI phải đang chạy ở máy bạn) (lặp được, tối đa 5 trang).
`init` mở UI đang chạy bằng Chromium, đọc **nhãn hiển thị** (tiêu đề, nút, liên kết, ô nhập; không mã nguồn, không giá trị ô nhập, không query URL), gửi tới endpoint
`MIDSCENE_MODEL_*` (cùng khoá Midscene) và ghi `.qc-agent/midscene/explore.yaml`. Đầu ra bị ép vào lược đồ chặt (chỉ `aiAct/aiTap/aiAssert/aiWaitFor`, ≤3 flow, ≤8 bước),
luôn mang dấu `qc-agent:todo` "GỢI Ý" nên `validate` từ chối cho tới khi người duyệt và xoá dấu. Mỗi lần gửi được ghi vào `.qc-agent/egress.jsonl`; `--dry-run` không bao giờ gọi LLM;
thiếu khoá hay LLM lỗi thì chỉ cảnh báo và giữ khung TODO. Nhãn của trang là dữ liệu không tin cậy (có thể chứa chỉ dẫn nhằm thao túng LLM): đừng bỏ bước duyệt.

Kiểm offline (vài giây, không Docker/mạng/SUT) rồi mới mở PR:

```
qc-agent validate --sut-root <repo SUT> [--project myapp] [--projects-dir <thư mục policy>] [--strict]
```

Dòng đầu luôn in nguồn policy đang dùng (xem 1b). `--project` mặc định suy từ `origin` của `--sut-root` (rồi tên thư mục). Project đã đăng ký mà `repo` khác `origin` chỉ bị cảnh báo (có thể là fork).
`blocking_suites` rỗng là **lỗi**. Dấu chưa hoàn tất có 4 dạng, mỗi dạng kèm hướng dẫn: `qc-agent:todo` (hoàn tất), `todo VERIFY` (xác nhận lựa chọn của scanner), `todo REFINE` (đợi gợi ý có dữ liệu sống), `todo SUGGESTED` (duyệt bước do LLM gợi ý).
Exit `0` = ổn, `3` = có lỗi. Nó bắt: schema suite/project, lane xung đột policy, task không có worker, file tham chiếu thiếu, biến `${env.X}` mà workflow không cấp
(vd. `APP_UI_URL` khi chưa khai `sut_ui_dockerfile`), `qc.yml` chưa ghim SHA/digest hoặc sai tên input, và mọi dấu `qc-agent:todo` còn sót.

## 2c. Refine trên CI (Pha 2)

Bước `Refine (onboarding suggestions)` nằm **sau `Start SUT`, trước `Run qc-agent gate`** trong workflow tái sử dụng. Input `refine: auto|off` (mặc định `auto`); chạy khi event là `pull_request` **và** repo còn marker
(`grep -rlE 'qc-agent:todo (REFINE|SUGGESTED)|qc-agent:begin refine' .qc-agent .github/workflows/qc.yml`). Không còn marker thì bị bỏ qua ngay.

- Chạy image qc-agent (đã ghim) với `-v "$PWD:/work:ro"`: repo SUT chỉ-đọc, kết quả ghi ra `$RUNNER_TEMP/refine` (`refine.patch`, `suggestions.json`). Chỉ viết lại vùng giữa `qc-agent:begin refine <tên>` và `qc-agent:end`; xoá marker = vùng thuộc về bạn.
- OpenAPI lấy từ SUT đang chạy ở `${APP_BASE_URL}<schema_url của api-contract>` (mặc định `/openapi.json`). `--suggest-ui` chỉ khi có UI **và** secret `MIDSCENE_MODEL_*` (PR từ fork không có secret nên bỏ qua) và chỉ khi `explore.yaml` còn là khung TODO.
- `continue-on-error: true`: refine hỏng không làm hỏng gate. Không thêm quyền nào (`pull-requests: write` đã có), không push commit.
- Đăng **một review** với các comment ```suggestion``` cho vùng nằm **trong diff của PR** (GitHub không cho suggestion ngoài diff và không tạo được file mới); phần còn lại chỉ ở artifact `qc-refine-<run>-<attempt>` (`refine.patch`, `git apply`).
  Review mang `<!-- qc-agent:refine sha256=<patch> -->`: cùng hash thì không đăng lại. Fork: token chỉ-đọc nên chỉ còn artifact.
- Cùng OpenAPI thì cùng patch (xếp ổn định). Chạy cục bộ: `qc-agent init --refine --sut-root <repo> --openapi http://127.0.0.1:8000/openapi.json --out refine-out`.

## 3. Secret (khai ở repo SUT)
| Secret | Dùng cho |
|---|---|
| `QC_API_TOKEN` | đẩy kết quả lên service (tạo bằng `qc-agent token create --project <slug> --name ci`) |
| `OPENAI_API_KEY` / `GEMINI_API_KEY`, `QC_JUDGE_*` | judge của DeepEval (chỉ tham khảo, không chặn merge) |
| `MIDSCENE_MODEL_*` | Midscene (discovery, không chặn merge) |
| `ALERT_WEBHOOK_URL`, `ALERT_TELEGRAM_CHAT_ID`, `DASHBOARD_URL` | thông báo webhook |
| `GHCR_PULL_TOKEN` | kéo image private (nếu không dùng quyền của package) |
| `qc_read_token` (tuỳ chọn) | đọc policy từ qc-agent và kéo image khi repo/image chuyển private; hiện không cần truyền |

PR từ **fork** không nhận secret: task cần key sẽ `skipped`; project nên đặt `on_skipped_gate_task: fail` cho mode `pr` để gate không xanh giả.

## 4. Chặn merge
Branch protection của repo SUT → *Require status checks* → chọn job `qc-agent / <project>` (chính job trong workflow). Job **xanh/đỏ theo exit code của gate**: `0` PASS, `1` FAIL, `3` lỗi cấu hình/hệ thống. Check Run, comment và lịch sử chỉ là phần báo cáo: lỗi ở đó không làm đổi kết quả.

## 5. Kết quả ở đâu
- **Comment dính** trên PR (một comment, cập nhật tại chỗ mỗi lần push): bảng task chặn merge, skipped/error, finding tham khảo.
- **Check Run** `qc-agent / <project>`.
- **Lịch sử** trên dashboard (nếu bật `qc_api_url`), link nằm trong comment.
- **Artifact** `qc-runs-<project>-<attempt>` (14 ngày).

## 6. Rủi ro còn lại (biết trước)
- Worker eval của **chính repo SUT** (ví dụ `pytest tests/eval`) chạy mã của PR với các key LLM trong môi trường. Với PR cùng repo, tác giả là người có quyền ghi; hãy dùng key riêng cho CI với **giới hạn ngân sách**.
- Workflow chưa được chạy trên GitHub thật ở thời điểm viết tài liệu này: các bước shell đã được kiểm chứng trên Docker cục bộ bằng `tools/run_reusable_locally.py` và `tests/test_reusable_workflow.py`.
