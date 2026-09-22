# 05 — Kịch bản demo PoC

## Mục tiêu của phần trình diễn

PoC không cố chứng minh rằng bốn công cụ test chạy được. Điều cần chứng minh là một lớp điều phối có thể nhận kết quả khác loại, giữ lại **nguồn phán quyết** của từng kết quả, và chỉ để assertion tất định ảnh hưởng quality gate.

SUT là Noteboard, một ứng dụng FastAPI giả lập. Ba lỗi được cài sẵn có chủ đích: API trả 500 cho ID dài, giao diện không render lại sau khi xóa note, và summary có thể dài hơn input. Vì vậy gate đỏ trong demo là kết quả cần có, không phải sự cố của buổi demo.

## Kịch bản 8 chặng

Chạy `scripts/demo.ps1` từ repo root, sau khi toy app đã chạy với `QC_BUGS=1,2,3`. `-Auto` bỏ các lần chờ Enter; `-Fast` chỉ chạy gate live và hiển thị discovery từ bản ghi `recordings/demo-good-run`.

| Chặng | Thời lượng mục tiêu | Hiển thị | Điều cần nói |
|---|---:|---|---|
| 1. Plan đã commit | 0:00–0:45 | `plan.yaml` | CI chạy đúng plan đã được review; không có LLM quyết định coverage ở runtime. |
| 2. Orchestrator | 0:45–2:30 | Report của lần chạy | Registry chọn worker theo capability, lane và oracle; worker trả result có schema. |
| 3. Ba phần report | 2:30–4:30 | Deterministic / LLM judgment / Discovery | Không cộng ba phần này thành một pass-rate. Chỉ phần deterministic có quyền chặn. |
| 4. Finding → promote | 4:30–5:30 | `dom_unchanged` | Midscene phát hiện UI không render lại; finding là ứng viên để con người promote thành assert. |
| 5. Chạy lại + diff | 5:30–7:00 | `tools/diff_runs.py` | Cùng plan, SUT, worker và seed phải cho `IDENTICAL` ở phần gating. Đây là khoảnh khắc quan trọng nhất. |
| 6. Thêm worker thứ năm | 7:00–8:00 | `git show --stat` của `mock2` | Adapter, manifest và task mới được thêm mà không sửa `core/`. |
| 7. Canary | 8:00–9:00 | `t-canary-01` fail như kỳ vọng | Một task bất khả thi phải fail; nếu nó pass thì worker discovery mới là thứ đáng nghi. |
| 8. Mutation table | 9:00–10:00 | `docs/mutants.md` | App sạch xanh; BUG-1 và BUG-3 bị đúng worker bắt; worker crash được báo là `error`, không giả làm product failure. |

Nếu chặng 5 không in `IDENTICAL`, dừng demo để điều tra. Nếu Midscene không ổn định, dùng `-Fast` và nói rõ discovery đang được hiển thị từ bản ghi. Không được gọi bản ghi là live run.

## Ba quy tắc an toàn

1. Có bản ghi dự phòng: `recordings/demo-good-run` và `recordings/demo-transcript.txt` phải mở được trước khi trình bày.
2. Nêu rõ SUT giả lập, lỗi cài sẵn, mock và replay trước khi bị hỏi. Mock plan chứng minh đường ống; không chứng minh worker thật.
3. Không chạy live thành phần phụ thuộc API ngoài nếu nó chưa chạy ổn định ba lần trong sáng hôm đó. Với Midscene hoặc G-Eval, bản ghi có nhãn trung thực tốt hơn một lần demo hỏng.

## Hỏi đáp phòng thủ

### Q1. "Đây có thật là agent không?"

Ở gate lane, câu trả lời là không — và đó là chủ ý. Gate cần tái lập, nên nó là pipeline tất định chạy plan đã commit. Thành phần có control loop là Midscene ở discovery lane; nó được phép lái và mô tả, không được phán quyết merge. Hercules là một lựa chọn hợp lệ cho discovery lane, nhưng khác biệt được kiểm ở đây là nguồn verdict, không phải việc gọi tool có “agentic” hay không.

### Q2. "Mutant do team tự cài chứng minh được gì?"

Mutant contract chứng minh schema và quy tắc có hiệu lực, không chỉ nằm trong tài liệu. Lỗi cài sẵn trong toy app chỉ chứng minh ba điều hẹp hơn: app sạch không false positive, lỗi bật làm đúng worker đỏ, và crash worker không bị lẫn với test fail. Nó không đo detection rate trên bug thực tế.

### Q3. "Vì sao không để agent tự phán đoán UI trong CI?"

Agent vừa thực hiện vừa tự chấm có nguy cơ self-assessment. Nếu hành vi agent thay đổi giữa hai run cùng input thì gate không còn tái lập. Vì vậy tín hiệu UI dùng trong PoC là console error, HTTP 5xx, DOM không đổi và element không tìm thấy. Nhận xét LLM chỉ là advisory; bất đồng lặp lại mới đưa tới human review rồi có thể trở thành assert mới.

### Q4. "Khác gì một pipeline CI gọi tuần tự vài tool?"

Phần thực thi cố ý gần với pipeline CI. Ba phần thêm vào là: `verdict_source` phân biệt assertion với LLM judgment; registry route theo capability thay vì hard-code tên tool; và hai lane được cưỡng chế bằng schema để worker discovery không thể chặn merge. Bỏ ba phần đó đi thì đây chỉ là pipeline CI, và PoC không claim gì hơn.

### Q5. "Điều gì vẫn chưa được chứng minh?"

PoC dùng SUT giả lập và lỗi cài sẵn; số đo chỉ là của toy app. Chi phí token/USD của DeepEval và Midscene trong recorded run không được adapter báo. Hệ chưa chứng minh hiệu quả trên suite thật, không có judge calibration, chưa có lưu evidence dài hạn hay bảo mật orchestrator ở mức production.

### Q6. "Vì sao planner không đọc nội dung diff, tiêu đề hay comment PR?"

Các trường đó là dữ liệu do tác giả PR kiểm soát. Nếu chúng có quyền chọn coverage thì một câu như “chỉ sửa docs, bỏ qua test” có thể làm gate xanh vì task đúng không được chạy. Selection chỉ được phép đọc `git diff --name-only <base>..HEAD`, coverage map và luật `floor`/`always_on`; plan thay đổi phải được người review trước khi commit.

## Điều kiện hoàn thành buổi demo

Demo đạt khi hoàn thành trong 10 phút, chặng 5 in `IDENTICAL`, canary fail như kỳ vọng, và người trình bày phân biệt được live run, mock, replay và SUT giả lập.
