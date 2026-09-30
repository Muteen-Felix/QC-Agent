# S2-02 · Policy floor/full-set + path rules + suite floor cho noteboard (plan S2.3, S2.5 lớp selector)

- Branch: `feat/s2-02-policy-floor-rules`
- Tiền điều kiện: S2-01 đã merge.

## Đọc trước

- `docs/prompts/_common.md`; plan §0 quyết định #1 (floor = secrets + sast), S2.3, S2.5; DoD các dòng "docs → chỉ floor" và "Dockerfile → FULL SET"
- `src/qc_agent/core/project.py`: `PROJECT_SCHEMA` (mode policy), `resolve_project`, `deep_merge` (**list thay thế, không cộng dồn**), `suites_by_worker` (S2-01)
- `configs/projects/_default.yaml`, `configs/projects/noteboard.yaml`, `tests/test_shipped_projects.py`
- `src/qc_agent/scaffold/suites_security.py` (`sast_suite`, `secrets_suite`)
- `.github/workflows/ci.yml`, job `image-e2e`
- `tests/fixtures/workers/fake_semgrep.py`, `fake_gitleaks.py`
- `schemas/module_map.json` (S1-02)

## Mục tiêu

Luật tất định chạy **trước** LLM. Bước này làm ba việc:
- định nghĩa floor trong policy;
- dựng phần rules: `full_set_paths` → FULL SET, diff chỉ chạm docs → chỉ floor, module-map → gợi ý;
- cho noteboard có suite floor thật.

## Việc cần làm

1. **Schema policy** (`core/project.py`, trong từng mode):
   - `floor_workers`: danh sách tên worker;
   - `full_set_paths`, `docs_paths`: danh sách glob POSIX, hỗ trợ `**`;
   - `max_parallel`: số nguyên ≥ 1, mặc định 1. S2-06 dùng key này.

   Khi resolve, kiểm: mọi suite của floor (tra qua `suites_by_worker`) phải nằm trong `blocking_suites` của mode, nếu không → `PlanError`. Floor không được rơi vào lane tư vấn.
2. **`configs/projects/_default.yaml`**, mode `pr`:
   ```yaml
   floor_workers: [gitleaks, semgrep]
   full_set_paths: [Dockerfile, "**/Dockerfile*", "docker-compose*.y*ml", package.json, package-lock.json,
                    pnpm-lock.yaml, yarn.lock, "requirements*.txt", pyproject.toml, uv.lock, poetry.lock,
                    "Pipfile*", go.mod, go.sum, ".github/workflows/**", ".qc-agent/**"]
   docs_paths: ["docs/**", "**/*.md", "**/*.rst", "**/*.adoc", "LICENSE*", "CHANGELOG*"]
   max_parallel: 1
   ```
   Giữ comment cảnh báo "đổi file này ảnh hưởng mọi team".
3. **noteboard có floor thật**:
   - Thêm `tests/fixtures/sut/noteboard/.qc-agent/suites/sast.yaml` và `secrets.yaml`, sinh bằng `suites_security` (task `t-010`, `t-011`).
   - `configs/projects/noteboard.yaml` → `pr.blocking_suites` thêm `sast`, `secrets` và **giữ đủ** các suite cũ.
   - Chạy `pytest -q`. Test đang chạy noteboard mode `pr` bằng worker giả thì sửa theo một trong hai cách:
     - thêm manifest giả cho semgrep/gitleaks (đã có `fake_semgrep.py`, `fake_gitleaks.py`);
     - hoặc giới hạn `--suites` trong chính test đó.

     **Không** nới lỏng policy.
   - Có Docker thì kiểm `image-e2e`: semgrep/gitleaks thật chạy trên toyapp sạch vẫn phải exit 0, còn `1,3` phải exit 1. Không có Docker thì ghi rủi ro vào báo cáo.
4. **`src/qc_agent/selector/rules.py`**
   ```python
   @dataclass(frozen=True)
   class ChangedFile: path: str; status: str   # A|M|D|R|… (từ pruner S2-03; ở đây nhận list thuần)
   @dataclass(frozen=True)
   class RuleDecision:
       full_set: bool; floor_only: bool; reason: str
       hint_workers: dict[str, tuple[str, ...]]   # worker -> lý do (vd "module-map: notes ← toyapp/app.py")
       unmapped: tuple[str, ...]                  # file không khớp module nào
       module_map_status: str                     # approved | draft | missing
   def decide(changed, mode_policy, module_map: dict | None, suite_map: dict[str, list[str]]) -> RuleDecision
   ```
   - Thứ tự ưu tiên:
     1. diff rỗng → `floor_only`;
     2. có file khớp `full_set_paths` → `full_set` (lý do nêu file đầu tiên khớp);
     3. mọi file khớp `docs_paths` → `floor_only`;
     4. còn lại → gợi ý từ module-map, theo chuỗi module → suites → workers (đảo `suite_map`).
   - Gợi ý **chỉ được thêm**, nên dùng được cả khi module-map đang `draft` hoặc `approved`; ghi `module_map_status` vào lý do. Thiếu module-map → không có gợi ý.
   - Tự viết `glob_to_regex` có hỗ trợ `**`. Không dùng `PurePath.full_match` (chỉ có từ Python 3.13; repo cho phép ≥ 3.11). Có test cho `**`, `*`, `?`, dotfile, và phân biệt hoa thường theo POSIX.
5. **Gộp floor, lớp 1 (selector)**: `selector/payload.py` thêm `merge_floor(selection, mode_policy, suite_map)`, dùng ở S2-04/05. Lớp 2 nằm ở `core/` (S2-05).
6. **Tài liệu**: `docs/usage-ci.md` giải thích floor, `full_set_paths`, `docs_paths` và cách team override (nhớ list thay thế list).

## Test bắt buộc

- `tests/test_selector_rules.py`: đủ 4 nhánh ưu tiên; khớp `Dockerfile` → FULL SET; chỉ `docs/**` hoặc `*.md` → floor only; `requirements.txt` không bị nhận nhầm là docs; gợi ý từ module-map; file ngoài map → `unmapped`; thiếu module-map.
- `tests/test_project.py` (mở rộng): schema của key mới; floor nằm ngoài `blocking_suites` → `PlanError`; `deep_merge` với list thay thế.
- `tests/test_shipped_projects.py` xanh.

## Ngoài phạm vi

Chạy git diff (S2-03), gọi LLM (S2-04), gộp floor trong `core/` (S2-05), chạy song song (S2-06).
