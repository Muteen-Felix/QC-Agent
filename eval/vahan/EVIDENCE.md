# Báo cáo bằng chứng: agent sinh test case trên SUT thật (vahan-api-server)

Mục đích: cho thấy bộ test do agent Ground-Truth sinh ra có **bắt được lỗi thật** hay không (mutation testing), đo trên một SUT thật chứ không phải noteboard. Báo cáo ghi cả những lần chạy hỏng và các giới hạn của phép đo; số liệu thô nằm trong `eval/real*.json`.

## 1. Kết luận được phép và không được phép

| Được phép nói | Không được phép nói |
|---|---|
| Với PRD và mã nguồn của `apps/api-server`, một lượt chạy của agent (Claude Sonnet 5.5) sinh 66 test case; sau khi loại 7 TC đỏ trên SUT sạch, 59 TC còn lại **bắt được 13/15 mutant** (86,7%). | "Agent tốt hơn sinh test một lời gọi (single-shot)": chưa có baseline (xem mục 5). |
| Độ phủ AC theo nhãn: 24/26 AC kiểm được qua HTTP (92,3%). | "Agent đạt ngưỡng 90% của kế hoạch ban đầu": hai trong bốn ngưỡng tự động bị hụt (kill rate 86,7% < 90%; TC xanh 89,4% < 90%). Xem cập nhật ngay dưới bảng. |
| Hai mutant sống sót chỉ ra hai lỗ hổng cụ thể của bộ test (mục 4). | Con số này đại diện cho agent nói chung: chỉ **1 lượt, 1 SUT, 15 mutant**, không có độ biến thiên. |

> **Cập nhật 2026-10-05 (quyết định của chủ dự án).** Hết ngân sách API nên không đo lại; ngưỡng nghiệm thu S1 cho phép đo LLM thật được hạ từ 90% xuống 85% **sau khi đã có số đo này** và chốt ở 1 lượt (kế hoạch gốc: median 3 lần). Theo ngưỡng mới cả ba chỉ số đạt (92,3% / 89,4% / 86,7%), nhưng kill rate chỉ hơn ngưỡng đúng 1 mutant (13/15; 12/15 = 80% sẽ không đạt) và không có độ biến thiên. Cột "Ngưỡng" ở mục 2 giữ nguyên 90% vì đó là ngưỡng lúc đo. Mọi giới hạn ở mục 6 vẫn đúng.

## 2. Kết quả lần chạy hợp lệ (lần 3)

Nguồn: `eval/real.json`. Bộ vừa sinh: `runs/eval-generated-20261004T010946Z/agent-run1/` (catalog.json, meta.json, egress.jsonl).

| Chỉ số | Giá trị | Ngưỡng | Đạt |
|---|---|---|---|
| Mutant bị bắt (trên bộ vừa sinh, chỉ tính TC xanh) | **13/15 = 86,7%** | ≥ 90% | Không |
| Mutant `required` bị sót | 0 (M01, M05, M14 đều bị bắt) | 0 | Có |
| AC coverage theo nhãn `non_testable` (a) | **24/26 = 92,3%** (thiếu AC-1.5, AC-1.7) | ≥ 90% | Có |
| TC xanh trên SUT sạch (b) | **59/66 = 89,4%** | ≥ 90% | Không (thiếu 0,6 điểm) |
| Scorer coverage AC / technique / API | 100% / 100% / 100% | 100% | Có, nhưng xem ghi chú |
| Hoàn tất | Có (15 lượt, 322 s, `stop: finished`) | | |
| Chi phí | ước tính của tool **$1,064** (trần đặt $1,0; tool chỉ kiểm trần trước mỗi request nên vượt tối đa một lượt) | | |
| Phân bố technique (66 TC) | negative_validation 19, boundary 17, error_handling 12, happy_path 10, authz 5, data_integrity 2, equivalence 1 | | |

Ghi chú về các con số:

- **TC xanh 89,4%:** 7 TC đỏ trên SUT sạch, gồm 6 TC về auth (AC-1.4 ×1, AC-1.6 ×2, AC-1.8 ×3) kỳ vọng hành vi mà hồ sơ đo đã tắt (mục 6), và 1 TC lỗi của agent (TC-AC-14.1-43336c: muốn kiểm `pageUrl` đúng 2000 ký tự nhưng chuỗi sinh ra dài 7970 ký tự nên SUT trả 422). Nếu loại 6 TC auth thì được 59/60, nhưng đó là phép tính **sau khi** thấy kết quả; con số báo cáo chính thức là 59/66.
- **Scorer 100%** tính cả 20 waiver do chính agent viết (18 `needs_infra_fault`, 2 `not_applicable`), nên không phải bằng chứng độc lập. Hãy dùng (a) theo nhãn QA thay cho scorer.
- 7 TC đỏ bị tool loại khỏi phép đo mutant (đúng thiết kế: TC đỏ trên SUT sạch fail ở mọi nơi nên "bắt lỗi" vô nghĩa). Mutant được đo trên 59 TC xanh.

## 3. Ma trận mutant (13/15 bị bắt)

Mỗi mutant vi phạm đúng các AC nêu ở cột AC, chỉ được áp lên **bản sao** của SUT. `req` = mutant bắt buộc phải bị bắt.

| Mutant | AC | Mô tả lỗi cấy | Bị bắt | TC bắt đầu tiên |
|---|---|---|---|---|
| M01 (req) | AC-1.3 | login chỉ cần username/password không rỗng, không so với cấu hình | ✅ | TC-AC-1.3-7d7d3b |
| M02 | AC-1.5 | `GET /api/auth/me` trả `user` thay vì `username` | ❌ | — |
| M03 | AC-1.2 | giới hạn độ dài username của login lệch một (128 → 129) | ✅ | TC-AC-1.2-8fb900 |
| M04 | AC-2.6 | `GET /api/runners` trả 201 thay vì 200 | ✅ | TC-AC-2.6-30f545 |
| M05 (req) | AC-4.4 | `source: "old"` không còn bị từ chối 410 | ✅ | TC-AC-4.4-f3248b |
| M06 | AC-4.2 | tạo Job cho runner không tồn tại trả 400 thay vì 404 | ✅ | TC-AC-4.2-c322e1 |
| M07 | AC-4.7, AC-5.3 | job_id không tồn tại trả 400 thay vì 404 (GET job và POST cancel, cùng một dòng mã) | ✅ | TC-AC-4.7-4c110f, TC-AC-5.3-bcc7ee |
| M08 | AC-11.3 | `POST /jobs/reports/verify` trả -1 thay vì 0 cho file không có | ✅ | TC-AC-11.3-88cc2e |
| M09 | AC-11.7 | `GET /jobs/{id}/no-data` không có báo cáo trả 410 thay vì 404 | ✅ | TC-AC-11.7-9d3d03 |
| M10 | AC-11.8 | `GET /jobs/reports/file/{name}` không có file trả 400 thay vì 404 | ✅ | TC-AC-11.8-962b68 |
| M11 | AC-12.1 | chu kỳ kiểm tra UI mặc định 7 ngày thay vì 3 | ❌ | — |
| M12 | AC-12.3 | cận trên `intervalDays` lệch một (365 → 366) | ✅ | TC-AC-12.3-e4e99a |
| M13 | AC-13.3 | `run-now` với runnerId không tồn tại trả 409 thay vì 404 | ✅ | TC-AC-13.3-1bea98 |
| M14 (req) | AC-14.1 | `POST /ui-health/logs` trả 200 thay vì 201 | ✅ | TC-AC-14.1-127d4f (+2 TC) |
| M16 | AC-14.6 | tải file CSV không tồn tại trả 410 thay vì 404 | ✅ | TC-AC-14.6-9d6e5a |

Tất cả mutant bị bắt đều bị bắt bởi TC thuộc đúng AC đã khai (không có trường hợp "bắt nhầm cửa"). Ghi chú: không có M15. Mutant này đã bị loại có chủ ý trước mọi lần gọi API thật, vì sau khi PRD được đồng bộ với mã nó không còn vi phạm AC-14.4 một cách rõ ràng; mã định danh của các mutant còn lại được giữ nguyên.

Các mutant đều được kiểm là có hiệu lực thật trước khi đo (`eval/vahan/probe_mutants.py`: phản hồi của SUT khác bản sạch và không trả 5xx).

## 4. Vì sao hai mutant sống sót

| Mutant | Nguyên nhân | Loại |
|---|---|---|
| **M11** (mặc định 3 → 7) | Có TC cho AC-12.1, nhưng nó chỉ kiểm `intervalDays` thuộc kiểu số nguyên chứ không kiểm giá trị mặc định là 3. | **Lỗ hổng chất lượng test** do agent (assertion yếu). Đây đúng là kiểu lỗi mà mutation testing sinh ra để tìm. |
| **M02** (`user` thay vì `username`) | Không có TC nào cho AC-1.5. Agent waive AC-1.5 và AC-1.7 với lý do "cần token hợp lệ, mà credential là bí mật". | **Giới hạn của công cụ**: runtime test chưa hỗ trợ auth động và prompt cấm đưa token/credential vào test. Agent tuân luật; trong hồ sơ đo tắt auth thì endpoint này gọi được không cần token, nhưng agent không suy ra điều đó. |

## 5. Quá trình các lần chạy (minh bạch)

Tất cả lần gọi API thật đều dùng cùng PRD (sha256 ở mục 7), cùng mô hình `claude-sonnet-5-5`, 1 lượt (`--runs 1`). Chi phí là **ước tính của tool** (bảng giá $2/$10 mỗi triệu token); lần đối chiếu với hóa đơn thật: $2.73. Từ lần smoke, ước tính thấp hơn thực tế khoảng 19%.

| # | Mục tiêu | Cấu hình | Kết quả | Dùng được? |
|---|---|---|---|---|
| 0 | Smoke trên noteboard (SUT mẫu, không phải vahan) | `gt generate --agent`, trần $0,5 | 4 story, 25 AC, 39 TC, ~$0,42 ước tính (thực tế ≈ $0,5) | Chỉ để kiểm đường ống và chi phí |
| A | Đo thật `both` (agent + baseline single) | thiếu giá cho baseline | Dừng trước mọi lời gọi LLM (lỗi cấu hình) | — ($0) |
| B | Đo thật `both`, có giá baseline | trần agent $2,4 | Baseline single gọi API 1 lần rồi `GTError`; tool dừng, chưa chạy agent. Loại lỗi chưa được ghi (đã vá tool để in mã lỗi từ đó). Nguyên nhân nghi: bị cắt do `max_tokens=16000` với PRD 98 AC (giả định, chưa xác minh). | Không. Tốn ≤ ~$0,21 |
| **1** | Agent, lớp bọc ASGI gắn token cho mọi request | trần $2,4 | Hoàn tất 10 lượt, ~$0,67. TC xanh 12/31, AC coverage 4/26, mutant 2/15. **Không hợp lệ**: agent đọc mã thấy middleware đòi Bearer nên waive 59 mục "cần token", và 19 TC authz của nó kỳ vọng 401 nhưng lớp bọc luôn gắn token nên đỏ. Lỗi do thiết kế môi trường đo của chúng tôi, không phải năng lực agent. | **Không** (`eval/real-run1-invalid.json`) |
| 2 | Agent, hồ sơ tắt auth (sửa mã bản sao) | trần $1,8, timeout request 600 s | Một request treo đến hết timeout (`error_kind: timeout`) sau 9 request đã gửi; kết quả dở dang (8 TC, toàn US-1), số lượt/chi phí ghi 0 do lỗi của tool (đã vá). | **Không** (`eval/real-run2-timeout.json`) |
| **3** | Như lần 2, trần thấp hơn, timeout ngắn hơn | trần $1,0, timeout request 240 s | Hoàn tất, kết quả ở mục 2 | **Có** (`eval/real.json`) |

Hai thay đổi giữa lần 1 và lần 3 là **thay đổi môi trường đo**, không phải tinh chỉnh để ra số đẹp: (i) thay lớp bọc token bằng hồ sơ tắt auth ngay trong mã bản sao, (ii) đặt trần/timeout. PRD, nhãn và danh sách mutant giữ nguyên giữa các lần chạy.

Tool đo cũng được sửa trong quá trình (không đổi cách tính các chỉ số): lưu bộ vừa sinh ra đĩa trước khi đo, mutant lỗi khi đo không làm mất cả lượt, in mã loại lỗi, ghi chi phí khi lỗi giữa chừng. Các thay đổi này có test (109 test đạt) và nằm ở commit `4fa7adc`. **Lần 1 và lần 2 chạy trước các thay đổi đó** (lần 3 chạy sau khi vá phần lưu kết quả và ghi chi phí khi lỗi), nên số liệu `turns/cost` của lần 2 là 0 trong `real-run2-timeout.json`.

## 6. Giới hạn của phép đo

1. **Môi trường tắt auth.** Runtime test chưa hỗ trợ auth động (docs/groundtruth-real-sut.md, mục 1.1), nên bản sao lab có middleware bỏ qua kiểm token (`eval/vahan/prepare.ps1`, repo SUT thật không đổi). Hệ quả: AC-1.4, 1.6, 1.8 không đo được; agent cũng tự báo hai `spec_conflict` (AC-1.6, 1.8) về chính điểm này.
2. **Phạm vi hẹp: 26/98 AC.** 72 AC nằm ngoài tầm HTTP thuần (Socket.IO, Chrome extension, UI React) hoặc cần runner đã kết nối để có Job. Danh sách và lý do nằm trong `non_testable` của `eval/vahan.yaml`. Kết quả **không** nói gì về các AC đó.
3. **1 lượt, 1 SUT, 15 mutant.** Không có phân phối; mỗi mutant chiếm 6,7 điểm phần trăm. Khoảng chênh với ngưỡng 90% (13 → 14 mutant) nằm trong biên của một mutant.
4. **Nhãn và mutant là bản nháp của AI, chưa có QA duyệt.** `non_testable` và 15 mutant do Claude soạn từ PRD và mã SUT **trước khi** agent chạy lần nào, nhưng agent cũng là Claude, nên có nguy cơ tương quan (cùng cách hình dung "lỗi hợp lý"). Tài liệu của repo yêu cầu người gán nhãn không phải người viết prompt/PRD, tốt nhất là QA. Trước khi dùng làm bằng chứng chính thức, một người cần đọc lại và đổi trường `labeled_by`.
5. **Loại mutant hẹp.** Chủ yếu là mã trạng thái sai, lệch một ở biên, sai trường trả về. Không có lỗi logic nhiều bước hay lỗi tuần tự trạng thái.
6. **Chưa có baseline single-shot** (lần B lỗi, nguyên nhân chưa xác minh), nên không có kết luận so sánh.
7. **Chi phí ước tính, chưa đối chiếu hóa đơn.**

## 7. Tái lập

| Mục | Giá trị |
|---|---|
| Mô hình | `claude-sonnet-5-5` (agent), prompt `gt-agent/1` |
| SUT | `vahan-automation`, commit `fcb6fb78d5a7eb8f0a31763bcd2a95e024f6cdd6`, thư mục `apps/api-server`; snapshot bằng `git archive`, bỏ `tests/` và `.venv`; hồ sơ đo: auth tắt trong `app/main.py` (xem `runs/vahan-sut.source`) |
| PRD | `docs/prd/vahan.md`, sha256 `9d0957be7ef70b3926102b76b286db934a2f52ed0233208650d2fae6831d265c` (**chưa được commit** ở repo SUT) |
| OpenAPI | `eval/vahan/openapi.json`, sha256 `cf446b9f9cc94fe5f0c83a74019166d03d4283dbc03919e8353ceda103aab1c4` (xuất từ mã bản sao) |
| Cấu hình đo | `eval/vahan.yaml`, sha256 `377dfdf556c7c91b41ae2018b5c2506897877cafcf17db6f7aa289e64a27da1c`; ngưỡng 0,9 / 0,9 / 0,9 |
| QC-Agent | commit `4fa7adc` (tool đo đã vá) trên nền `de31da5`. **Lần 3 chạy trên mã này nhưng chưa được commit lúc chạy**: sau đó mã được commit nguyên trạng, không sửa thêm logic. Lần 1 và 2 chạy trên `de31da5` cộng bản vá chưa hoàn chỉnh (chưa có lưu kết quả/ghi chi phí khi lỗi) |
| Lưu ý hash | `openapi.json` trên Windows có CRLF trong working copy; Git chuẩn hóa về LF, nên sha256 trên máy khác (Linux/CI) sẽ khác. So bằng nội dung đã chuẩn hóa dòng nếu cần |
| Môi trường | Python 3.11.16 (venv lab riêng cho SUT ở `runs/vahan-venv`) |

Lệnh chạy lần 3 (đặt khóa API qua biến môi trường, không đọc từ file `.env`):

```powershell
powershell -ExecutionPolicy Bypass -File eval\vahan\prepare.ps1      # dựng snapshot + venv lab
$env:ANTHROPIC_API_KEY = "<khóa>"
$env:QC_GT_MODEL = "claude-sonnet-5-5"; $env:QC_GT_AGENT_MAX_COST_USD = "1.0"; $env:QC_GT_AGENT_TIMEOUT_S = "240"
.\.venv\Scripts\python.exe tools\eval_gt_sut.py --config eval\vahan.yaml --llm real --generator agent `
  --generated-mutants --runs 1 --max-total-usd 1.0 --yes --out-json eval\real.json
```

Đo lại bộ đã lưu mà **không gọi LLM**: thêm `--from-saved runs\eval-generated-20261004T010946Z` (và bỏ `--yes`).

Kiểm khô miễn phí (không gọi LLM): `python tools/eval_gt_sut.py --config eval/vahan.yaml --check-only`.

## 8. Việc cần làm trước khi trình hội đồng

1. Một QA đọc lại `non_testable` và 15 mutant, rồi cập nhật `labeled_by`.
2. Đối chiếu chi phí thật trên Console và điền vào mục 5.
3. Commit PRD ở repo SUT (hoặc giữ sha256 làm mốc): `docs/` của repo SUT vẫn là untracked nên commit SHA của SUT không ghim được PRD.
4. Nếu còn ngân sách: chạy thêm lượt để có độ biến thiên, và đo baseline single sau khi xác minh nguyên nhân lỗi ở lần B.
