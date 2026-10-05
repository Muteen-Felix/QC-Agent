# ADR 0001: `core/` không dùng LLM; verdict là hàm thuần
- Trạng thái: Accepted
- Ngày: 2026-09-29 (chuyển từ `architecture.md` §5.2)

## Bối cảnh
Gate có quyền chặn code của người khác, nên phải trả lời được câu "vì sao PR của tôi bị chặn".
`core/` chỉ quyết 4 thứ: chạy suite nào (policy), worker nào nhận task (capability), thứ tự nào
(`depends_on`), đỏ hay xanh (lane và severity). Cả bốn chỉ có một đáp án đúng.

## Quyết định
- `core/` không gọi LLM và không import `qc_agent.llm` / `groundtruth` / `selector` ở top-level.
  Lệnh CLI mới dùng import lười để `--trigger manual` không kéo LLM vào tiến trình.
- Verdict là hàm thuần của (kết quả worker, policy, `selection.json`): cùng đầu vào thì cùng verdict.
- Ranh giới thật không phải "có LLM hay không" mà là: **LLM được sinh ứng viên và chọn phạm vi ngoài
  floor; LLM không được phán quyết; mọi thứ nó ảnh hưởng đều là một file đọc được** (TC qua PR,
  `selection.json` trong artifact).

## Hệ quả
- Muốn biết vì sao PR đỏ: mở policy và `selection.json`.
- Tách vòng ngoài (có LLM) khỏi vòng trong (tất định) vì ba lý do:
  1. **Quyền:** nhập lại thì LLM thừa hưởng quyền chặn merge.
  2. **Nhịp:** vòng trong xong trong vài phút ở mọi PR (chọn phạm vi P95 ≤ 20 giây); vòng ngoài được chạy lâu, chạy đêm, thử lại.
  3. **Hỏng:** LLM hết quota thì vòng ngoài dừng, selector rơi về FULL SET, phán quyết không bị ảnh hưởng.
- Đưa LLM vào `core/` không thêm năng lực nào, chỉ thêm chi phí, độ trễ và sai số.
