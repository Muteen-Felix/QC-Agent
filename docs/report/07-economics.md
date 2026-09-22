# 07 — Kinh tế vận hành và dữ liệu rời máy

## Luận điểm kinh tế

Quality gate xanh không cần gọi LLM để **quyết định verdict**. Plan đã là artifact được review; registry route bằng phép lọc deterministic; aggregation là AND trên deterministic assertion. Vì vậy planning, routing và aggregation không tạo token LLM trên đường phán quyết của gate.

| Khoản | Khi nào phát sinh | Cách triệt tiêu khỏi gate xanh |
|---|---|---|
| Planning: spec → plan | Khi spec/contract đổi | Sinh plan ngoài CI, review diff rồi commit `plan.yaml`. Mỗi PR chỉ đọc artifact đã pin. |
| Routing | Mỗi task | Lọc manifest theo capability, lane, oracle và probe; không dùng LLM. |
| Aggregation / triage | Sau khi có kết quả, triage sâu chỉ khi fail | Gate chỉ AND các verdict tất định; LLM judgment là advisory. |

Nếu G-Eval advisory được bật trong task DeepEval thì nó vẫn có thể gọi model, nhưng kết quả đó không có quyền đổi verdict. Vì vậy câu chính xác là **“gate verdict không tiêu token LLM”**, không phải khẳng định mọi process chạy trong cùng lượt đều có chi phí bằng 0.

## Số đo từ PoC

Nguồn là `recordings/demo-good-run/results/*.json`, run đầy đủ được render trong `recordings/demo-good-run/report.md`. Các số dưới đây là số worker tự báo; `null` nghĩa là adapter không nhận được số đo, không phải 0.

| Task | Worker | Status | Wallclock (s) | Tokens | USD |
|---|---|---|---:|---:|---:|
| `t-000` | http-collect | pass | 0.158 | 0 | 0.000 |
| `t-001` | schemathesis | fail | 3.287 | 0 | 0.000 |
| `t-002` | k6 | pass | 30.411 | 0 | 0.000 |
| `t-003` | deepeval | fail | 17.050 | không được worker báo | không được worker báo |
| `t-101` | midscene-cli | pass | 17.206 | không được worker báo | không được worker báo |
| `t-canary-01` | midscene-cli | fail như kỳ vọng | 6.844 | không được worker báo | không được worker báo |

Tổng wallclock của các task là khoảng **74.956 giây**; report của run ghi khoảng 76 giây gồm điều phối và làm tròn. Bốn task gate có tổng wallclock khoảng **50.906 giây**. Các task deterministic không-LLM (`t-000`, `t-001`, `t-002`) báo 0 token. `t-003` có G-Eval advisory nên không được suy diễn token bằng 0 khi adapter báo `null`.

Đây là số đo của **toy app**; chi phí ở quy mô suite thật **chưa đo**. Chúng không dùng để ngoại suy sang ứng dụng thật, số endpoint khác, dữ liệu thật hoặc provider khác.

## Bảng chi phí theo 9 nhóm

Nhóm dưới đây giữ nguyên ranh giới nghiên cứu. Chỉ các nhóm có worker trong PoC mới có số đo; các ô còn lại là “chưa đo”, không phải chi phí bằng 0.

| Nhóm | Vai trò / tình trạng trong PoC | Chi phí kết luận được |
|---|---|---|
| G1 | Nhóm không nằm trong roster PoC | Chưa đo. |
| G2 | Record–replay; không triển khai vì rào hạ tầng | Chưa đo. |
| G3 | Property/fuzz: Schemathesis | 3.287 s trong recorded run; 0 token và $0 do worker báo. |
| G4 | Self-heal; chủ động không cho tự merge | Chưa đo; không phải tiêu chí thành công. |
| G5 | Plan generation ngoài CI | Chưa đo; gửi spec/acceptance criteria/mã nguồn nếu bật. |
| G6 | UI agent step-bound: Midscene | 17.206 s cho discovery; token/USD không được worker báo. |
| G7 | Autonomous exploration diện rộng; không vào PoC | Chưa đo. |
| G8 | AI-app eval: DeepEval/G-Eval advisory | 17.050 s; token/USD không được worker báo. |
| G9 | Nhóm không được roster PoC chọn | Chưa đo. |

## Egress và mức nghiêm trọng

Thứ tự rủi ro dữ liệu là **G8 > G6/G7 > G5 > G1–G3**. Nó ảnh hưởng lựa chọn worker trước cả điểm kỹ thuật.

| Mức | Worker / nhóm | Dữ liệu có thể rời máy | Kiểm soát |
|---|---|---|---|
| Cao nhất | G8 / DeepEval | Input và output của ứng dụng gửi cho model chấm | Khai báo `data_egress`; model judge khác SUT; dùng test environment. |
| Cao | G6/G7 / Midscene và explorer | Screenshot, DOM, trạng thái hiển thị có thể chứa dữ liệu khách hàng | Không cấp credential ghi; giới hạn discovery vào test environment. |
| Trung bình | G5 / plan-gen | Spec, acceptance criteria, có thể là source code | Chạy ngoài CI; human review plan trước khi commit. |
| Thấp / không egress từ worker PoC | G1–G3; Schemathesis, k6 | Không gửi dữ liệu tới LLM provider | Khai báo manifest trước khi chạy. |

PoC chỉ dùng `.env` local và `.gitignore`; bảo mật orchestrator, lưu evidence dài hạn và dọn dữ liệu vẫn **chưa đo/thiết kế ở mức production**. Vì vậy worker tự hành chỉ được trỏ vào test environment, không được trỏ vào production.
