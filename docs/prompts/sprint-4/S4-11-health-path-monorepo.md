# S4-11 · Chọn health path đúng service trong monorepo (plan S4.11)

- Branch: `fix/s4-11-health-path-monorepo`
- Tiền điều kiện: **S4-08 đã merge**. Cần từ S4-08: kết quả chọn Dockerfile và context, hàm đọc `COPY`/`ADD` trả cấu trúc (nguồn đã phân loại: thư mục cụ thể / tệp / `.` / glob / bỏ qua), và helper "input có VERIFY thì luôn được khai".
- **Khuyến nghị làm tuần tự S4-08 → S4-10 → S4-11 trên một worktree**: S4-10 và S4-11 cùng sửa `docs/user-guide-sprint-1.md` và `tests/test_scaffold_validate.py`. Chỉ chạy song song nếu tách worktree riêng và chủ động xử lý xung đột merge ở hai file đó. S4-11 chỉ *bắt buộc* sau S4-08.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.11 và S4.8; prompt S4-08 và code S4-08 đã merge
- `src/qc_agent/scaffold/scan.py`: `_scan_health`, `scan`, `_python_files`, `_rank_health`, `_rank_note`, `Overrides`, `Finding`
- `src/qc_agent/scaffold/init.py`: cách ghi `sut_port`/`sut_health_path` và `marks`, và dòng đưa health path vào `k6_smoke_script`
- `src/qc_agent/scaffold/templates.py`: `qc_workflow`, `_WORKFLOW_INPUTS`, `todo_mark`, `k6_smoke_script`; `src/qc_agent/scaffold/tmpl/k6-smoke.js.tmpl`
- `src/qc_agent/scaffold/validate.py` (chặn mọi `qc-agent:todo` còn trong workflow caller), `src/qc_agent/scaffold/refine.py` (so `sut_health_path` với OpenAPI sống)
- `tests/test_scaffold_scan.py` (nhất là `test_false_positive_health_prefix_is_one_candidate_so_no_verify` và `test_health_priority_and_default`), `tests/test_scaffold_init.py` (`sut_health_path == "/health"` của fixture vahan và `test_ambiguous_choices_carry_verify_and_nothing_else_does`), `tests/test_scaffold_validate.py`
- Fixture `tests/fixtures/scan/vahan-rpa/` (Dockerfile ở gốc, `COPY apps/api-server/app ./app`) và `tests/fixtures/sut/noteboard/`

## Mục tiêu

`qc-agent init` không lấy health path của **service khác** trong monorepo. Khi không chứng minh được file mã nguồn nào thuộc API đã chọn ở S4-08, hoặc có nhiều health path hợp lý, thì **không tự khẳng định**: ghi ứng viên kèm file nguồn và `qc-agent:todo VERIFY`.

## Sự thật cần biết (đã kiểm trong code)

1. `_scan_health(root, flag)` quét **mọi** `.py` trong repo (sâu ≤ 4) và không biết Dockerfile nào đã được chọn; `scan()` gọi nó sau khi chọn Dockerfile nhưng không truyền Dockerfile vào.
2. Xếp hạng `/api/health` > `/health` > `/healthz` > chữ. API dùng `/health` còn service khác dùng `/api/health` → `init` ghi `/api/health`.
3. Chỉ MỘT ứng viên thì không có VERIFY (được test giữ nguyên), nhưng ứng viên duy nhất đó có thể thuộc service khác.
4. `init` bỏ `sut_health_path` khi giá trị là `/` (bằng mặc định của workflow) nên VERIFY (nếu có) bị mất; cùng lớp lỗi mà S4-08 sửa cho `sut_dockerfile`/`sut_context`.
5. Health path còn được dùng ở nơi khác: `init` đưa nó vào script k6 smoke, và script đó **chỉ chấp nhận 2xx** (`x.status >= 200 && x.status < 300`); `refine` so `sut_health_path` với OpenAPI sống. Path sai, **kể cả `/` nếu API không trả 2xx ở `/`**, làm k6 smoke báo lỗi.
6. **Readiness của workflow coi mọi mã HTTP là "đã chạy"** (kể cả 404). Nên health path sai (hoặc `/`) vẫn qua bước chờ SUT và lỗi chỉ lộ muộn ở k6. Vì vậy VERIFY là lớp bảo vệ chính.
7. Fixture `vahan-rpa`: Dockerfile **ở gốc**, `COPY apps/api-server/app ./app`, route `/health` ở `apps/api-server/app/api/health.py`, có thêm `apps/web-ui` và `vahan-chrome-extension`; hiện cho `/health` không VERIFY và có test khoá điều đó. Fixture `noteboard`: `COPY toyapp ./toyapp`, không có route health, mặc định `/` không VERIFY.

## Nguyên tắc

1. Chỉ **chọn** health path. Không tuyên bố kiểm chứng sức khỏe API, DB hay nghiệp vụ.
2. **Build context không phải ranh giới service, và vị trí Dockerfile cũng không**: một Dockerfile ở `deploy/api/` có thể `COPY apps/api/...`. Phạm vi mã API chỉ được coi là đã chứng minh khi nội dung Dockerfile (nguồn `COPY`/`ADD`) cho thấy mã đó vào image.
3. Không chứng minh được → không khẳng định; ghi ứng viên **kèm đường dẫn file** và VERIFY.
4. Giá trị `/` khi chưa chứng minh chỉ là **giá trị tạm đang chờ xác nhận**, không phải giá trị an toàn: readiness chấp nhận mọi mã HTTP nhưng k6 smoke đòi 2xx, nên giữ `/` mà API không trả 2xx ở `/` thì xoá dấu VERIFY vẫn làm k6 lỗi. Thông báo VERIFY phải nói điều này và hướng dẫn thay `/` bằng path thật trước khi xoá dấu.
5. Route tĩnh không biết prefix `include_router` (nên `/health` trong fixture VAHAN có thể thực tế là `/api/health`): giới hạn đã biết, **không** sửa ở đây; thông báo VERIFY không được nói "đúng".

## Việc cần làm

1. **`--health-path` do người dùng đặt luôn thắng**: giữ nhánh flag hiện có, không quét gì, không VERIFY.
2. **Truyền Dockerfile đã chọn vào `_scan_health`** (đổi chữ ký; `scan()` gọi sau khi có kết quả S4-08). Xác định **phạm vi mã nguồn của API** = *hợp của các thư mục mà bằng chứng đưa vào image*; thư mục không được đối chiếu thì **không** vào phạm vi. Chỉ đọc `.py` nằm trong phạm vi. Thang bằng chứng (vị trí Dockerfile **không bao giờ đủ một mình**):
   - **E1 — thư mục chứa Dockerfile, có đối chiếu:** Dockerfile nằm ở thư mục con D (không phải gốc repo, không phải `docker/`) **và** có bằng chứng mã API nằm trong D: (i) có nguồn `COPY`/`ADD` là *thư mục cụ thể* nằm trong D (đã phân giải theo context S4-08 chốt không VERIFY), hoặc (ii) `COPY .`/`ADD .` mà context đúng bằng D (cả D được đưa vào image). Phạm vi gồm D. Nếu chỉ có tệp lẻ (vd `COPY pyproject.toml`) hoặc không có nguồn nào trỏ vào D → D **không** được coi là phạm vi mã.
   - **E2 — nguồn `COPY`/`ADD` tường minh (mọi vị trí Dockerfile):** lấy các nguồn là *thư mục cụ thể* (vd `COPY apps/api/ ./app`, `COPY apps/api-server/app ./app`) → phạm vi gồm các thư mục đó, kể cả khi chúng **nằm ngoài** thư mục chứa Dockerfile (ca `deploy/api/Dockerfile` copy `apps/api/` → phạm vi `apps/api`, không phải `deploy/api`). Nguồn `.`/`./` ở context gốc repo, glob, biến, tệp lẻ, hoặc nguồn mà S4-08 không phân giải chắc chắn → **không** đóng góp bằng chứng. Chỉ dùng khi context do S4-08 chốt **không có VERIFY** (context dùng để *phân giải đường dẫn nguồn*, không để *định nghĩa service*).
   - **E3 — đơn dịch vụ (hẹp):** đúng một Dockerfile ứng viên **và** không có dấu hiệu service thứ hai. Người thực hiện **định nghĩa điều kiện cụ thể** trong kế hoạch (ví dụ chỉ một gốc ứng dụng Python); mặc định khi nghi ngờ là *không đủ bằng chứng*. Mục đích: repo một service kiểu `COPY . .` không bị hỏi thừa.
   - Không đạt E1/E2/E3 → phạm vi **chưa chứng minh**. Phạm vi đã chứng minh nhưng không chứa file `.py` nào vẫn hợp lệ (API không phải Python, hành vi cũ), ghi vào `reason`.
3. **Ra quyết định theo trạng thái** (ghi `Finding.candidates` kèm file nguồn của từng ứng viên, `reason` nêu loại bằng chứng):

   | Phạm vi | Ứng viên health trong phạm vi | Kết quả |
   |---|---|---|
   | đã chứng minh | đúng 1 | dùng nó, không VERIFY |
   | đã chứng minh | ≥ 2 | xếp theo luật cũ (chỉ là gợi ý) + VERIFY liệt kê kèm file |
   | đã chứng minh | 0 | `/` mặc định, không VERIFY (hành vi hiện tại, vd `noteboard`) |
   | **chưa chứng minh** | liệt kê ứng viên toàn repo chỉ để **hiển thị** trong VERIFY, không để chọn | giá trị **tạm** `/` **+ VERIFY** nêu ứng viên kèm file; nội dung VERIFY có hướng dẫn thay `/` bằng path thật trước khi xoá dấu |

   - `/` là giá trị tạm, **không** gọi là "an toàn" (xem Nguyên tắc 4). Nếu người thực hiện thấy ứng viên xếp đầu phù hợp hơn `/` làm giá trị tạm, **báo thành quyết định** trong kế hoạch, không tự đổi.
4. **Marker luôn đến caller** (dùng helper của S4-08): `sut_health_path` có VERIFY thì được khai **kể cả khi giá trị là `/`** (hiện bị bỏ khi bằng mặc định). Nội dung VERIFY (kiểm bằng test) phải có: giá trị hiện tại là **tạm**, danh sách ứng viên kèm file, và hướng dẫn "đặt `--health-path` hoặc sửa `sut_health_path` thành path trả 2xx thật, rồi mới xoá dấu". Có test qua `init` và `validate`.
5. **Tài liệu:** `docs/user-guide-sprint-1.md` mục 3, dòng `--health-path`: khi nào có VERIFY và cách chốt; ghi giới hạn "route tĩnh không biết prefix `include_router`". Cập nhật docstring `scan.py` (thang bằng chứng). Chỉ tick task S4.11 ở `docs/implementation-plan.md`, không sửa nội dung plan.
6. **Kế hoạch ≤ 15 dòng trước khi code** (định nghĩa E3 cụ thể, cách phân giải nguồn E2, danh sách test) và chờ tôi duyệt.

## Test bắt buộc

Qua `scan`, và qua `init` + `validate` với các ca có VERIFY:

| Cây mẫu | Kỳ vọng |
|---|---|
| `apps/api-server/Dockerfile` (`COPY . .`, **context = `apps/api-server`**) có `/health` + `apps/gateway` có `/api/health` | `/health`, **không** `/api/health`; không VERIFY (E1 ii) |
| `apps/api-server/Dockerfile` sâu 2 cấp, **context gốc repo**, `COPY apps/api-server/app ./app`, service khác có health khác | health của API (E2), không phụ thuộc context là gốc repo |
| **`deploy/api/Dockerfile`** với `COPY apps/api/ ./app` (route `/health` trong `apps/api/`), service khác có `/api/health` | phạm vi `apps/api` (E2), kết quả `/health`; **không** quét `deploy/api` rồi chọn `/` không VERIFY |
| `deploy/api/Dockerfile` chỉ có `COPY requirements.txt` + nguồn không phân giải được; mã API ở nơi khác | **chưa chứng minh** → `/` tạm + VERIFY (vị trí Dockerfile một mình không đủ) |
| Dockerfile ở thư mục con D chỉ `COPY pyproject.toml` (tệp lẻ), D không chứa mã | không đạt E1 → VERIFY, không chọn `/` im lặng |
| Dockerfile gốc `COPY apps/api-server/app ./app` + service khác có `/api/health` | health trong `apps/api-server/app` (E2), không VERIFY — **và `vahan-rpa` giữ nguyên kết quả** |
| Dockerfile gốc `COPY . .`, ≥ 2 service, route health ở nhiều nơi | VERIFY, ứng viên kèm file, giá trị tạm `/`; `init` ghi marker dù là `/`; `validate` ERROR tới khi xoá dấu; thông báo VERIFY có hướng dẫn thay `/` bằng path thật trước khi xoá dấu |
| API không có route health, service khác có `/api/health`, phạm vi đã chứng minh | `/` mặc định (hành vi hiện tại, giữ nguyên), **không** nhận `/api/health` của service khác |
| `--health-path /x` | đúng `/x`, không quét, không VERIFY (ở `scan` và `init`) |
| repo một service hiện có (`noteboard`, `vahan-rpa`, các cây trong `test_scaffold_scan.py`) | kết quả và có/không VERIFY **như cũ** |

- **Quy tắc test cũ:** mọi test hiện có của scan/init phải xanh; nếu phải đổi test nào (đặc biệt `test_false_positive_health_prefix_is_one_candidate_so_no_verify` và `test_health_priority_and_default`) thì **liệt kê trong báo cáo kèm lý do**, không sửa lặng lẽ.
- Chạy `pytest tests/test_scaffold_scan.py tests/test_scaffold_init.py tests/test_scaffold_validate.py -q`.

## Nghiệm thu bằng `qc-agent init --dry-run`

Chạy trên các cây monorepo mẫu: ca E1, ca E2 (gồm `deploy/api`), ca không xác định, ca có `--health-path`. Với **mỗi** cây, báo:
- health path được chọn;
- dựa vào **file nào** và loại bằng chứng (E1 / E2 / E3 / flag / không có);
- ca nào cần người dùng xác nhận.

Cây mơ hồ không được xuất hiện chữ "đã xác định".

## Ngoài phạm vi

- **Tiêu chí readiness của workflow** (mọi mã HTTP là "đã chạy"). S4-11 **không** kiểm chứng DB hay sức khỏe nghiệp vụ của API. Muốn đổi tiêu chí đó thì báo thành một quyết định riêng; việc siết (vd chỉ 2xx/3xx) ảnh hưởng mọi SUT đang chạy.
- Giới hạn phạm vi quét của `openapi_url`, `fastapi`, `cors` (cùng kiểu lỗi quét toàn repo): không làm ở đây.
- Sửa prefix `include_router`.
- Ca "phạm vi đã chứng minh nhưng không có route health" vẫn cho `/` mặc định không VERIFY như hiện tại; nếu API không trả 2xx ở `/` thì k6 vẫn lỗi như trước. Muốn xử lý thì báo thành quyết định riêng.
- Không đụng `core/`, contract, workflow.
