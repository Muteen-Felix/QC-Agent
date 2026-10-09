"""Công cụ S4-06: chuẩn bị nghiệm thu E2E trên repo sandbox thật. Mọi thứ ở đây CHẠY OFFLINE.

Quy ước bất di bất dịch (test `tests/test_e2e_tools.py` canh):
  - không import thư viện mạng, không gọi `gh`/`curl`/`docker`: việc ra ngoài chỉ được IN thành lệnh cho người chạy;
  - mỗi bước gắn nhãn `offline` / `llm` (tốn tiền, gửi dữ liệu ra ngoài) / `external` (GitHub, Jira, secret, branch protection);
  - bằng chứng luôn mang nguồn (`github` | `local` | `fake`); chỉ `github` mới có thể là bằng chứng DoD, và công cụ không bao giờ tự tick DoD.

    python -m tools.e2e --help
"""
