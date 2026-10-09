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
- Mô hình tin cậy: **tin team SUT, chỉ chống sơ suất**. PR của SUT vẫn sửa được `qc-gate.yml` để né gate (đổi `project:` sang slug chưa đăng ký, thêm `suites:`, xoá job);
  chỗ chặn thật là branch protection (required check) và review thay đổi `.github/workflows/`, nằm ngoài qc-agent.
- Một merge vào `main` của qc-agent đổi policy của PR **mọi team ngay lập tức**: review `_default.yaml` và `configs/projects/*` như code của gate. Mọi thay đổi schema policy phải tương thích ngược ít nhất một bản image
  (image ghim cũ mà gặp field lạ thì exit 3 ở mọi repo cùng lúc).
- Chạy lại một PR cũ dùng policy `main` *hiện tại*; `policy_ref` + `policy_sha256` trong report cho biết lần chạy đó đã dùng bản nào.

**Khi repo qc-agent hoặc image chuyển private** (hiện cả hai đang public):
1. Settings → Actions → General → *Access* của repo qc-agent: chọn “Accessible from repositories owned by Muteen-Felix” (`uses:` từ repo private cần cài đặt này, token không thay được).
2. Tạo Org Secret `QC_READ_TOKEN` (`contents:read` trên qc-agent + `read:packages`).
3. Trong `qc-gate.yml` của repo SUT thêm `secrets: inherit` (hoặc truyền tường minh `qc_read_token: ${{ secrets.QC_READ_TOKEN }}`). Workflow dùng nó để fetch policy và đăng nhập GHCR.

Ở máy dev, `qc-agent validate` lấy policy theo thứ tự `--projects-dir` > fetch `main` (đọc `QC_READ_TOKEN` nếu có) > snapshot trong image (kèm cảnh báo `using bundled policy snapshot from build <sha>`). Snapshot chỉ để `validate` chạy offline; gate CI không bao giờ dùng nó.
API/Dashboard vẫn đọc `projects_dir` của bản deploy: đổi đăng ký thì phải redeploy, nên dashboard có thể lệch policy gate cho tới lúc đó.

## 2. File gọi (trong repo SUT: `.github/workflows/qc-gate.yml`)

```yaml
name: qc-gate
on:
  pull_request:
    branches: [main]
  workflow_dispatch:           # chạy tay từ tab Actions
    inputs:
      workers:
        description: "vd semgrep,schemathesis — để trống = toàn bộ theo policy"
        type: string
        default: ""

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
      workers: ${{ inputs.workers }}   # input của `with:`, không bao giờ vào `run:`
      # sut_env: |               # biến môi trường cho container SUT (KHÔNG đặt bí mật)
      #   QC_BUGS=none
      # sut_base_url: http://...  # SUT đã chạy sẵn: bỏ qua build/chạy SUT. SUT cần database: xem "SUT cần database" bên dưới
      # qc_api_url: https://qc.example.com   # lưu lịch sử tập trung (cần secret QC_API_TOKEN)
    secrets: inherit
```

Ghim `@<COMMIT-SHA>` (không dùng `@main`) để một thay đổi ở qc-agent không tự động đổi gate của bạn.

**Chạy tay (`workflow_dispatch`):** có `workers` (vd. `semgrep,schemathesis`) thì gate chạy đúng các worker đó (không LLM, không floor); để trống thì chạy toàn bộ theo policy (FULL SET). Trên PR `workers` luôn rỗng và Select chọn phạm vi.

**Tên file và job:** `init` sinh `.github/workflows/qc-gate.yml` (cùng `qc-groundtruth.yml`). Job id vẫn là `qc` nên Check Run `qc-agent / <project>` và branch protection không đổi. Repo cũ dùng `qc.yml` **vẫn chạy**: `validate` và `refine` nhận cả hai tên, `validate` chỉ ghi một NOTE. `init` thấy `qc.yml` thì **không** sinh `qc-gate.yml` (tránh hai job cùng project, hai Check Run) mà in hướng dẫn; việc đổi tên là của bạn: `git mv .github/workflows/qc.yml .github/workflows/qc-gate.yml`, rồi thêm khối `workflow_dispatch` ở trên nếu muốn chạy tay. Chạy `init --force` chỉ ghi thêm `qc-gate.yml`, không xoá `qc.yml`.

**Cache kết quả Select.** Trên PR, chạy lại cùng một thay đổi thì Select không gọi LLM nữa (`selection.json` có `source: "cache"` và `llm.cache_hit: true`). Cache nằm ở `~/.cache/qc-agent/select` (`QC_SELECT_CACHE_DIR`; `none` = tắt), được `actions/cache/restore` và `actions/cache/save` (ghim SHA) mang qua các lần chạy; lưu ngay sau Select nên gate BLOCKED không làm mất cache.
- Mỗi entry là một file đặt tên theo hash của diff đã prune + module-map + policy + model + prompt + danh sách worker/suite. Đổi một trong các thứ đó là miss.
- Chỉ lưu phần LLM chọn thêm. Floor, rules và FULL SET luôn được tính lại; fallback (LLM lỗi, 529, timeout…) không bao giờ được lưu, nên một lần lỗi thoáng qua không bị "đóng băng".
- Lỗi đọc/ghi cache (file hỏng, đĩa đầy, thiếu quyền) chỉ là miss, không làm đỏ job.
- **Mô hình rủi ro:** cache của Actions tách theo ref; PR đọc được cache của nhánh gốc nhưng không ghi được vào đó. Kịch bản xấu nhất là cache của chính PR đó bị đầu độc để Select chọn *thiếu* worker ngoài floor trong đúng PR đó, tương đương injection vào prompt; floor vẫn do `core` ép. Entry được kiểm lại theo schema và theo allowlist hiện tại khi đọc.

**Cache sinh Ground-Truth** (`qc-groundtruth.yml`): bộ sinh MỘT lời gọi (mặc định) lưu output đã validate của LLM ở `~/.cache/qc-agent/gt` (`QC_GT_CACHE_DIR`); chạy lại cùng PRD + OpenAPI + model + prompt + `auth.yaml` thì không gọi LLM mà vẫn render ra đúng các file. Input `agent: true` **không** dùng cache vì agent đọc mã nguồn qua nhiều lượt.

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

### SUT cần database

Mặc định workflow chạy **một container** SUT chỉ với biến từ `sut_env` (không bí mật) và chờ nó trả lời HTTP tối đa 120 s. Điều kiện để dùng được mặc định: **SUT khởi động và trả lời được chỉ bằng `sut_env`**. SUT bắt buộc có database lúc khởi động (ví dụ API đọc `DATABASE_URL` trong `lifespan` rồi thoát khi không nối được) sẽ không qua được bước "Start SUT". Có ba cách, chọn theo SUT:

1. **SUT có chế độ chạy không cần DB**: bật bằng `sut_env`, không cần gì thêm.
2. **Môi trường có sẵn**: khai `sut_base_url` (URL SUT đã chạy). Workflow bỏ qua cả build/chạy SUT lẫn DB phụ (nếu có khai thì chỉ cảnh báo). Cổng gate phải với tới URL đó từ runner.
3. **DB phụ** (mô tả dưới đây): workflow chạy thêm container `db` trong mạng `qc-net`, chờ DB sẵn sàng rồi mới chạy SUT.

```yaml
    with:
      project: myapp
      image: ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST>
      sut_db_image: postgres@sha256:<DIGEST>          # BẮT BUỘC ghim digest (trừ allow_unpinned_image: true); loại DB do SUT chọn
      sut_db_env: |                                    # KHÔNG bí mật
        POSTGRES_DB=app
        POSTGRES_USER=app
      sut_db_ready_cmd: pg_isready -h 127.0.0.1 -U app -d app   # chạy trong container db; exit 0 = sẵn sàng
      # sut_db_port: "5432"                            # thay cho ready_cmd: chỉ thăm dò kết nối TCP tới db:<cổng>
      sut_env: |                                       # cũng KHÔNG bí mật
        APP_ENV=ci
    secrets: inherit
```

| Input / secret | Ý nghĩa |
|---|---|
| `sut_db_image` | image DB; rỗng = không có DB (đường chạy cũ, không đổi một ký tự). Khai thì phải khai thêm `sut_db_ready_cmd` hoặc `sut_db_port` |
| `sut_db_env` | `KEY=VALUE` mỗi dòng cho container `db`, không bí mật |
| `sut_db_ready_cmd` | lệnh chạy bằng `docker exec db sh -c`; ưu tiên hơn `sut_db_port`. Với Postgres dùng `-h 127.0.0.1` để khỏi tính máy chủ tạm lúc khởi tạo (chỉ nghe socket) |
| `sut_db_port` | cổng TCP dự phòng; "mở được kết nối" chưa chắc là "sẵn sàng nhận truy vấn" nên đây là lựa chọn yếu hơn |
| secret `SUT_SECRET_ENV` | nhiều dòng `KEY=VALUE` cho **container SUT**: `DATABASE_URL=postgresql://app:<mật khẩu>@db:5432/app`, token ký, ... |
| secret `SUT_DB_SECRET_ENV` | nhiều dòng `KEY=VALUE` chỉ cho **container `db`**: `POSTGRES_PASSWORD=<mật khẩu>`. Tách khỏi `SUT_SECRET_ENV` để image DB không nhận bí mật của SUT |

Thứ tự: tạo mạng `qc-net` → chạy `db` (tên cố định, SUT gọi `db:<cổng>`; không publish cổng ra runner) → chờ sẵn sàng (tối đa 120 s) → build và chạy SUT → (UI). DB không sẵn sàng hoặc đã thoát thì step đỏ với `::error::db không sẵn sàng sau 120s` kèm 50 dòng log cuối của container `db` (không in env); SUT không nối được DB thì đỏ ở `sut không sẵn sàng sau 120s` kèm log của SUT.

Về bí mật: hai secret được ghi vào file quyền 600 trong `$RUNNER_TEMP` rồi truyền bằng `--env-file`, nên không nằm trên dòng lệnh và không đi qua `${{ }}` trong script; từng giá trị được `::add-mask::` trước khi dùng; file bị xoá ở bước "Clean up SUT" cùng container `db`. Mỗi dòng phải là `KEY=VALUE` (dòng sai làm step đỏ mà **không in dòng đó**); `--env-file` của Docker không hỗ trợ giá trị nhiều dòng hay ngoặc kép. Cả hai secret để trống thì lệnh `docker run` của SUT y như trước.

Lưu ý:
- **Migration là việc của image SUT** (entrypoint tự chạy, hoặc SUT tạo bảng lúc khởi động). Workflow không chạy lệnh nào trong SUT ngoài `docker run`.
- PR từ **fork** không nhận secret: `SUT_SECRET_ENV`/`SUT_DB_SECRET_ENV` rỗng, DB thường không khởi tạo được (Postgres đòi mật khẩu) và gate đỏ ở "Start SUT" thay vì xanh giả.
- Container `db` nằm cùng mạng `qc-net` với gate nên mã của PR (vd. worker eval) với tới được DB: dùng DB dành riêng cho CI, không đặt dữ liệu thật.
- `qc-agent init` / `qc-agent validate` **cảnh báo** (không lỗi) khi mã SUT tham chiếu `DATABASE_URL`, `SQLALCHEMY_DATABASE_URI`, `DB_URL`, `MONGODB_URI` hay `MONGO_URL` mà job không khai `sut_db_image` hay `sut_base_url`. Bỏ qua cảnh báo nếu SUT có chế độ không DB. `init` chỉ sinh comment mẫu trong `qc-gate.yml`, không bật DB giúp bạn.

## 2b. Sinh sẵn cấu hình (khuyến nghị) và kiểm trước khi đẩy lên CI

Thay vì viết tay suite/`qc-gate.yml`/Dockerfile UI, chạy **một lệnh** ở gốc repo SUT (Pha 1 của onboarding, offline, không cần Python, không cần SUT đang chạy):

```
docker run --rm -v "$PWD:/sut" ghcr.io/muteen-felix/qc-agent@sha256:<DIGEST> init
```

Trên Linux, image chạy bằng user không phải root nên thêm `--user "$(id -u):$(id -g)" -e HOME=/tmp` để file mới thuộc về bạn (chạy bằng root thì `init` tự `chown` các file vừa tạo theo chủ của `/sut`). Trên Git Bash (Windows) đặt `MSYS_NO_PATHCONV=1`.

`init` **chỉ đọc** cây thư mục (scanner tất định, không mạng, không chạy code SUT, không LLM) và **chỉ ghi trong repo SUT**: `.github/workflows/qc-gate.yml`, `.qc-agent/suites/*.yaml`, `.qc-agent/perf/smoke.js`, `.qc-agent/midscene/{explore,canary}.yaml` và
`.qc-agent/Dockerfile.ui` (chỉ cho **SPA tĩnh**: Vite → `dist/`, CRA → `build/`, Next.js `output: 'export'` → `out/`; build bằng đúng lockfile, phục vụ bằng nginx cổng 8080). Không sinh config bên qc-agent: repo chưa đăng ký dùng `_default` (xem 1b).
Không ghi đè file đã có (trừ `--force`); `--dry-run` chỉ in kế hoạch. `--slug` mặc định lấy từ `origin` rồi tên thư mục. `qc-gate.yml` được ghim sẵn `uses:` theo commit build của image; **digest** thì image không tự biết nên vẫn là TODO.

Scanner đọc: Dockerfile API và context (gợi ý: gốc > `*/Dockerfile` > `docker/*Dockerfile*` > `{apps,services,packages}/*/Dockerfile`, context theo nguồn `COPY`; chỗ không chắc có `VERIFY`), cổng (`EXPOSE`/`--port`), route health (`/api/health` > `/health` > `/healthz`), có FastAPI hay không, biến CORS (`*CORS*`), thư mục UI, package manager (theo lockfile), Node (`engines.node`/`.nvmrc`, mặc định 22), biến URL API của UI (`VITE_*`/`REACT_APP_*`/`NEXT_PUBLIC_*`). Bỏ qua `.git`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `tests`... sâu tối đa 4.
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
(vd. `APP_UI_URL` khi chưa khai `sut_ui_dockerfile`), `qc-gate.yml` chưa ghim SHA/digest hoặc sai tên input, và mọi dấu `qc-agent:todo` còn sót.

## 2c. Refine trên CI (Pha 2)

Bước `Refine (onboarding suggestions)` nằm **sau `Start SUT`, trước `Run qc-agent gate`** trong workflow tái sử dụng. Input `refine: auto|off` (mặc định `auto`); chạy khi event là `pull_request` **và** repo còn marker
(`grep -rlE 'qc-agent:todo (REFINE|SUGGESTED)|qc-agent:begin refine' .qc-agent .github/workflows/qc-gate.yml`). Không còn marker thì bị bỏ qua ngay.

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
| `SUT_SECRET_ENV`, `SUT_DB_SECRET_ENV` (tuỳ chọn) | bí mật `KEY=VALUE` nhiều dòng cho container SUT / container DB phụ; xem mục "SUT cần database" |
| `qc_read_token` (tuỳ chọn) | đọc policy từ qc-agent và kéo image khi repo/image chuyển private; hiện không cần truyền |

PR từ **fork** không nhận secret: task cần key sẽ `skipped`; project nên đặt `on_skipped_gate_task: fail` cho mode `pr` để gate không xanh giả.

### Integration với hệ thống ngoài qua HAR

`init` sinh khung trung tính cho mọi repo, đuôi `.example` nên **chưa hoạt động**: `.qc-agent/suites/integration.yaml.example`, `integration-live.yaml.example` và `.qc-agent/integration/{support,tier1.spec,tier2.spec}.mjs.example`. Kích hoạt (chỉ khi repo thật sự cần):

1. Bỏ đuôi `.example` ở cả 5 file; viết kiểm tra thật trong hai spec (đổi tên test `todo_replace_me`, tên test = tên check trong `oracle.required`); thay `b_host` bằng host thật; ghi HAR đã lọc vào `.qc-agent/har/external.har` (hoặc đổi đường dẫn trong suite). `qc-agent validate` từ chối khung còn dấu `qc-agent:todo`, test `todo_*`, `b_host` placeholder, HAR không tồn tại, `test.fixme/skip`, `change-me`, và check bắt buộc không có test cùng tên.
2. Đăng ký suite trong policy project (`configs/projects/<slug>.yaml`, PR vào qc-agent): thêm `integration` vào `modes.pr.blocking_suites`. **Chưa đăng ký thì mode `pr` không chạy suite này** (`_default` không liệt kê nó); chỉ mode `manual` (`suites: "*"`) chạy.

Khi đã đăng ký, Tier 1 (thành phần nội bộ giả lập) và Tier 2 (phát lại trang ngoài từ HAR) chặn merge. Hạn chế của Tier 2: HAR chỉ áp lên trình duyệt của chính test; nếu hệ thống ngoài được gọi từ process khác thì HAR không thấy traffic đó.

Ghi lại HAR bằng tài khoản thử nghiệm hoặc dữ liệu ẩn danh, rồi chạy:

```bash
python tools/har_scrub.py raw.har .qc-agent/har/external.har
git grep -i -E "authorization|cookie|bearer|eyJ" .qc-agent/har/
```

Không commit `raw.har`. Sau scrub vẫn cần người thứ hai xem response body vì công cụ lọc credential, không bảo đảm xoá dữ liệu cá nhân.

Tier 3 nằm trong suite `integration-live`, chỉ chạy manual:

```bash
qc-agent run --project <slug> --mode manual --suites integration-live --sut-root <repo>
```

Tier 3 fail là tín hiệu trang ngoài hoặc flow đã đổi: điều tra rồi ghi HAR mới. Nó là discovery, không chặn merge và không được thêm vào `advisory_suites` của mode `pr`.

## 4. Chặn merge
Branch protection của repo SUT → *Require status checks* → chọn job `qc-agent / <project>` (chính job trong workflow). Job **xanh/đỏ theo exit code của gate**: `0` PASSED hoặc PASSED_WITH_WARNINGS, `1` BLOCKED, `3` lỗi cấu hình/hệ thống. Có thể đặt `--warn-exit N` để đổi exit code của PASSED_WITH_WARNINGS.

### 4b. Bỏ qua một finding Security có lý do

Khi gate Security (`sast`, `secrets`, `deps`) đỏ vì một finding mà bạn xác định là **chấp nhận được** (dương tính giả, hoặc rủi ro đã được duyệt), bỏ qua đúng finding đó bằng cơ chế của công cụ. Mọi dòng bỏ qua nằm **trong repo của bạn** nên hiện trong diff của PR để người review thấy. Đừng nới ngưỡng trong suite hay gỡ suite để "cho xanh".

| Finding | Cách bỏ qua | Ví dụ |
|---|---|---|
| Semgrep (`sast`) | comment `# nosemgrep: <rule-id>` ở **chính dòng** bị báo (hoặc dòng ngay trên nó); ghi lý do ở một comment riêng liền trước | `# cmd là hằng do CI đặt, không nhận input` rồi `subprocess.run(cmd, shell=True)  # nosemgrep: python-subprocess-shell-true` |
| gitleaks (`secrets`) | thêm **fingerprint** vào `.gitleaksignore` ở gốc repo (mỗi dòng một fingerprint, nên có comment `#` giải thích) | `# khoá giả trong tài liệu` rồi `docs/example.md:generic-api-key:12` |
| Trivy (`deps`) | thêm mã CVE vào `.trivyignore` ở gốc repo, kèm lý do | `# chưa có bản vá; chỉ dùng ở dev, không lên production` rồi `CVE-2024-12345` |

- Rule id, mã CVE và `file:dòng` có trong review Security của PR. Fingerprint của gitleaks (ở chế độ quét working tree có dạng `file:rule-id:dòng`) nằm trong `gitleaks.json` của artifact `qc-runs-*` (cùng `semgrep.json`, `trivy.json`).
- Nếu là **secret thật**: đừng bỏ qua. Thu hồi/xoay secret ngay rồi xoá khỏi mã (xoá khỏi commit cuối là chưa đủ, secret vẫn nằm trong lịch sử git).
- Bỏ qua là quyết định của người review PR, không phải của tác giả một mình. Ghi lý do đủ để người đọc sau này hiểu tại sao lúc đó chấp nhận.
- `trivy.db_age_days` đỏ (báo cáo nói DB CVE quá 14 ngày) **không phải finding của bạn** và không bỏ qua được bằng cách trên: image qc-agent đang dùng đã cũ, hãy cập nhật digest `image:` trong `qc-gate.yml` lên bản mới hơn.
- Lỗi công cụ (`error`) hoặc công cụ thiếu (`skipped`) cũng làm gate đỏ nhưng là lỗi hạ tầng, không phải finding: báo cho phòng QC thay vì bỏ qua.

### 4c. Bề mặt chưa có test (`coverage-debt`)

Nếu policy bật `coverage-debt` trong `advisory_suites`, mỗi PR dò bề mặt **mới thêm**
(endpoint API, route UI, operation OpenAPI) mà **chưa có test nào chạm tới**. Worker phát
finding Low có vị trí file/dòng khi tìm được. Finding Low không chặn merge:

- Check Run: **`PASSED_WITH_WARNINGS`** (kết luận `success`, kèm số cảnh báo Low).
- Finding trong diff được gắn vào đúng dòng PR; finding ngoài diff nằm trong thân review.
- Nếu cấu hình Jira, mỗi fingerprint Low tạo tối đa một ticket. Jira lỗi chỉ hiện cảnh báo, không đổi verdict.
- Reusable workflow checkout với `fetch-depth: 0` để Select tính đúng merge-base của PR;
  bạn không cần cấu hình gì để có bước này; workflow tái sử dụng phiên bản mới hơn tự có sẵn.

**Bỏ qua một bề mặt có lý do** (ví dụ endpoint nội bộ không cần test, hoặc route đã có test ở nơi khác
mà bộ dò không nhận ra): tạo `.qc-agent/coverage.yaml` ở gốc repo SUT.

```yaml
# .qc-agent/coverage.yaml — nằm trong repo của bạn nên mọi dòng bỏ qua hiện trong diff PR để người review thấy
ignore:
  - surface: "GET /internal/debug"
    reason: chỉ dùng nội bộ, không thuộc hợp đồng public
  - surface: "api_endpoint:POST /admin/*"     # tiền tố "<kind>:" tuỳ chọn, và glob (*) dùng được
    reason: đã có test ở service khác, kiểm bằng contract test riêng

test_globs:                                    # mặc định, chỉ khai khi thư mục test của bạn khác
  - .qc-agent/**
  - tests/**
  - e2e/**
  - midscene/**
```

- `ignore[].reason` **bắt buộc**, không được rỗng — đây là quyết định của người review, không phải
  lối tắt để im lặng tắt cảnh báo.
- `test_globs` là danh sách glob quyết định file nào được coi là "test" khi bộ dò kiểm "đã có test
  chưa" (khớp tên đường dẫn theo mẫu, ví dụ `spec/**` cho repo dùng Playwright ở thư mục `spec/`).
Lịch sử cũ trong Postgres vẫn được giữ; luồng PR/manual mới không ghi sổ nợ và không cần database.

Để tạo Jira ticket cho finding Low, cấu hình project trong `configs/projects/<slug>.yaml`:

```yaml
jira:
  project_key: QCSB
  issue_type: Task
  user_map:
    github-login: jira-account-id
  max_new_per_run: 20
```

Đặt `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` trong secret của workflow gọi lại.

**Xác thực cho test Ground-Truth (tuỳ chọn).** Repo có `.qc-agent/ground-truth/auth.yaml` (xem `docs/groundtruth.md` §5e) thì đặt secret `QC_TEST_USERNAME`, `QC_TEST_PASSWORD` (tài khoản TEST riêng, quyền thấp). Workflow `qc-gate` truyền hai biến này vào container của gate; thiếu thì suite `gt-functional` báo `error`, không phải `fail`. Hai biến nằm trong môi trường của mọi worker trong lượt gate đó (chỉ `pytest` dùng), nên đừng đặt tài khoản có quyền rộng.
`user_map` ánh xạ tác giả PR sang Jira account ID; nếu không có ánh xạ, ticket vẫn được
tạo nhưng không gán người. Worker tìm ticket theo fingerprint trước khi tạo nên chạy lại
không tạo ticket trùng. Lỗi Jira được ghi trong `jira-status.json` và không đổi verdict.

## 5. Kết quả ở đâu

### Phạm vi chạy (Sprint 2)

Trên PR, workflow chạy `qc-agent select` trước gate. Selector đọc diff từ merge-base, áp dụng
`full_set_paths`, `docs_paths`, `module-map` rồi chỉ gọi LLM khi rules chưa quyết được.
Key `ANTHROPIC_API_KEY` là tùy chọn; nên dùng key CI có trần ngân sách. PR từ fork không có
key sẽ fallback FULL SET. Lỗi Select cũng dẫn đến FULL SET, không tự cho qua gate.

Policy `floor_workers` (mặc định gitleaks và semgrep) luôn chạy ở trigger PR. `core` gộp lại
floor kể cả khi `selection.json` bị sửa thiếu. Các list trong project override thay thế toàn bộ
list mặc định, không cộng dồn. `full_set_paths` khiến chạy mọi suite; PR chỉ đổi tài liệu
theo `docs_paths` chỉ chạy floor. Artifact `runs/selection.json` và mục **Phạm vi chạy**
trong report cho biết source, fallback, số suite và lý do chọn.

Chạy tay: `qc-agent run --project noteboard --mode pr --trigger manual --workers semgrep,gitleaks`.
Trên `workflow_dispatch`, input `workers` cũng đi theo đường manual: không gọi LLM, không thêm floor.
Checkout `fetch-depth: 0` tăng thời gian clone nhưng cần để tìm merge-base của PR; bước dò nợ
vẫn dùng `HEAD^1` như trước. Cách đọc chi phí của Select: xem "Chi phí LLM, prompt cache và trần token" bên dưới (chi phí trung bình sẽ đo khi nghiệm thu cuối).

- **Comment dính** trên PR (một comment, cập nhật tại chỗ mỗi lần push): bảng task chặn merge, skipped/error, finding tham khảo.
- **Check Run** `qc-agent / <project>`.
- **Lịch sử** trên dashboard (nếu bật `qc_api_url`), link nằm trong comment.
- **Artifact** `qc-runs-<project>-<attempt>` (14 ngày).

Trên PR, workflow chạy `gate → PR review → Jira (Low) → Report → upload → enforce`.
Jira chạy trước Report để lỗi 401/5xx được ghi thành cảnh báo trong Check Run cùng lượt;
Jira và PR review không đổi verdict. Check Run liệt kê số finding Critical/Medium/Low
và những finding đầu tiên. Giá trị mới là `BLOCKED`, `PASSED_WITH_WARNINGS`, `PASSED`;
dashboard vẫn đọc được các giá trị lịch sử `FAIL`, `YELLOW`, `PASS`.

### Chi phí LLM, prompt cache và trần token (Sprint 4)

Vận hành hằng ngày (xoay khoá, FULL SET tăng đột biến, Jira hỏng, làm sạch cache, đổi nhà cung cấp, event log): [operations.md](operations.md).

**Đọc dòng chi phí.** Đầu `report.md`, mục tóm tắt của Check Run và comment PR cùng in **một** dòng (cùng chuỗi, một con số tiền):

```
wallclock 1m02s · LLM: 5 340 in (4 096 từ cache) / 210 out · worker: 1 200 token (tasks: ai-eval) · ~$0.012
```

- `in` = token đầu vào của Diff Agent gồm phần không cache, phần ghi cache và phần đọc cache; `từ cache` là phần đọc từ prompt cache của API; `out` = token đầu ra.
- `worker: N token (tasks: …)` là phần của các worker có gọi LLM; contract chỉ cho tổng token nên không tách in/out.
- `~$` là **ước lượng** theo bảng giá duy nhất `src/qc_agent/llm/prices.py` (giá cached 2026-09-25, không phải hoá đơn) = chi phí worker + chi phí Diff Agent.
- Hậu tố `(chưa gồm giá của <model>)`: model không có trong bảng giá (mọi `gemini-*`) nên không ước được và không bị đoán.
- Hậu tố `(+N lời gọi timeout/lỗi mạng chưa rõ chi phí)`: request đã gửi mà không có response nên server **có thể đã tính phí**; số `~$` là **cận dưới**. Nếu phần đã biết bằng 0 thì dòng in `~$? (chưa rõ)`, không bao giờ in `$0.00` khi còn lời gọi chưa xác định.
- `N lần dùng cache (không tính phí)`: kết quả Select lấy từ cache S4-02 (`llm.cache_hit`), không gọi LLM nên không cộng vào tổng.
- **[Assumption, chưa kiểm chứng]** response lỗi HTTP (4xx, 429, 5xx có body lỗi của API) không bị tính phí nên không sinh dòng nào; cần đối chiếu tài liệu billing của nhà cung cấp.

**`llm_usage.json`** (`runs/<run_id>/llm_usage.json` của gate; `<egress-dir>/llm_usage.json` của `gt generate|regen`) là mảng JSON, mỗi lời gọi **đã gửi** một dòng, gồm cả lời gọi bị từ chối do output sai schema; không có nội dung (prompt, response, rationale). File vắng mặt nghĩa là không ghi nhận lời gọi nào. Các trường: `purpose, model, prompt_version, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, est_usd, cache_hit, duration_s`; trường tuỳ chọn `status` (khác `ok` khi lời gọi bị từ chối hoặc timeout), `usage_known: false`, `unknown_calls`, `turns` (dòng tổng của GT agent, một dòng cho cả lần chạy).
`null` luôn nghĩa là **không có dữ liệu**, không phải 0:

- `duration_s` là thời gian client chờ **một request HTTP** (không gồm thời gian ngủ backoff của Gemini). `null` ở dòng của **Diff Agent** (thời lượng không được lưu vào `selection.json` để khỏi đổi `plan_id`; xem `llm.call` trong log bước Select) và ở mọi dòng `cache_hit: true` (lần này không có request).
- token và `est_usd` là `null` khi `usage_known: false` (đã gửi, không có response) hoặc model không có trong bảng giá.
- Dòng `cache_hit: true` giữ số của lần tạo để minh bạch nhưng không được cộng vào tổng.

**Trần `QC_LLM_MAX_INPUT_TOKENS`** (mặc định 100000). Trước khi gọi, qc-agent ước lượng `ceil(số byte UTF-8 / 3)` token đầu vào (không gọi API, không gửi thêm dữ liệu, cùng công thức cho Claude và Gemini). **Đây là ước lượng gần đúng, không phải trần tuyệt đối**: có thể thấp hơn thực tế với ký tự hiếm; số thật nằm trong `llm_usage.json` sau lời gọi. Vượt trần: Select chạy FULL SET với `fallback_reason: token_cap` (không gọi LLM, gate không đỏ vì chi phí); `gt generate` thoát 3 và gợi ý tách PRD thành nhiều file. Kết quả cache hit không bị chặn. GT agent **không** áp trần này vì đã có ngân sách riêng (`QC_GT_AGENT_MAX_COST_USD`, `_MAX_TURNS`, `_MAX_WALL_S`).

**Ngưỡng prompt cache theo model.** Tiền tố tĩnh (`tools` + `system`) được đánh dấu `cache_control`; ngắn hơn ngưỡng thì API **im lặng không cache** (không lỗi, `cache_creation_input_tokens: 0`): `claude-sonnet-5` 1024 token, `claude-haiku-4-5` 4096 token. Trên repo `noteboard`, tiền tố của Diff Agent ước lượng chỉ khoảng 700 đến 1 100 token (ước lượng, chưa đo bằng `count_tokens`), nên với Haiku 4.5 `cache_read_input_tokens` rất có thể luôn là 0 trên repo nhỏ; qc-agent không độn prompt để vượt ngưỡng. Tiền tố của GT generator (khoảng 2 100 đến 3 400 token ước lượng) vượt ngưỡng của Sonnet 5. Gemini không dùng `cache_control`.

**Rút gọn diff trước khi gửi cho Diff Agent (pruner, S4-04).** Diff Agent nhận danh sách file đã prune, không phải `git diff` thô. Danh sách file **luôn đầy đủ** (kể cả lockfile, nhị phân, generated, vendor, file xoá, file đổi tên kèm `old_path`). File code giữ nguyên mọi dòng `+`/`-` của hunk; chỉ bỏ hunk chỉ có chú thích/dòng trống và cắt theo trần mỗi file (`per_file_tokens=1500`) và trần tổng (`total_tokens=12000`), tính bằng ký tự/4 nội bộ (**không phải số token thật**); mọi phần bị cắt hiện qua `truncated`/`dropped_hunks`. Floor security luôn chạy và LLM lỗi vẫn là FULL SET. S4-04 bỏ phần lặp mà không bỏ dòng thay đổi nào: header git trong hunk, dấu cách của JSON, trường mặc định (`old_path` rỗng, `truncated: false`, `dropped_hunks: 0`, `hunks: null`), và file đổi tên không còn nhúng cả file như file mới. Đây **đổi định dạng `user` message** (không đổi `diff_select.md`): recall của model thật phải đo lại.

Số đo (**ước lượng** `ceil(byte UTF-8 / 3)` của TOÀN BỘ request, không phải `count_tokens`; ca FULL SET/chỉ-floor không vào median; median trên ca đi đường LLM; "trần" = `1 − B_min/A` với B_min là danh sách file đầy đủ không hunk):

| dataset | split (số ca LLM) | giảm trước S4-04 (median) | giảm sau (median / P90) | trần ước lượng ở median | 40% ở median (ước lượng) |
|---|---|---|---|---|---|
| `noteboard` (nhãn đã duyệt) | tune (20) | −5,0% | +2,7% / +4,3% | 5,2% | chưa đạt |
| `monorepo-poly` (nhãn CHƯA KIỂM CHỨNG) | tune (14) | −3,7% | +5,6% / +19,8% | 10,1% | chưa đạt |
| `monorepo-poly` | holdout (6) | −4,7% | +3,8% / +7,1% | 10,6% | chưa đạt |
| `node-api` (nhãn CHƯA KIỂM CHỨNG) | tune (9) | −4,9% | +4,8% / +92,9% | 14,8% | chưa đạt |
| `node-api` | holdout (4) | −2,7% | +5,0% / +7,8% | 9,1% | chưa đạt |

**Chưa đạt 40% ở median theo ước lượng `ceil(byte/3)` với định dạng payload hiện tại**; `count_tokens` và recall thật còn PENDING, nên đây chưa phải kết luận về số token thật và mục tiêu 40% của kế hoạch không bị đổi. Trần ước lượng ở median thấp (5–15%) vì đa số diff chỉ chạm 1–2 file nhỏ, phần diff chỉ chiếm khoảng 7–18% request, còn tiền tố cố định (system, module-map, catalog worker, schema tool) khoảng 850–900 token ước lượng. Diff lớn thì có dư địa (diff 45 file đổi tên giảm 57,5% ở `node-api`, trong đó có 45 hunk chỉ đổi chú thích `helper … (moved)` của fixture này bị bỏ theo quy tắc COMMENT có sẵn và hiện qua `dropped_hunks`, nên không suy ra mọi thay đổi chú thích đều an toàn để bỏ; file 3 000 dòng bị cắt theo trần mỗi file giảm hơn 90%) nhưng không đại diện cho median. Trước S4-04 payload đã prune còn **to hơn** diff thô ở hầu hết ca LLM nhỏ (20/20 ca ở `noteboard`). Số **token thật** (`count_tokens`) và **recall thật** chưa đo (PENDING): các con số trên không nói gì về chất lượng chọn worker của model.

## 6. Rủi ro còn lại (biết trước)
- Worker eval của **chính repo SUT** (ví dụ `pytest tests/eval`) chạy mã của PR với các key LLM trong môi trường. Với PR cùng repo, tác giả là người có quyền ghi; hãy dùng key riêng cho CI với **giới hạn ngân sách**.
- Workflow chưa được chạy trên GitHub thật ở thời điểm viết tài liệu này: các bước shell đã được kiểm chứng trên Docker cục bộ bằng `tools/run_reusable_locally.py` và `tests/test_reusable_workflow.py`.
