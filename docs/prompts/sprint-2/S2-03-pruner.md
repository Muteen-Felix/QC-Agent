# S2-03 · Pruner: diff của PR → payload gọn cho LLM (plan S2.2)

- Branch: `feat/s2-03-pruner`
- Tiền điều kiện: S2-01 đã merge (đã có package `selector/`). Chạy song song được với S2-02.

## Đọc trước

- `docs/prompts/_common.md`; plan S2.2; README "Hiệu chỉnh" #7 (dùng merge-base)
- `src/qc_agent/adapters/coverage_debt_worker.py`: cách repo đang gọi git và đọc diff; dùng lại được phần nào thì dùng
- `src/qc_agent/scaffold/gitinfo.py`

## Mục tiêu

Biến thay đổi của PR thành một payload **tất định**, **gọn**, và **ít bề mặt injection**. Danh sách file luôn đầy đủ, kể cả khi hunk bị cắt, vì rules (S2-02) và FULL SET dựa vào nó.

## Việc cần làm

1. **`src/qc_agent/selector/pruner.py`**
   ```python
   @dataclass(frozen=True)
   class PrunedFile:
       path: str; status: str; old_path: str | None
       kind: str            # code | lockfile | binary | generated | vendor | deleted
       hunks: str | None    # None khi bị bỏ theo kind hoặc vượt trần
       truncated: bool; dropped_hunks: int
   @dataclass(frozen=True)
   class PrunedDiff:
       base: str; head: str; merge_base: str
       files: tuple[PrunedFile, ...]; approx_tokens: int; sha256: str
   def prune(repo: Path, base: str, head: str, *, per_file_tokens=1500, total_tokens=12000) -> PrunedDiff
   ```
2. **Gọi git an toàn**
   - `base`/`head` phải khớp `^[0-9a-fA-F]{7,40}$` hoặc là tên ref hợp lệ (regex chặt, không bắt đầu bằng `-`).
   - Mọi lời gọi là argv list, có `--` ngăn cách đường dẫn, có `-c core.quotepath=off`, `--no-color --no-ext-diff`, và timeout.
   - `merge_base = git merge-base base head`. Diff tính **từ merge-base**, tương đương `base...head`, để không lẫn commit mới của nhánh đích.
3. **Danh sách file**: `git diff --name-status -z -M <merge_base> <head>`. Đầy đủ, gồm cả rename/delete/binary.
4. **Hunk**: `git diff -w -U1 <merge_base> <head> -- <path>` (plan: `-w`), chạy theo từng file hoặc cả lượt rồi tách theo file.
5. **Loại bỏ theo kind** (file vẫn nằm trong danh sách, chỉ `hunks=None`):
   - **binary**: phát hiện bằng numstat `-`;
   - **lockfile**: `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `uv.lock`, `poetry.lock`, `Pipfile.lock`, `Cargo.lock`, `go.sum`, `composer.lock`;
   - **vendor/generated**: `vendor/**`, `node_modules/**`, `dist/**`, `build/**`, `*.min.js`, `*.map`, và file có `@generated` hoặc `DO NOT EDIT` trong 5 dòng đầu ở phía head.
6. **Bỏ hunk chỉ đổi comment hoặc format**. Whitespace đã được `-w` lo. Một hunk bị bỏ **chỉ khi mọi dòng +/−** đều rỗng hoặc là comment, theo bảng nhận diện comment của `.py`, `.js/.ts/.jsx/.tsx`, `.java/.go/.c/.cpp/.cs`, `.sh`, `.yml/.yaml`, `.sql`, `.html/.md`.
   - Ghi chú trong docstring: nhờ đó payload injection nằm trong comment cũng bị cắt. Nhưng đây **không** phải lớp phòng thủ chính; phòng thủ chính là floor và allowlist.
7. **Trần token**
   - Ước lượng tất định bằng số ký tự / 4 (ghi rõ trong docstring là *ước lượng*; S4-04 đo thật bằng `count_tokens`).
   - Một file vượt trần → cắt hunk, thêm dòng `…[cắt N dòng]`, `truncated=true`.
   - Tổng vượt trần → bỏ hunk của file có độ ưu tiên thấp trước (lớn nhất trước, test trước code), nhưng **giữ entry của file**.
8. **`sha256`**: tính trên JSON chuẩn hoá (sort key) của `files`. Dùng làm `diff_sha256` trong selection và làm khoá cache ở S4-02.
9. **Log**: `selector.prune` gồm số file theo từng kind, `approx_tokens`, `truncated`. **Không** log tên file (có thể lộ tên nội bộ) và không log nội dung.

## Test bắt buộc: `tests/test_selector_pruner.py`

Dựng repo git thật trong `tmp_path`. Nếu máy không có `git` thì `skip`, kèm lý do.

- Binary, lockfile, vendor, generated → có trong `files`, `hunks=None`.
- Hunk chỉ đổi comment (py, js) bị bỏ; hunk trộn comment và code được giữ; hunk chỉ đổi whitespace biến mất.
- Rename và delete có đúng `status` và `old_path`.
- File lớn bị cắt; tổng vượt trần → mọi file vẫn có trong danh sách.
- **Merge-base**: nhánh base có commit mới sau khi PR tách ra → những file đó **không** xuất hiện trong diff.
- `base` dạng `--output=/tmp/x` hoặc `-x` bị từ chối.
- Hai lần prune ra cùng `sha256`.

## Ngoài phạm vi

Gọi LLM, quyết định rules, cache, tinh chỉnh trần (S4-04).
