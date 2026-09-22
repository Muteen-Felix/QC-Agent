# 2. Landscape công cụ QC/agentic testing

## Bốn câu hỏi dùng để đọc thị trường

1. Công cụ thuộc nhóm năng lực nào, và chạy trên surface nào?
2. Nó **tự hành [A]** hay chỉ **hỗ trợ [B]** con người/công cụ khác?
3. Có CLI/SDK, structured output và exit code đủ tin cậy để cắm vào contract không?
4. Oracle cuối là assert tất định, heuristic hay LLM judgment?

Số sao GitHub không trả lời bốn câu hỏi này và **không được dùng làm tín hiệu dự án còn sống**. Tín hiệu tốt hơn là release/push gần nhất, tài liệu CLI thực tế, structured output và một smoke test tại thời điểm nghiên cứu.

## Taxonomy 9 nhóm theo 2 trục

Hai trục là **mức tự hành** (`[A]` tự lập kế hoạch/thao tác; `[B]` hỗ trợ hoặc thực thi theo cấu hình) và **loại năng lực QC**.

| Nhóm | Năng lực | Loại |
|---|---|---|
| G1 | Unit/component generation và execution | [B] |
| G2 | Record–replay / traffic capture | [B] |
| G3 | Property-based, fuzz, invariant | [B] |
| G4 | Self-healing test | [A] tạo patch; human duyệt |
| G5 | API/contract testing | [B] |
| G6 | UI agent theo flow/mục tiêu đóng | [A] step-bound |
| G7 | Autonomous exploratory testing | [A] |
| G8 | AI-app/RAG evaluation, LLM judge | [A]/[B] lai |
| G9 | Orchestration, execution và triage đa tool | [B], có thể có AI triage |

G3 thường bị bỏ quên dù giải đúng bài toán “không biết exact expected output” bằng invariant. Ngược lại, G8 thường được bán cho cùng bài toán với chi phí và phương sai cao hơn.

## Roster 14 công cụ

Độ tin cậy phản ánh mức đã kiểm trong artifact, không phản ánh độ nổi tiếng. “Hoạt động gần nhất” giữ nguyên mức chắc chắn của snapshot nghiên cứu; nơi artifact không còn ngày gốc được ghi thẳng là chưa chốt.

| Công cụ | Nhóm / loại | Độ tin cậy | Hoạt động gần nhất trong artifact | Kết luận |
|---|---|---|---|---|
| Midscene CLI | G6 [A] | Cao | Smoke verify 2026-09-18; live 2026-09-22 | Có CLI, summary JSON, exit code; discovery worker của PoC |
| TestZeus Hercules | G7 [A] | Trung bình | Chưa chốt ngày trong artifact | Hợp lệ cho discovery lane; cần làm rõ assertion node; AGPL-3.0 |
| Explorbot | G7 [A] | Trung bình | Chưa chốt ngày trong artifact | Tự hành mạnh nhưng A9=1, report khó tích hợp và lượt chạy dài |
| browser-use | G6/G7 [A] | Trung bình | Chưa chốt ngày trong artifact | Automation tác vụ, không có oracle QC rõ |
| Skyvern | G6 [A] | Trung bình | Chưa chốt ngày trong artifact | Server nặng và AGPL-3.0 |
| agent-device | Mobile tool layer [B] | Cao | Chưa chốt ngày trong artifact | Không phải LLM agent; cần emulator |
| Maestro | Mobile execution [B] | Trung bình | Chưa chốt ngày trong artifact | Surface mobile, ngoài phạm vi PoC Windows |
| Schemathesis | G3/G5 [B] | Cao | Chạy thật trong PoC | Gate API theo schema/property |
| Keploy | G2/G5 [B] | Cao về rào cản | Chưa chốt ngày; yêu cầu eBPF đã kiểm | Hợp legacy integration nhưng cần WSL2/Docker/Linux |
| k6 | Load/performance [B] | Cao | k6 v2.2.0 đã smoke test | Gate threshold tất định |
| DeepEval | G8 [A]/[B] | Cao | DeepEval 4.2.3 đã chạy thật | Metric tất định và G-Eval advisory phải tách provenance |
| Ragas | G8 [B] | Trung bình | Last push ghi nhận 2026-02-24 sau khi đổi owner | Không chọn cho PoC; cần đánh giá lại độ sống trước khi dùng |
| Testkube | G9 [B] | Trung bình-cao | Chưa chốt ngày trong artifact | Execution engine dài hạn; cần Kubernetes |
| ReportPortal | G9 [B] + ML triage | Trung bình-cao | Chưa chốt ngày trong artifact | Tốt cho gom/triage; không thay contract provenance |

## Capability matrix: tự hành không đồng nghĩa với đủ điều kiện gate

| Capability | [A] tự hành | [B] hỗ trợ | Gate an toàn khi |
|---|---|---|---|
| Sinh test/flow | Hercules, Explorbot | generator truyền thống | Candidate qua review và filter tất định |
| Lái UI | Midscene, Skyvern, browser-use | Playwright/Maestro | Assert cuối độc lập với LLM |
| Tìm bug mở | Explorbot, Midscene | crawler/rule engine | Chỉ ở discovery lane |
| API property | — | Schemathesis | Schema/invariant được pin |
| Load/perf | — | k6 | Threshold và môi trường được pin |
| AI-app eval | LLM judge | DeepEval metric | Chỉ metric tất định gate; judge advisory |
| Triage | AI triage | ReportPortal | Không tự đổi verdict hay tự nộp ticket |

## Bảy surface và độ chín

| Xếp hạng | Surface | Độ chín / oracle khả dụng |
|---:|---|---|
| 1 | API | Cao nhất: schema, differential/replay, invariant |
| 2 | Unit/component | Cao: oracle cục bộ và chạy rẻ |
| 3 | Web functional | Khá: DOM/a11y + assert; exploration chỉ bổ sung |
| 4 | Performance | Khá: metric/threshold rõ nhưng nhạy môi trường |
| 5 | AI application/RAG | Trung bình: cần dataset, baseline, calibration và provenance |
| 6 | Mobile | Trung bình-thấp trong sprint này: emulator/device farm là rào hạ tầng |
| 7 | Desktop | Thấp trong phạm vi hiện tại: driver và môi trường phân mảnh |

## Ba chỗ bị overhype

1. **“Agent tự viết test sẽ thay regression suite.”** TestGen-LLM chỉ có 57% pass ổn định; exploration cũng có trần coverage khoảng 65%. Hai thứ này sinh candidate, không thay oracle.
2. **“Self-healing càng nhiều càng tốt.”** Heal rate tăng là mùi hỏng: selector/spec đang trôi hoặc patch đang che regression. Patch máy sinh phải đi PR riêng, không tự merge.
3. **“LLM judge giải được bài toán không có expected output.”** Nhiều trường hợp thực chất có thể dùng schema, invariant hoặc tool-call assertion. Judge còn có position bias mạnh đúng vùng so sánh hai bản gần nhau — vùng regression testing.

Nguồn tổng hợp: `docs/architecture.md` D1, D4, D5, §8–§9; nhãn `[R2 S5–S8]`, `[R3 S9–S11]`. Các dòng chưa có ngày hoạt động cần được double-check trước khi biến thành tuyên bố về trạng thái hiện tại.
