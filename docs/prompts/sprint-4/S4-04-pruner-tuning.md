# S4-04 · `eval_cost` + tinh chỉnh pruner mà không tụt recall (plan S4.4)

- Branch: `feat/s4-04-pruner-tuning`
- Tiền điều kiện: S4-03 đã merge (có `count_tokens`), cùng golden set của S2-07.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.4, DoD S4 (token đầu vào của Diff Agent giảm ≥ 40% ở median so với git diff thô, recall S2 vẫn ≥ 90%)
- `src/qc_agent/selector/pruner.py` (các núm chỉnh: trần mỗi file, trần tổng, số dòng ngữ cảnh `-U`, bảng comment, danh sách generated/vendor)
- `tools/eval_selector.py`, `tests/fixtures/diffs/`

## Mục tiêu

Đo được mức tiết kiệm token của pruner, và chỉnh các núm cho tới khi median ≥ 40%. Chứng minh recall của Diff Agent không tụt.

## Việc cần làm

1. **`tools/eval_cost.py --llm-tokens count|estimate --out-json`**
   - Với mỗi diff trong golden set (dựng repo tạm giống `eval_selector`), so hai payload:
     - payload A: `git diff <merge-base> <head>` thô, đặt vào đúng khung prompt của Diff Agent;
     - payload B: payload đã prune.
   - Đếm token của **toàn bộ request** Diff Agent (system + tools + user). Tỉ lệ giảm = 1 − B/A. Báo cả median, P90, và kết quả theo nhóm.
   - `--llm-tokens count`: đếm bằng `count_tokens`. Chính xác, nhưng cần key, gửi nội dung ra ngoài (có ghi egress), và cần **HỎI TÔI** trước khi chạy.
   - `--llm-tokens estimate`: đếm ký tự / 4. Chạy offline, kết quả ghi rõ là *ước lượng*.
   - Exit 0 khi median ≥ 40%.
2. **Tinh chỉnh `pruner.py`**
   - Mỗi thay đổi đi kèm số đo trước/sau trong một bảng ở thân PR.
   - Không được bỏ file khỏi danh sách: danh sách file luôn đầy đủ.
   - Không được bỏ hunk code thật chỉ để đạt số.
   - Các núm gợi ý: `-U0`/`-U1`; chỉ lấy dòng `+` cho file mới hoàn toàn; gộp khoảng trắng; nhận diện thêm file generated; trần mỗi file và trần tổng.
3. **Kiểm recall**
   - `python tools/eval_selector.py --llm fake` phải xanh.
   - Lượt `--llm real --runs 3` (median) cần **HỎI TÔI**: báo ước tính chi phí trước. Recall LLM ≥ 90%, precision ≥ 80%, final recall 100% với nhóm core/security.
4. **Ghi lại tham số đã chốt** trong docstring của `pruner.py` và trong `docs/usage-ci.md` (mục chi phí).

## Test bắt buộc

- Unit test của `pruner` xanh, và thêm test cho mọi núm mới.
- `tests/test_eval_cost.py`: công thức tính tỉ lệ và median trên dữ liệu tổng hợp; chế độ `estimate` chạy được mà không cần mạng.

## Ngoài phạm vi

Đổi prompt `diff_select.md`. Nếu thật sự cần đổi thì tăng `prompt_version`, báo lại, và việc đó sẽ làm mất cache của S4-02.
