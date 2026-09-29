# S1-02 · Schema Ground-Truth + parse PRD (plan S1.3, S1.2)

- Branch: `feat/s1-02-gt-schema-prd`
- Tiền điều kiện: S1-00 đã merge. Không phụ thuộc S1-01.

## Đọc trước

- `docs/prompts/_common.md`; plan S1.2, S1.3, và "Nguyên tắc thiết kế" của Sprint 1
- `src/qc_agent/scaffold/openapi.py`: dùng lại `load`, `analyze`, `Analysis`
- `tests/fixtures/sut/noteboard/toyapp/app.py` và `summarizer.py`: hành vi **sạch** (`QC_BUGS=none`) chính là "sự thật nghiệp vụ" mà PRD mẫu mô tả
- `tests/fixtures/openapi/`

## Mục tiêu

Chốt hình dạng dữ liệu của Ground-Truth, vì mọi bước sau đều dựa vào nó. Sau đó viết bộ parse PRD tất định.

LLM chỉ sinh dữ liệu có cấu trúc. QA duyệt dữ liệu, không duyệt code. Vì thế schema phải đủ chặt để một renderer tất định biến được nó thành test chạy được.

## Việc 1: `schemas/ground_truth.json` và `schemas/module_map.json`

Cả hai là *schema dữ liệu* (Draft 2020-12), **không** nằm trong `CONTRACT.lock`; ghi điều đó trong `$comment`. Mọi object đều có `additionalProperties: false`.

**Hình dạng đề xuất cho catalog** (đây là file `test-cases.yaml`):

```yaml
version: 1
prd: {id: noteboard, sha256: "<64 hex>", source: docs/prd/noteboard.md}
generated_by: {model: claude-sonnet-5, prompt_version: gt-generate/1}
status: draft                      # draft | approved (approved chỉ khi không còn TC draft)
stories:
  - story_id: US-1
    title: "…"
    acs: [{ac_id: AC-1.1, text: "…"}]
test_cases:
  - tc_id: TC-AC-1.1-3f2a1c        # DO CODE TÍNH (S1-04), không phải LLM
    title: "…"
    ac_refs: [AC-1.1]
    kind: api_functional           # api_contract | api_functional | flow
    status: draft                  # draft | approved | rejected
    origin: llm                    # llm | qa
    rejected_reason: null          # bắt buộc khác null khi status=rejected
    notes: null                    # ghi chú của QA
    steps:                         # api_functional: đúng 1 bước; flow: ≥ 2 bước; api_contract: 1 bước, chỉ method+path
      - request: {method: POST, path: /notes, path_params: {}, query: {}, headers: {}, json: {title: "a", body: "b"}}
        expect:
          status: [201]
          json: [{path: "$.title", op: eq, value: "a"}]
        capture: {note_id: "$.id"}  # bước sau dùng "{{note_id}}" trong path/query/json
uncovered_acs: [{ac_id: AC-3.2, reason: "chỉ kiểm được trên UI"}]
```

- Plan ghi TC có hai trường `request` và `expect`. Đề xuất ở đây gói chúng vào `steps[]` để kiểu `flow` dùng chung một hình dạng. Nêu quyết định này trong báo cáo.
- **Bộ assertion đóng**:
  - `op` ∈ {`eq`, `ne`, `exists`, `absent`, `type`, `len_eq`, `len_gte`, `contains`};
  - `value` chỉ là JSON scalar;
  - `path` chỉ là tập con JSONPath `$`, `.key`, `[n]` (validate bằng regex);
  - `type` ∈ {`string`, `number`, `integer`, `boolean`, `null`, `object`, `array`}.

  Không có trường nào chứa code hay biểu thức.
- Viết schema sao cho bản `wire_schema` (S1-01, đã bỏ min/max/length) vẫn còn nghĩa: ưu tiên `enum`/`const` hơn ràng buộc số.

**Hình dạng của module map** (`module-map.yaml`):

```yaml
version: 1
status: draft
modules:
  - name: notes
    paths: ["toyapp/app.py"]       # glob POSIX, tương đối gốc repo SUT, hỗ trợ **
    suites: [api-contract, gt-functional]
    source: openapi                # openapi | scan | qa
```

## Việc 2: `src/qc_agent/groundtruth/{__init__.py, prd.py}`

```python
@dataclass(frozen=True)
class AC: ac_id: str; text: str
@dataclass(frozen=True)
class Story: story_id: str; title: str; acs: tuple[AC, ...]
@dataclass(frozen=True)
class ParsedPRD:
    prd_id: str; sha256: str; format: str   # markdown | openapi | text
    stories: tuple[Story, ...]; endpoints: tuple[dict, ...]; warnings: tuple[str, ...]

def parse_prd(path: Path, *, openapi_source: str | None = None) -> ParsedPRD
```

- **Markdown**:
  - chia theo heading;
  - nhận khối story qua heading hoặc dòng chứa `User Story` / `Story` / `US-<n>`;
  - nhận AC dưới heading `Acceptance Criteria` / `AC` / `Tiêu chí chấp nhận`, ở dạng `AC-<n>(.<n>)*:`, list item, hoặc Given/When/Then.
- **ID**:
  - ưu tiên ID tường minh trong PRD;
  - nếu thiếu thì dùng ID theo nội dung, `AC-<sha1(text chuẩn hoá)[:8]>`, để đổi thứ tự không làm lệch ID; kèm một warning khuyên BA ghi ID.
- **OpenAPI**: nhận diện theo đuôi file hoặc key `openapi`/`swagger`. Lấy endpoint (method, path, tham số bắt buộc, mã response) bằng `scaffold/openapi.py`. `openapi_source` cho phép truyền OpenAPI riêng khi PRD là Markdown.
- **Text thô**: fallback về một story `S-1` duy nhất, không có AC, kèm warning.
- **`prd_sha256`**: tính trên bytes đã chuẩn hoá (bỏ BOM, CRLF→LF), giống `core/project.file_sha256`. `prd_id` lấy từ front-matter `id:` nếu có, không thì từ tên file, rồi slug hoá thành `[a-z0-9-]`.
- **Giới hạn**: PRD > 256 KB → `GTInputError`, sau này CLI trả exit 3. Không log nội dung PRD.

## Việc 3: fixture `tests/fixtures/prd/noteboard-prd.md`

Viết PRD cho toyapp đúng với hành vi **sạch** hiện tại: 4–6 user story, 15–25 AC, mỗi AC có ID tường minh.
- Mỗi AC phải kiểm được theo kiểu hộp đen qua API: mã trạng thái, validation 422, 404 sau khi xoá, quy tắc của summarize, id dài bất thường thì trả 4xx chứ không 5xx (liên quan BUG-1), …
- Thêm 1–2 AC chỉ kiểm được trên UI, để thử luồng `uncovered_acs`.

AC phải đủ cụ thể, vì S1-08 sẽ cài mutant BUG-4…BUG-13 bám vào chúng.

## Test bắt buộc: `tests/test_gt_schema.py`, `tests/test_gt_prd.py`

- Schema:
  - catalog mẫu hợp lệ;
  - bắt được từng lỗi cấu trúc: thiếu `rejected_reason` khi `rejected`, `op` lạ, `path` sai cú pháp, `flow` chỉ có 1 bước;
  - `wire_schema(schema)` vẫn là JSON Schema hợp lệ.
- PRD:
  - đếm đúng số story/AC và ID của fixture;
  - ID fallback ổn định khi đảo thứ tự;
  - heading tiếng Việt;
  - Given/When/Then;
  - OpenAPI (dùng fixture có sẵn);
  - text thô;
  - CRLF và LF cho cùng sha256;
  - quá cỡ → lỗi.

## Ngoài phạm vi

Gọi LLM, render, CLI.
