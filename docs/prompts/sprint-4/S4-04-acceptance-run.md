# S4-04 · Lượt nghiệm thu đo thật (PENDING, cần tôi duyệt)

Chưa chạy. Mọi việc dưới đây gọi API thật hoặc gửi nội dung diff ra ngoài, nên **chỉ chạy khi tôi nói "OK"** và có `ANTHROPIC_API_KEY`. Code và phép đo offline của S4-04 đã xong; phần này là cái còn thiếu để đóng DoD S4.4.

## Mốc so sánh

- **Tinh chỉnh (sau)**: `HEAD` của `feat/s4-04-pruner-tuning` (núm đóng băng ở `ecb1457`; `055273b` chỉ thêm tốc độ, digest giống hệt).
- **Baseline (trước)**: commit `d26eff6` = pruner **và** định dạng payload trước tinh chỉnh, đã có `--dataset`/`--baseline`. Không dùng riêng `2f4c2dec` (commit cuối chạm `pruner.py`): commit đó chưa có công cụ dataset, và định dạng payload nằm ở `agent.py`.
  ```
  git worktree add ../qc-agent-baseline d26eff6
  ```
- Noteboard còn có baseline lịch sử `eval/selector-real.json` (không ghi đè, không sửa). Nên chạy thêm baseline `d26eff6` cho noteboard để loại ảnh hưởng của S4-03 (prompt caching, tools sắp khoá); tốn khoảng $0,2.

## A. Số token thật (`count_tokens`, gửi nội dung diff ra ngoài, ghi egress)

Mỗi dataset một lệnh (`--baseline` chỉ đi với một dataset; B0 phát lại từ payload đã lưu, không cần mã pruner cũ):

```
python tools/eval_cost.py --dataset noteboard     --split tune    --baseline eval/pruner-baseline/noteboard.json     --llm-tokens count --yes --out-json eval/cost-count/tune-noteboard.json
python tools/eval_cost.py --dataset monorepo-poly --split tune    --baseline eval/pruner-baseline/monorepo-poly.json --llm-tokens count --yes --out-json eval/cost-count/tune-monorepo-poly.json
python tools/eval_cost.py --dataset monorepo-poly --split holdout --baseline eval/pruner-baseline/monorepo-poly.json --llm-tokens count --yes --out-json eval/cost-count/holdout-monorepo-poly.json
python tools/eval_cost.py --dataset node-api      --split tune    --baseline eval/pruner-baseline/node-api.json      --llm-tokens count --yes --out-json eval/cost-count/tune-node-api.json
python tools/eval_cost.py --dataset node-api      --split holdout --baseline eval/pruner-baseline/node-api.json      --llm-tokens count --yes --out-json eval/cost-count/holdout-node-api.json
```

- Số lời gọi `count_tokens`: khoảng 4 mỗi ca (A, B_min, B1, B0; tiền tố P đếm một lần) trên toàn bộ ca của dataset, tổng cỡ 300. **[Assumption, chưa kiểm chứng]** endpoint này không tính phí và chỉ bị giới hạn tần suất; cần đối chiếu trang pricing trước khi chạy. Có một request lớn (ca file 3 000 dòng, A ≈ 48 000 token ước lượng).
- Kết quả cần đọc: từng dataset riêng (median/P90, số ca, % giảm, ca tăng token), **không** gộp. Kiểm sai số `estimate` so với `count_tokens`; nếu lệch nhiều thì trần khả thi cũng phải đo lại bằng `--ceiling --llm-tokens count`.

## B. Recall và precision thật (model thật, 3 lượt, median)

Cần **nhãn đã được duyệt** của `monorepo-poly` và `node-api` (người duyệt thêm `reviewed_by` vào `labels.yaml` rồi đổi `labels_status: reviewed`). Chưa duyệt thì chạy được nhưng số **CHƯA KIỂM CHỨNG** và không được coi là đạt.

Baseline (trong `../qc-agent-baseline`) rồi tinh chỉnh (trong repo này), cùng model/prompt/policy, mỗi cặp ghi file riêng:

```
# trong ../qc-agent-baseline (d26eff6)
python tools/eval_selector.py --llm real --runs 3 --yes --dataset monorepo-poly --out-json <repo>/eval/selector-real-monorepo-poly-baseline.json
python tools/eval_selector.py --llm real --runs 3 --yes --dataset node-api      --out-json <repo>/eval/selector-real-node-api-baseline.json
python tools/eval_selector.py --llm real --runs 3 --yes --dataset noteboard     --out-json <repo>/eval/selector-real-noteboard-baseline.json
# trong repo này (HEAD), thêm --baseline để có chênh lệch theo điểm phần trăm
python tools/eval_selector.py --llm real --runs 3 --yes --dataset monorepo-poly --baseline eval/selector-real-monorepo-poly-baseline.json --out-json eval/selector-real-monorepo-poly-pruned.json
python tools/eval_selector.py --llm real --runs 3 --yes --dataset node-api      --baseline eval/selector-real-node-api-baseline.json      --out-json eval/selector-real-node-api-pruned.json
python tools/eval_selector.py --llm real --runs 3 --yes --dataset noteboard     --baseline eval/selector-real-noteboard-baseline.json     --out-json eval/selector-real-pruned.json
```

- Không ghi đè `eval/selector-real.json`. Mỗi lệnh có trần chi phí `--max-usd` (mặc định 0,6) và tự dừng nếu 3 lời gọi liên tiếp lỗi.
- Số lời gọi mỗi lượt: noteboard 30, monorepo-poly 22, node-api 15 (ca đi đường LLM, kể cả injection); × 3 lượt × 2 phía (baseline, tinh chỉnh) ≈ 400 lời gọi khi chạy cả ba dataset hai phía.
- Ước tính chi phí theo `src/qc_agent/llm/prices.py` (Haiku 4.5, $1/$5 mỗi triệu token, giá cached 2026-09-25, **kiểm lại trang pricing**): tham chiếu baseline noteboard 90 lời gọi = $0,21; monorepo-poly cỡ $0,07 mỗi lượt mỗi phía (có một ca 45 file, request lớn), node-api cỡ $0,04. Tổng cỡ **$1,1**; trần an toàn **$1,5**.
- Đọc kết quả: recall LLM ≥ 90%, precision ≥ 80%, final recall 100% với nhóm core/security (ngưỡng sẵn có), và **chênh lệch theo điểm phần trăm so với baseline của từng dataset** (`vs_baseline.delta_pp`). Mỗi dataset chỉ 13 đến 30 ca nên nhiễu lớn: dùng median 3 lượt và nói rõ cỡ mẫu. Phần holdout báo riêng cạnh tune; số `unverified` phải được nêu.
- Định dạng `user` đã đổi (bỏ trường mặc định, JSON gọn): đây là chỗ có thể ảnh hưởng recall, nên dòng này là quan trọng nhất.

## C. Việc cần tôi quyết sau khi có số

1. Đóng hay không DoD S4.4 "giảm ≥ 40% ở median" (không dataset nào khả thi; xem `docs/usage-ci.md`). Đề xuất đóng theo từng dataset với điều kiện "không tụt recall" và "payload ≤ diff thô", ghi rõ N/A cho mục tiêu 40%.
2. `-U0` (+0,8 đến +1,8 điểm % ước lượng) có nhận hay không, dựa trên recall thật.
3. `_default.yaml`: lockfile/manifest lồng (`apps/web/pnpm-lock.yaml`) và Dockerfile tên khác (`deploy/docker/api.Dockerfile`) không được rules chọn FULL SET; `gt-functional` không có trong policy mặc định nên `pytest` không bao giờ được chọn. Đây là việc riêng, đổi hành vi mọi project.
