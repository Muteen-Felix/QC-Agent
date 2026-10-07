# S4-04 · `eval_cost` + tinh chỉnh pruner mà không tụt recall, đo trên nhiều kiểu repo (plan S4.4)

- Branch: `feat/s4-04-pruner-tuning`
- Tiền điều kiện: **tách từ commit S4-03 đã hoàn tất, không chờ S4-03 vào `main`** (ngoại lệ có chủ ý so với `_common.md` mục 4.2 "tạo branch từ `main`").
  - Điểm tách là `e0aebd6` (`fix(llm): Gemini thử lại sau lỗi mạng rồi thành công không được làm mất unknown_calls`), commit sửa trên `117a528` (S4-03 gốc). Nhánh được tạo bằng `git switch -c feat/s4-04-pruner-tuning feat/s4-03-prompt-cache-budget` khi cây sạch.
  - Kiểm trước khi làm: `git merge-base --is-ancestor e0aebd6 HEAD` thoát 0; `llm/client.py` có `estimate_input_tokens`, `count_tokens` và `ToolCall.unknown_calls`; có `src/qc_agent/llm/prices.py`; `schemas/selection.json` có `token_cap` và `llm.unknown_calls`. Thiếu thì dừng và báo.
  - Commit đầu tiên của nhánh S4-04 là chính prompt này (docs). Nếu nhánh S4-03 nhận thêm commit sửa sau review thì rebase S4-04 lên đầu nhánh mới và cập nhật SHA ở đây. Khi được phép mở PR: base là nhánh S4-03 nếu nó chưa vào `main`.
- Golden set S2-07 (`tests/fixtures/diffs/`, `labels.yaml`) và `eval/selector-real.json` giữ nguyên làm baseline lịch sử.

## Giới hạn của giai đoạn này (quyết định của tôi, không cần hỏi lại)

- **Không** gọi LLM thật, **không** gọi `count_tokens` thật, **không** push, **không** chạy trên SUT sản phẩm (VAHAN hay repo khách) hay GitHub thật.
- S4-04 được làm xong code, dataset, test và mọi phép đo offline. Recall bằng model thật và số token đếm thật là **PENDING**, để dành cho một lượt nghiệm thu riêng do tôi duyệt sau.
- **Không tick DoD** (`docs/implementation-plan.md` S4.4, dòng DoD S4 về token/recall) dựa trên ước lượng hay fake LLM. Kết thúc phiên, S4.4 vẫn là `[ ]`.

## Quyết định đã chốt sau cổng bước 0 (2026-10-07, phương án A)

- Cổng đã chạy (`eval_cost --ceiling`, `estimate`, commit `101378a`): trần chặt `1 − B_min/A` của noteboard là **2,5%** ở median (P90 2,6%) trên 20 ca LLM; trần lỏng 7,3%; đã đạt **−5,0%** (pruner hiện tại làm request to hơn diff thô ở cả 20 ca). Phần diff chỉ chiếm 7,3% request (tiền tố ≈ 907 token ước lượng), nên **mục tiêu 40% không khả thi trên golden set noteboard**; chỉ-phần-diff cũng chỉ 32,4% nên đổi định nghĩa DoD không cứu được.
- Quyết định: **mục tiêu giảm token được chốt THEO TỪNG DATASET**. Noteboard: không có mục tiêu 40% (`cost_target: null`, kèm số đo trên làm lý do); vẫn báo số đo, vẫn bắt buộc không tụt recall. Dataset mới: dựng với diff đủ lớn (đúng các ca "diff lớn", "code + generated lớn" ở mục 2.1) rồi chạy cổng trần chặt cho từng dataset; dataset nào trần < 40% thì ghi `cost_target: null` + số đo + lý do và **tiếp tục** (không dừng nữa), dataset nào ≥ 40% thì mục tiêu là 40% ở median.
- Phương án B (đổi DoD sang "phần diff") bị loại. Phương án C (giảm tiền tố, đổi cách dựng `system`/`diff_select.md`) vẫn **ngoài phạm vi**, cần tôi duyệt riêng.
- DoD S4.4 "giảm ≥ 40% ở median" **không tick** dựa trên noteboard; điều kiện đóng của nó sẽ do tôi quyết ở lượt nghiệm thu, dựa trên số từng dataset.
- Việc tinh chỉnh `pruner.py` vẫn bị rào chắn như cũ (không bỏ hunk code thật để giảm token). Có một núm hợp lệ đã thấy từ số đo: khung JSON của mỗi file nặng hơn `-U3` thô ở diff nhỏ (B0 > A); thu gọn khung đó chỉ là chỉnh cách biểu diễn, không bỏ file hay hunk.

## Đọc trước

- `docs/prompts/_common.md`; plan S4.4, DoD S4 (token đầu vào của Diff Agent giảm ≥ 40% ở median so với git diff thô; recall S2 vẫn ≥ 90%)
- `src/qc_agent/selector/pruner.py`: các núm chỉnh là trần mỗi file (`per_file_tokens`), trần tổng (`total_tokens`), `-U1`, `-w`, bảng `COMMENT`, `LOCKFILES`, nhận diện vendor/generated trong `_kind`. Lưu ý: pruner tự đo trần bằng **ký tự / 4** (`_cap`, `approx_tokens`), khác đơn vị của S4-03 (xem mục "Hai loại bằng chứng token").
- `src/qc_agent/selector/agent.py: select`: request đầy đủ = `system` (prompt + MODULE MAP + WORKERS + CAPABILITIES) + tool (`select_workers`, mô tả, schema enum) + `user` (`<untrusted_diff>` bọc JSON danh sách file đã prune). Trần `token_cap` ước lượng trên đúng ba phần này.
- `src/qc_agent/selector/rules.py`, `configs/projects/_default.yaml` (`full_set_paths`, `docs_paths`, `floor_workers`), `core/project.py: resolve_project` (slug chưa đăng ký thì dùng `_default.yaml` thuần).
- `tools/eval_selector.py`: công thức đã sửa 2026-10-06 (tách FULL SET, median theo lượt). Hiện **cố định** `SUT = tests/fixtures/sut/noteboard`, `FIXTURES = tests/fixtures/diffs` và `load_project("noteboard", ...)`.
- `tests/test_selector_pruner.py`, `tests/test_selector_golden.py`.
- `eval/selector-real.json`: **baseline** đo bằng Haiku 4.5 thật, 3 lượt, `prompt_version: diff-select/1`:
  - recall LLM 97,1% (cả 3 lượt), precision 91,7% (từng lượt 89,2% / 91,7% / 94,3%), final recall 100%, critical recall 100%, P95 3,4 s, `rules_full_set` 30/30, injection 30/30;
  - `input_tokens` thật của 90 lời gọi: median 1 652, min 1 635, max 2 137.

## Sự thật cần biết trước khi đặt mục tiêu

- **[Fact]** Trên noteboard, `input_tokens` thật nhỏ nhất là 1 635 trong khi diff của golden set đều nhỏ. Tức là phần tiền tố cố định (system + module-map + catalog + capabilities + tool schema) chiếm phần lớn request. Tỉ lệ giảm của toàn bộ request bị chặn trên bởi `1 − P/A` (P = tiền tố + khung `user` rỗng, A = request với diff thô), nên **40% ở median có thể không khả thi trên noteboard** dù pruner tốt đến đâu. Số đo năm 2026-10-06 là trước S4-03. **Việc đầu tiên của phiên là đo lại trần này bằng `estimate` (mục 0), trước khi dựng dataset hay đụng `pruner.py`.**
- **[Fact]** Glob trong `_default.yaml` hiện chỉ khớp lockfile/manifest **ở gốc repo**: `package-lock.json`, `pnpm-lock.yaml`, `requirements*.txt`, `go.mod`, `package.json` không khớp `apps/web/package-lock.json` hay `services/api/package.json`. `**/Dockerfile*` khớp `apps/api/Dockerfile` nhưng **không** khớp `deploy/docker/api.Dockerfile`. Với monorepo, các ca này hiện đi đường LLM (pruner đánh dấu `kind: lockfile`, không có hunk) thay vì FULL SET. Đây là loại giả định ngầm dataset mới phải làm lộ ra (xem HỎI TRƯỚC).

## Mục tiêu

1. Đo được mức tiết kiệm token của pruner **trên toàn bộ request Diff Agent**, riêng cho từng dataset, và tinh chỉnh pruner trong giới hạn rào chắn.
2. Có dataset local thứ hai và thứ ba, khác noteboard về cấu trúc, để phát hiện giả định ngầm của pruner/rules. Đây là fixture tổng hợp, không phải SUT sản phẩm.
3. Chuẩn bị sẵn đường đo chất lượng (recall/precision) riêng cho từng dataset. Số thật cần LLM thật nên PENDING; giai đoạn này chỉ chứng minh đường ống chạy.

## Việc cần làm

Thứ tự có chủ ý: đo trần khả thi trước, rồi mới tốn công dựng dataset và tinh chỉnh; có một commit "trước tinh chỉnh" dùng lại được khi đo thật.

0. **Cổng trần khả thi trên noteboard (mục 0)**: tách hàm dựng request, `eval_cost.py --ceiling`, chạy offline. Nếu trần median < 40% thì **DỪNG** (xem HỎI TRƯỚC), chưa làm bước 1.
1. **Commit A (công cụ + dataset, `pruner.py` không đổi)**: mục 1, 2, 3 dưới đây. Ghi SHA của commit này làm `baseline_pruner_sha`.
2. **Cổng trần khả thi cho từng dataset mới**: ngay khi mỗi dataset có manifest và nhãn, chạy `eval_cost --ceiling --dataset <tên>`; dataset nào có trần median < 40% thì ghi `cost_target: null` kèm số đo và lý do rồi tiếp tục (đã chốt phương án A, xem "Quyết định đã chốt"); dataset ≥ 40% thì mục tiêu là 40% ở median.
3. Chạy `eval_cost --llm-tokens estimate` trên mọi dataset, lưu kết quả **trước tinh chỉnh**.
4. **Các commit tinh chỉnh**: mục 4, mỗi núm một commit, kèm số đo trước/sau.
5. Đóng băng núm, rồi mới chạy phần holdout (mục 2.3).

### 0. Đo trần khả thi trước (đầu phiên, offline, trước khi dựng dataset)

Câu hỏi: ngay cả khi pruner giữ đúng rào chắn và làm tốt nhất có thể, giảm 40% ở median trên **toàn bộ request** có còn khả thi không?

- **Commit 0a: tách phần dựng request** của `agent.select` (system = prompt + MODULE MAP + WORKERS + CAPABILITIES, tool, `user`) thành một hàm thuần dùng chung cho `select` và `eval_cost`. Test chứng minh request của `select` giống từng byte trước và sau khi tách (tool, system, user). Không đổi hành vi, không đổi `prompt_version`.
- **Commit 0b: `tools/eval_cost.py --ceiling`** (bản tối thiểu, chỉ noteboard, đúng golden set 30 diff sạch, chưa cần manifest; sau này mục 3 mở rộng cùng mã này, không viết lại). Với mỗi ca đi đường LLM (loại FULL SET, chỉ-floor, `token_cap`, đúng như `eval_selector`), dựng cùng một request cho:
  - **A**: diff thô (`git diff <merge-base> <head>`, mặc định của git) trong khung `<untrusted_diff>`;
  - **P**: tiền tố + khung `user` với danh sách file rỗng;
  - **B_min**: request mà danh sách file **đầy đủ** (đúng các trường của `PrunedFile`) nhưng không có hunk nào. Đây là payload nhỏ nhất hợp lệ theo rào chắn "danh sách file luôn đầy đủ", nên mọi pruner hợp lệ đều có B ≥ B_min;
  - **B0**: payload của pruner hiện tại, để so mức đã đạt với trần.
- Báo, cho noteboard: median và P90 của `1 − P/A` (trần lỏng), của `1 − B_min/A` (**trần chặt, dùng để quyết định**) và của `1 − B0/A` (đã đạt); `A`, `P`, `B_min` ở median; số ca có `B_min ≥ A` hoặc `B0 > A`. Đơn vị là `estimate_input_tokens` (ước lượng gần đúng, xem mục "Hai loại bằng chứng token"); in chữ "ước lượng".
- Đối chiếu với số thật đã có: `input_tokens` thật của baseline noteboard (median 1 652, min 1 635) so với B0 ước lượng cùng ca. Nêu sai lệch, không dùng để chỉnh hệ số.
- **Cổng**: trần chặt ở median ≥ 40% thì làm tiếp. Dưới 40% thì **dừng**: ghi số đo vào báo cáo, **không** dựng dataset, **không** chỉnh `pruner.py`, **không** cắt hunk, chờ tôi chốt mục tiêu (xem HỎI TRƯỚC). Trần ≥ 40% chỉ là điều kiện cần: báo kèm biên (trần trừ 40 điểm %) để biết còn bao nhiêu chỗ chỉnh, và không coi đã đạt.
- Kết quả bước này là số đo `estimate`, không phải `count_tokens`; không đủ để tick DoD.

### 1. Dataset và manifest dùng chung cho `eval_selector.py` và `eval_cost.py`

- Thiết kế **một** cách chọn dataset bằng manifest, một bộ nạp dùng chung cho cả hai công cụ (không chép logic dựng repo tạm sang công cụ thứ hai). Trình bày thiết kế (≤ 15 dòng) trước khi code. Gợi ý, không bắt buộc:
  ```
  tests/fixtures/selector-datasets/<dataset>/manifest.yaml
    name, sut: <đường dẫn SUT>, patches: <thư mục .patch>, labels: <labels.yaml>,
    project: {slug, projects_dir}, splits: {tune: [...], holdout: [...]}, labels_status: reviewed|unreviewed
  ```
- **Noteboard**: manifest chỉ *trỏ* vào `tests/fixtures/sut/noteboard`, `tests/fixtures/diffs`, `labels.yaml` hiện có. Không di chuyển, đổi tên hay sửa các file đó (kể cả không thêm trường `split` vào `labels.yaml`: split của noteboard khai trong manifest). Cả 30 diff + 10 injection của noteboard là tập **tune lịch sử** (pruner S2 đã được phát triển trên chúng).
- **Lệnh mặc định không đổi**: `python tools/eval_selector.py --llm fake` (không cờ) chạy đúng golden set noteboard, cùng tập diff, cùng công thức, cùng khoá trong JSON đầu ra (chỉ được thêm khoá như `dataset`). Test hiện có trong `test_selector_golden.py` phải xanh không sửa kỳ vọng.
- Cờ mới, ví dụ `--dataset noteboard|monorepo-poly|node-api|all`. `all` báo **từng dataset riêng**.
- Policy của dataset mới: dùng slug **chưa đăng ký** với `configs/projects/` để nhận đúng `_default.yaml` thật, như một repo vừa onboard. Không thêm project giả vào `configs/projects/`, không chép rồi sửa policy để số đẹp hơn.
- **Không** hard-code tên dataset, tên repo hay thư mục riêng của fixture (`toyapp/`, `apps/`, `noteboard`, …) vào `pruner.py`, `rules.py` hay `agent.py`. Tín hiệu generated/vendor phải là quy ước chung có tài liệu (header `@generated`/`DO NOT EDIT`, `linguist-generated` trong `.gitattributes`, tên thư mục chuẩn như `vendor/`, `node_modules/`, `dist/`). Thêm test: `grep -n "noteboard\|toyapp\|monorepo-poly\|node-api" src/qc_agent/selector/` rỗng.

### 2. Hai dataset tổng hợp mới

Cả hai là fixture local trong `tests/fixtures/selector-datasets/`, mỗi cái có SUT nhỏ với `.qc-agent/suites/` và `.qc-agent/ground-truth/module-map.yaml` của riêng nó (status `approved`, ánh xạ tới suite có thật trong fixture). Không chép mã từ SUT sản phẩm (VAHAN hay repo khách). Không gọi mạng khi dựng.

- **`monorepo-poly`**: `apps/api/` (Python), `apps/web/` (TypeScript/React), `packages/shared/`; Dockerfile sâu (`apps/api/Dockerfile`) và Dockerfile đặt tên khác (`deploy/docker/api.Dockerfile`); file generated có header (`apps/web/src/__generated__/graphql.ts`, `apps/api/gen/*_pb2.py`) và generated **không** header; `vendor/` hoặc `third_party/`; lockfile ở gốc **và** lockfile lồng (`apps/web/pnpm-lock.yaml`).
- **`node-api`**: API Node/TypeScript (Express hoặc Fastify), tên thư mục/file khác noteboard: `src/routes/*.ts`, `src/middleware/auth.ts`, `src/db/migrations/*.sql`, `openapi.yaml`, `test/*.spec.ts`, `package.json` + `package-lock.json` ở gốc.

#### 2.1 Ca bắt buộc (mỗi dataset ≥ 15 ca, trong đó ≥ 10 ca đi đường LLM)

Cột "Đường đi" là kết quả của `rules.decide` với `_default.yaml` **hiện tại**, suy từ glob trong code. Đây không phải nhãn kỳ vọng: nhãn do người duyệt đặt (mục 2.2). Nếu nhãn nói FULL SET mà rules không chọn FULL SET thì `rules_full_set` sai và phải hiện trong báo cáo.

| Ca | `monorepo-poly` | `node-api` | Đường đi theo rules hiện tại |
|---|---|---|---|
| Thêm file code (route/component mới) | `apps/api/routes/orders.py` | `src/routes/orders.ts` | LLM |
| Sửa handler | `apps/api/...` | `src/routes/*.ts` | LLM |
| Xoá file | `apps/web/src/legacy/*.tsx` | `src/routes/legacy.ts` | LLM |
| Đổi tên thuần (100%) và đổi tên + sửa | `packages/shared/*` | `src/lib/*` | LLM |
| Diff lớn (≥ 40 file hoặc 1 file ≥ 3 000 dòng, patch ≤ ~200 KB) | refactor `apps/web` | đổi tên hàng loạt `src/**` | LLM (kiểm trần pruner; ghi nếu chạm `token_cap`) |
| Chỉ docs | `docs/**`, `apps/web/README.md` | `docs/**`, `README.md` | chỉ floor (`docs_only`) |
| Lockfile ở gốc | `pnpm-lock.yaml` | `package-lock.json` | FULL SET |
| Lockfile/manifest lồng | `apps/web/pnpm-lock.yaml`, `apps/web/package.json` | (không có) | **LLM** (glob chỉ khớp ở gốc) |
| Dockerfile sâu | `apps/api/Dockerfile` | `Dockerfile` | FULL SET |
| Dockerfile đặt tên khác | `deploy/docker/api.Dockerfile` | (không có) | **LLM** (không khớp `**/Dockerfile*`) |
| Code bảo mật (`core_or_security: true`) | kiểm token/JWT, dựng câu SQL | `src/middleware/auth.ts`, migration quyền | LLM; floor `gitleaks`/`semgrep` vẫn chạy |
| Chỉ generated/vendor | `__generated__/*`, `vendor/**` | `dist/**` | LLM (chỉ danh sách file, không hunk) |
| Code + generated lớn | sửa `.proto` + `_pb2.py` sinh lại | sửa `openapi.yaml` + client sinh | LLM |
| Unicode | chuỗi tiếng Việt, emoji, CJK trong i18n và trong code | thông báo lỗi tiếng Việt | LLM |
| Workflow / `.qc-agent/**` | `.github/workflows/ci.yml` | `.qc-agent/suites/*` | FULL SET |

Khuyến nghị thêm 2–3 ca injection có cặp `twin` cho mỗi dataset, chấm bằng đúng công thức injection hiện có.

Ca cực lớn (hàng nghìn file, nhiều MB) **không** commit thành patch: dựng trong test bằng code trên `tmp_path`.

#### 2.2 Nhãn: nguồn và người duyệt

- Mỗi ca có `expect_workers`, `core_or_security`, `source` (căn cứ: tài liệu module-map của fixture, mô tả ca, quy ước FULL SET trong `_default.yaml`, …) và mỗi file nhãn có `labeled_by` + `reviewed_by`. Người duyệt khác người viết nhãn; agent được soạn nháp nhãn nhưng không được tự đặt `reviewed_by`.
- **Không** lấy output của Selector (fake hay thật) làm đáp án. Nhãn phải được commit **trước** lần chạy eval đầu tiên trên dataset đó (kiểm được bằng `git log`).
- Không đổi nhãn noteboard, không ghi đè `eval/selector-real.json`.
- Chưa có `reviewed_by`: manifest đặt `labels_status: unreviewed`. Vẫn được dùng cho test kỹ thuật và đo token offline, nhưng mọi số recall/precision/`rules_full_set` của dataset đó in nhãn **CHƯA KIỂM CHỨNG** và không được coi là đạt.

#### 2.3 Tách tune / holdout

- Dataset mới: khoảng 2/3 ca đi LLM để **tune**, còn lại là **holdout**, chia trước khi đo (khai trong manifest, có lý do ngắn), mỗi nhóm ở mục 2.1 có mặt trong tune.
- Chỉ dùng ca tune khi chỉnh núm. Holdout chỉ chạy **một lần** sau khi đóng băng núm (ghi SHA đóng băng), báo riêng cạnh số tune. Nếu sau đó còn chỉnh núm thì phải nói rõ holdout đã bị "nhìn thấy".

### 3. `tools/eval_cost.py --dataset … --llm-tokens estimate|count --out-json`

- Mở rộng bản `--ceiling` của mục 0 (cùng mã, không viết lại) để dùng chung bộ nạp dataset ở mục 1. Mặc định `--dataset noteboard`.
- Với mỗi ca, dựng **cùng một request** như `agent.select` cho ba payload, cùng model, prompt, `prompt_version`, policy, module-map và catalog:
  - **A**: `git diff <merge-base> <head>` thô (mặc định của git, `-U3`, không `-w`) đặt vào đúng khung `<untrusted_diff>`;
  - **B0**: payload của pruner tại `baseline_pruner_sha` (lưu từ bước 3 của thứ tự làm việc, không tính lại bằng code đã chỉnh);
  - **B1**: payload của pruner sau tinh chỉnh.
- Khung prompt lấy từ hàm dựng request chung đã tách ở commit 0a, để A/B0/B1 không lệch khung với `select`.
- Đếm token của **toàn bộ request** (system + tool + user). Ghi kèm, để giải thích, kích thước tiền tố P và trần lý thuyết `1 − P/A` của từng ca; số phụ "chỉ phần diff" được phép in nhưng **không** thay số chính.
- **Báo riêng từng dataset**:
  - số ca, median và P90 của token A/B0/B1, median và P90 của tỉ lệ giảm `1 − B1/A`, chênh lệch so với `1 − B0/A`;
  - **danh sách ca tăng token**: B1 > B0, và mọi ca B > A (JSON escape có thể làm payload đã prune to hơn diff thô với diff nhỏ);
  - **dòng riêng** cho ca FULL SET / chỉ-floor (0 lời gọi LLM) và ca `token_cap` (cũng 0 lời gọi, ghi rõ bị chặn ở A hay B). Các ca này **không** vào median;
  - số ca có `truncated` hoặc `dropped_hunks > 0` ở B0 và B1.
- Được in thêm số tổng hợp mọi dataset, nhưng phải in sau số từng dataset, và kết luận đạt/không đạt xét **từng** dataset.
- Exit: 0 khi mọi dataset được chọn **có `cost_target`** đạt median giảm ≥ mục tiêu trên ca đi LLM (dataset `cost_target: null` chỉ báo số, không quyết exit); 1 khi có dataset không đạt (in tên); 3 khi lỗi dataset/hệ thống. Exit 0 ở chế độ `estimate` **không** đủ để tick DoD.
- `--llm-tokens estimate`: chạy offline, gọi đúng `client.estimate_input_tokens` (xem mục dưới). JSON ghi `token_evidence: "estimate"`.
- `--llm-tokens count`: gọi `client.count_tokens` (chỉ model Claude; `gemini-*` báo lỗi rõ). Viết code và test bằng `httpx.MockTransport`/fake server; **không chạy với API thật** ở giai đoạn này. JSON ghi `token_evidence: "count_tokens"`.
- JSON đầu ra ghi: dataset, `labels_status`, split, model, `prompt_version`, SHA của policy/module-map, `baseline_pruner_sha`, SHA hiện tại, tham số pruner.

### Hai loại bằng chứng token (sửa thông tin cũ của prompt này)

- S4-03 đã chốt `estimate_input_tokens = ceil(số byte UTF-8 / 3)`. Đây là **ước lượng gần đúng, không phải trần bảo thủ tuyệt đối**: với ASCII bằng ký tự/3, với tiếng Việt có dấu/emoji/CJK cho số lớn hơn, và vẫn có thể thấp hơn thực tế ở ký tự hiếm. Không còn công thức "ký tự / 3" hay "ký tự / 4" cho `eval_cost`: dùng đúng hàm này, không tạo hệ số thứ hai.
- **`estimate`**: offline, không gửi gì ra ngoài, tất định. Chỉ là số đo gần đúng; mọi bảng/JSON dùng nó ghi chữ "ước lượng". Tỉ lệ `1 − B/A` theo byte có thể lệch tỉ lệ theo token thật khi A và B khác nhau về tỉ lệ ký tự nhiều byte (ca Unicode).
- **`count_tokens`**: số token thật của model Claude, nhưng **gửi toàn bộ request ra ngoài** (có ghi egress trước khi gửi). PENDING ở giai đoạn này.
- Có thể đối chiếu sai số của `estimate` với `input_tokens` thật **đã có** trong `eval/selector-real.json` (B0 của noteboard, `diff-select/1`, đo trước S4-03), không cần gọi API mới. Ghi rõ đây là số lịch sử.
- Pruner hiện đo trần nội bộ bằng ký tự/4. Đổi sang đơn vị của S4-03 là một núm tinh chỉnh như mọi núm khác (có số trước/sau), không phải "sửa cho khớp" bắt buộc. Docstring của `pruner.py` không được gọi ký tự/4 là số token thật.

### 4. Tinh chỉnh `pruner.py`

- Mỗi thay đổi một commit, kèm bảng trước/sau **từng dataset** (median, P90, ca tăng token, số ca bị cắt). Bảng nằm trong báo cáo cuối phiên; khi được phép mở PR thì chép vào thân PR.
- Chỉ chỉnh dựa trên ca **tune**.
- Núm gợi ý: `-U0`/`-U1`; chỉ lấy dòng `+` cho file mới hoàn toàn; gộp khoảng trắng; nhận diện thêm generated theo quy ước chung; trần mỗi file và trần tổng; đơn vị trần.
- **Rào chắn** (vi phạm là trả lại, dù đạt 40%):
  - danh sách file thay đổi luôn đầy đủ, kể cả lockfile, binary, generated, vendor, file xoá và đổi tên (giữ `old_path`);
  - không bỏ hunk code thật chỉ để giảm token: không được hạ trần tới mức một ca tune/holdout trước đây không bị cắt nay bị cắt, trừ khi tôi duyệt; mọi nội dung bị cắt phải hiện qua `truncated`/`dropped_hunks`;
  - floor security (`gitleaks`, `semgrep`) luôn chạy; `core/` vẫn gộp floor lần hai;
  - LLM lỗi vẫn FULL SET; `token_cap` vẫn FULL SET với 0 lời gọi;
  - giữ `core.quotepath=off`, `safe.directory=*` chỉ cho lệnh đọc, và digest tất định.
- Nếu tiền tố cố định khiến 40% không khả thi ở một dataset hay nhóm diff: báo số đo, `1 − P/A` và lý do, rồi dừng theo mục HỎI TRƯỚC. **Không cắt code để ép đạt chỉ tiêu.**

### 5. Kiểm chất lượng (offline ở giai đoạn này)

- `python tools/eval_selector.py --llm fake` (mặc định noteboard) xanh như trước; `--dataset all --llm fake` chạy được trên ba dataset.
- `eval_selector` báo **riêng từng dataset**: recall LLM, precision LLM, final recall sau rules/floor, critical recall, `rules_full_set` (đúng/tổng), số ca fallback theo lý do, và chênh lệch **theo điểm phần trăm** so với baseline trước tinh chỉnh của dataset đó.
- Fake LLM trả đúng nhãn, nên recall/precision fake **chỉ chứng minh đường ống chạy**. JSON ghi `quality_evidence: "fake-pipeline-only"`; không được viết "recall đạt X%" từ kết quả fake. Phần tất định thì là số thật dù chạy fake: `rules_full_set` so với nhãn đã duyệt, và việc floor có mặt.
- Baseline chất lượng: noteboard có baseline thật (`eval/selector-real.json`). Dataset mới **chưa có** baseline thật: ở lượt nghiệm thu sau, phải đo cả pruner tại `baseline_pruner_sha` (vd bằng `git worktree`) lẫn pruner đã chỉnh, cùng model/prompt/policy.
- Chuẩn bị (không chạy) lệnh đo thật cho lượt nghiệm thu: lệnh, số lời gọi dự kiến, ước tính chi phí theo `llm/prices.py`, file kết quả mới (`eval/selector-real-pruned.json` cho noteboard, `eval/selector-real-<dataset>-{baseline,pruned}.json` cho dataset mới). Không ghi đè `eval/selector-real.json`.

### 6. Tài liệu

- Docstring `pruner.py` và `docs/usage-ci.md` (mục chi phí): tham số đã chốt, loại bằng chứng của từng con số (ước lượng hay đếm thật), và dataset nào đã đo. Không viết "giảm X% token" mà không kèm "ước lượng" và tên dataset.

## Test bắt buộc

- Unit test `pruner` xanh, thêm test cho mọi núm mới, và:
  - đổi tên thuần, đổi tên + sửa, xoá: file có trong danh sách, `status`/`old_path` đúng;
  - generated (có header, `linguist-generated`, thư mục chuẩn) và vendor: file có trong danh sách, không có hunk;
  - diff Unicode (tiếng Việt, emoji, CJK, tên file Unicode): không vỡ UTF-8 khi cắt, digest tất định, `estimate` dùng đúng `client.estimate_input_tokens` (lớn hơn ký tự/3 với chuỗi nhiều byte);
  - diff rất lớn dựng bằng code (hàng nghìn file và một file nhiều MB): danh sách file đầy đủ, trần tổng giữ, cắt được đánh dấu, thời gian chạy có giới hạn, `token_cap` → FULL SET với 0 lời gọi;
  - bất biến cho mọi ca của cả ba dataset: tập đường dẫn của B1 = tập đường dẫn của diff thô.
- `tests/test_eval_cost.py`: công thức tỉ lệ, median và P90 trên dữ liệu tổng hợp; ca FULL SET/chỉ-floor/`token_cap` bị loại khỏi median và có dòng riêng; báo ca tăng token; số tổng hợp không đổi kết luận từng dataset; `estimate` chạy không cần mạng (transport chặn mọi request); `count` đi qua egress và fake transport, `deny` thì không có HTTP.
- Test bộ nạp dataset: lệnh mặc định chạy đúng 40 mẫu noteboard; manifest sai báo lỗi rõ (exit 3); `labels_status: unreviewed` làm báo cáo in **CHƯA KIỂM CHỨNG**.
- Test request dùng chung: request của `select` giống từng byte trước/sau khi tách hàm.
- Test không hard-code tên dataset/repo trong `src/qc_agent/selector/`.
- Cổng chung: `python tools/freeze_contract.py --check` exit 0; `demo.yaml`/`demo_fail.yaml` exit 0/1.

## HỎI TRƯỚC (dừng, báo số đo, chờ tôi chọn)

- **Trần chặt `1 − B_min/A` (mục 0) < 40% ở median** trên noteboard hoặc trên một dataset mới (cổng ở đầu phiên và ở bước 2): dừng trước khi dựng dataset/tinh chỉnh. Đưa `A`, `P`, `B_min` ở median, ba trần, và các phương án với đề xuất: (a) chốt mục tiêu theo từng dataset (ghi rõ dataset nào không khả thi và vì sao); (b) đổi định nghĩa DoD sang "phần diff trong request", ghi rõ trong DoD và báo cáo là đã đổi; (c) giảm tiền tố (module-map, catalog, capabilities): đổi `diff_select.md` hoặc cách dựng system nên cũng là đổi `prompt_version` và mất cache S4-02, ngoài phạm vi, chỉ làm khi tôi duyệt.
- Median giảm `estimate` của một dataset < 40% dù trần chặt ≥ 40%, sau khi đã thử các núm hợp lệ: đưa số, trần, các phương án như trên và đề xuất.
- Nhãn nói FULL SET mà `_default.yaml` không chọn (lockfile lồng, `*.Dockerfile`): **không** tự sửa `_default.yaml` trong S4-04, vì nó đổi hành vi mọi project. Báo danh sách ca và đề xuất.
- Cần đổi `diff_select.md`: phải tăng `prompt_version`, làm mất cache S4-02 và làm baseline noteboard không còn so sánh trực tiếp được.
- Cần di chuyển/đổi tên fixture noteboard, thêm dependency, hoặc bất kỳ lời gọi mạng nào.

## Ngoài phạm vi

- Gọi LLM thật, `count_tokens` thật, push, mở PR, chạy trên SUT sản phẩm hay GitHub thật.
- Tick DoD S4.4 hoặc dòng DoD token/recall của S4.
- Sửa `_default.yaml`, `diff_select.md`, nhãn noteboard, `eval/selector-real.json`.

## Bảng nghiệm thu

| Việc / kết luận | Kiểm local ngay (bằng chứng) | Cần lượt đo thật về sau (PENDING) | Phạm vi kết luận |
|---|---|---|---|
| Lệnh mặc định `eval_selector --llm fake` chạy đúng golden set noteboard | `test_selector_golden.py` xanh không đổi kỳ vọng | — | noteboard |
| Bộ nạp dataset/manifest dùng chung cho hai công cụ | test bộ nạp; `--dataset all` chạy | — | ba dataset (đường ống) |
| Pruner không chứa tên repo/dataset | test grep | — | mã nguồn |
| Rào chắn: file list đầy đủ, rename/delete, generated/vendor, Unicode, diff rất lớn, floor, LLM lỗi → FULL SET, `token_cap` → FULL SET | unit test + bất biến trên ba dataset | — | nhiều kiểu repo (fixture tổng hợp) |
| Rules chọn đúng FULL SET / chỉ-floor theo nhãn | `rules_full_set` từng dataset (tất định) | — | noteboard; dataset mới chỉ khi `labels_status: reviewed`, nếu không là CHƯA KIỂM CHỨNG |
| Giả định ngầm của `_default.yaml` (lockfile lồng, `*.Dockerfile`) | ca tương ứng trong `monorepo-poly` | — | phát hiện; sửa là việc riêng do tôi quyết |
| Trần khả thi `1 − B_min/A` (cổng đầu phiên và cổng từng dataset mới) | `eval_cost --ceiling`, `estimate`; dưới 40% thì ghi `cost_target: null` + lý do (đã chốt A); noteboard đã đo: 2,5% | `count_tokens` xác nhận P và B_min | từng dataset đã đo; noteboard trước, dataset mới sau |
| Mức giảm token (median/P90, ca tăng token) | `eval_cost --llm-tokens estimate` từng dataset, ghi "ước lượng" | `--llm-tokens count` (gửi dữ liệu ra ngoài) | từng dataset đã đo; không suy rộng sang repo thật |
| DoD "giảm ≥ 40% ở median" | chỉ có số ước lượng: **không tick** | `count_tokens` hoặc `usage.input_tokens` thật | theo từng dataset |
| Đường ống chất lượng (recall/precision/final recall, fallback, Δ điểm %) | `--llm fake`, `quality_evidence: fake-pipeline-only` | — | không nói gì về model |
| Recall LLM noteboard không tụt so với 97,1% / precision so với 91,7% | — | `eval_selector --llm real --runs 3` trên pruner đã chỉnh | **chỉ noteboard** |
| Recall/precision trên `monorepo-poly`, `node-api` (trước và sau tinh chỉnh, tune và holdout) | — | lượt thật ở `baseline_pruner_sha` và SHA đã chỉnh, cần nhãn đã duyệt | từng dataset; trước khi đo: CHƯA KIỂM CHỨNG |
| DoD "recall S2 vẫn ≥ 90%" | — | lượt thật noteboard | **chỉ noteboard** (định nghĩa theo golden set S2) |
| Hiệu quả trên SUT sản phẩm / GitHub thật | — | ngoài S4-04 | chưa có bằng chứng |

Báo cáo cuối phiên phải dùng đúng cột "Phạm vi kết luận" này: mọi câu "đạt", "không tụt", "giảm X%" phải nói kèm dataset và loại bằng chứng.
