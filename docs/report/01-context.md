# 1. Bối cảnh: “QC Agent” không phải một khái niệm duy nhất

Yêu cầu “làm QC Agent” dễ gom nhiều bài toán khác nhau vào một nhãn. Trước khi chọn công cụ, cần tách sáu khái niệm sau:

| Khái niệm | Đầu vào | Đầu ra | Có quyền phán quyết? | Vai trò phù hợp |
|---|---|---|---|---|
| Test automation | Test/spec đã biết | Kết quả chạy lặp lại | Có, nếu oracle tất định | CI gate |
| Test generation | Requirement, code hoặc trace | Test candidate | Không; phải review/lọc | Dev-time |
| Autonomous exploration | UI và mục tiêu mở | Finding/candidate path | Không mặc định | Discovery/nightly |
| Self-healing | Test hỏng vì UI/selector đổi | Patch candidate | Không tự merge | PR riêng có người duyệt |
| LLM-as-judge | Output khó chấm bằng assert cứng | Điểm/nhận xét | Chỉ advisory trong PoC | Đánh giá định tính |
| AI-application testing | Dataset, output AI, invariant | Metric tất định + đánh giá | Chỉ phần tất định được gate | Gate kết hợp eval |

Điểm phân chia quan trọng không phải “có dùng AI hay không”, mà là AI đang **sinh ứng viên**, **điều khiển thao tác**, hay **ra phán quyết**. Hai vai trò đầu có thể hữu ích; vai trò cuối không được âm thầm chặn hoặc mở merge.

## Neo định lượng: TestGen-LLM

Kết quả TestGen-LLM trên Instagram Reels/Stories được tổng hợp trong nghiên cứu nội bộ: 75% test sinh ra build được, nhưng chỉ **57% pass ổn định** và 25% làm tăng coverage. Nói cách khác, **43% bị vứt**. Giá trị triển khai không nằm ở việc LLM sinh được nhiều test, mà ở bộ lọc tất định quyết định test nào đủ điều kiện sống trong suite.

Do đó, nguyên tắc của đề xuất này là:

> **LLM sinh ứng viên, oracle tất định quyết định.**

Hệ quả kiến trúc là hai lane trên cùng contract:

- **Gate lane** chạy plan đã commit, routing tất định, không cho `llm_judgment` ảnh hưởng verdict.
- **Discovery lane** cho phép worker tự hành tìm candidate finding, nhưng không chặn merge; finding chỉ đi vào gate sau khi được chuyển thành assert có thể tái lập.

Đây không phải đề xuất “một AI agent thay QC”. Đây là lớp điều phối mỏng giữ provenance của phán quyết để dùng worker AI mà không đánh đổi tính tái lập của quality gate.

Nguồn tổng hợp: `docs/architecture.md` D2, D4, D5; nhãn nghiên cứu `[R1 S1–S4]`, `[R2 S7]`.
