# S1-08 · Mutant nghiệp vụ + golden + bộ GT đã duyệt cho noteboard + `eval_groundtruth` (plan S1.10)

- Branch: `feat/s1-08-gt-eval`
- Tiền điều kiện: S1-03 và S1-06 đã merge (worker `pytest` đã có trong image, CLI `gt` đã có).

## Đọc trước

- `docs/prompts/_common.md`; plan §2 (bảng "Đạt 90%"), S1.10, DoD Sprint 1
- `tests/fixtures/sut/noteboard/toyapp/app.py` (docstring `QC_BUGS`, tập `BUGS`), `summarizer.py`, `tests/test_toyapp.py`
- `tests/fixtures/prd/noteboard-prd.md` (S1-02); `configs/projects/noteboard.yaml`; `.github/workflows/ci.yml`, job `image-e2e`
- `tests/test_shipped_projects.py`, và mọi test đang chạy `--project noteboard --mode pr` (grep ra)

## Mục tiêu

Có số đo khách quan cho "đạt 90%" của Sprint 1, và có một bộ GT **đã duyệt** thật sự chặn merge trên SUT tham chiếu.

## Việc cần làm

1. **Mutant `BUG-4…BUG-13`** trong `toyapp/app.py`, bật qua `QC_BUGS`, ghi vào bảng trong docstring đầu file.
   - Mỗi mutant vi phạm **ít nhất một AC cụ thể** của `noteboard-prd.md`, và ghi AC đó trong docstring.
   - Mỗi mutant là lỗi **nghiệp vụ**, suite `api-contract` (Schemathesis: không 5xx, đúng schema) **không bắt được**. Kiểm bằng cách chạy `api-contract` với `QC_BUGS=<k>`: vẫn phải xanh. Mutant nào bắt buộc phải vi phạm schema thì ghi rõ lý do.
   - Mặc định vẫn **tắt**. Giá trị mặc định `"1,2,3"` giữ nguyên để CI hiện có không đổi hành vi (`none` → sạch, `1,3` → lỗi).
   - Gợi ý dạng mutant (chọn theo code thật): nhận title rỗng; trả sai mã 201/204; xoá xong vẫn GET được; xoá note không tồn tại trả 204; list thiếu hoặc lặp phần tử; cắt ngắn body; summarize note không tồn tại trả 200; trả nhầm note khác; không trim/chuẩn hoá theo AC.
   - `tests/test_toyapp.py`: mỗi mutant có một test chứng minh hành vi sai chỉ xuất hiện khi bật đúng cờ của nó.
2. **Golden `tests/fixtures/prd/noteboard-golden.yaml`**:
   ```yaml
   labeled_by: "dev — CẦN QA DUYỆT"
   acs:
     AC-1.1: {testable: true, kills: [BUG-4]}
   mutants:
     BUG-4: {acs: [AC-1.1], description: "…"}
   ```
   Plan §3.4 yêu cầu nhãn này do QA gán. Nếu tôi chưa đưa bản QA thì giữ `labeled_by` như trên và nêu trong báo cáo.
3. **Bộ GT đã duyệt** trong `tests/fixtures/sut/noteboard/.qc-agent/ground-truth/`:
   - Sinh bằng `qc-agent gt generate` với `FakeAnthropic` và fixture của S1-04.
   - Rồi **đóng vai QA**, ghi rõ trong báo cáo đó là vai giả lập:
     - duyệt từng TC: `approved`, hoặc `rejected` kèm lý do;
     - thêm ≥ 3 TC `origin: qa` (biên độ dài, unicode, xoá hai lần…);
     - đặt `status: approved` cho catalog và module-map.
   - Kết quả phải thoả `qc-agent gt validate` exit 0.
4. **Bật gate GT cho noteboard**: `.qc-agent/suites/gt-functional.yaml` (render ở S1-05), và `configs/projects/noteboard.yaml` → `modes.pr.blocking_suites` thêm `gt-functional`.
   - Danh sách trong policy **thay thế** chứ không cộng dồn, nên phải giữ đủ các suite cũ.
   - Không đụng `_default.yaml`, vì repo chưa có suite này sẽ lỗi.
   - Chạy lại toàn bộ `pytest -q`. Test nào giả định bộ blocking cũ thì sửa bằng cách giới hạn `--suites` trong chính test đó, **không** nới lỏng policy.
   - Kiểm tay: toyapp sạch → `qc-agent run --project noteboard --mode pr --sut-root tests/fixtures/sut/noteboard` exit 0; `QC_BUGS=<k>` với k = 4…13 → exit 1.
   - Có Docker thì kiểm cả `image-e2e`: sạch = 0, `1,3` = 1. Không có Docker thì ghi rủi ro vào báo cáo.
5. **`tools/eval_groundtruth.py`**
   - `--prd --golden --sut-root --llm fake|real --runs N --out-json --yes`
   - Metric:
     - (a) **AC coverage** = số AC `testable` trong golden có ≥ 1 TC / tổng số AC `testable`;
     - (b) **tỉ lệ TC chạy xanh trên toyapp sạch**: chạy mọi TC *vừa sinh* bằng cách ép `approved` trong một bản sao tạm, không đụng file thật;
     - (c) **tỉ lệ bắt mutant** của bộ **đã duyệt**, với từng `QC_BUGS=k` (k = 4…13), và riêng `BUG-1`.
   - Toyapp chạy bằng subprocess uvicorn trên cổng trống. Dùng `none` chứ không dùng chuỗi rỗng: PowerShell coi env rỗng là xoá biến.
   - Xuất JSON và bảng Markdown. Exit 0 khi đạt ngưỡng (≥ 90%, ≥ 90%, ≥ 9/10 và bắt được BUG-1), exit 1 khi không đạt.
   - `--llm real` cần `ANTHROPIC_API_KEY` **và** `--yes`. Trước khi gọi phải in ước tính chi phí: token ước lượng × giá Sonnet 5 $2/$10 mỗi MTok. **HỎI TÔI trước khi chạy chế độ real.**
   - Unit test cho phần tính metric, với input tổng hợp: `tests/test_eval_groundtruth.py`.

## Nghiệm thu bước này

`python tools/eval_groundtruth.py --llm fake ...` chạy hết và in đủ ba metric. Bộ đã duyệt bắt ≥ 9/10 mutant và bắt được `BUG-1` (đo trên máy local). Lượt đo `real` (median 3 lần) để dành cho phiên `dod-verify`, khi đã có key và tôi đồng ý.

## Ngoài phạm vi

Cache (S4-02), đổi prompt để cải thiện metric. Nếu metric fake thấp thì báo lại, đừng sửa prompt ở bước này.
