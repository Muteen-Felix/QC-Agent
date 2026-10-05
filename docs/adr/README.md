# Architecture Decision Records (ADR)

Mỗi file ghi **một** quyết định kiến trúc: bối cảnh, quyết định, hệ quả. ADR không sửa lại khi đổi ý:
quyết định mới là ADR mới, ADR cũ đổi trạng thái thành `Superseded by NNNN`.

| ADR | Quyết định | Trạng thái |
|---|---|---|
| [0001](0001-core-khong-dung-llm.md) | `core/` không dùng LLM; verdict là hàm thuần | Accepted |
| [0002](0002-diff-agent-chon-pham-vi-co-floor.md) | LLM được chọn phạm vi chạy, nhưng bị bao vây và có floor | Accepted |
| [0003](0003-gt-llm-sinh-du-lieu-khong-sinh-code.md) | Ground-Truth: LLM sinh dữ liệu test case, code test render tất định | Accepted |
| [0004](0004-llm-qua-httpx-chon-provider-theo-model.md) | Gọi LLM qua `httpx`, không SDK; chọn provider theo tiền tố model | Accepted |

## Mẫu

```markdown
# ADR NNNN: <quyết định>
- Trạng thái: Proposed | Accepted | Superseded by NNNN
- Ngày: YYYY-MM-DD

## Bối cảnh
## Quyết định
## Hệ quả
```
