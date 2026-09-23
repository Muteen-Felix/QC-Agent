# QC-Agent Dashboard

Lớp web **bọc ngoài** pipeline: chỉ đọc `runs/r-NNNN/` + `midscene_run/report/` và gọi
`python orchestrator.py --plan plan.yaml` qua subprocess. Không import và không sửa `core/`, `adapters/`, `schemas/`, `tests/`, `plan.yaml`.

```powershell
.\scripts\toyapp.ps1 start      # SUT phải chạy ở :8000 thì nút 🚀 mới hoạt động
.\scripts\dashboard.ps1         # http://127.0.0.1:8080
python -m pytest dashboard/tests -q
```

- Không thêm dependency: dùng FastAPI/uvicorn đã có trong `.venv`. Tailwind tải từ CDN (mất mạng thì vẫn đọc được nhờ CSS fallback nội tuyến).
- Nút **🚀** chạy lệnh giống `scripts/demo.ps1` (dot-source `scripts/env.ps1`). Mỗi lần chạy ~85s và gọi LLM thật (Midscene/DeepEval). Mỗi lúc chỉ một job; log ở `dashboard/_state/job-*.log`.
- Đừng chạy `orchestrator.py` từ CLI **cùng lúc** với nút 🚀: core cấp `run_id` bằng max+1 nên hai run có thể đụng ID.
- **📌 Tạo Ticket Jira là MOCK**: không gọi Jira; ghi ticket nháp vào `dashboard/_state/tickets.json`, kèm sha256 thật của evidence.
- Cost: task báo `usd = null` được đếm là "không báo cost", không cộng như $0.

## Webhook alerting (v2)

Đặt `ALERT_WEBHOOK_URL` trong `.env` (Slack incoming webhook / Discord webhook / Telegram bot
`.../bot<TOKEN>/sendMessage` — thêm `ALERT_TELEGRAM_CHAT_ID`) để mỗi lần pipeline chạy xong tự
động bắn 1 message tổng hợp (verdict + gate x/y + finding + link `#run=`) qua
`dashboard/notifier.py`. Không có env → chỉ ghi log vào `dashboard/_state/alerts.log` (không bao
giờ ghi URL — đó là secret). Nút "Gửi thử" trên header gọi `POST /api/alerts/test`.
`.github/workflows/qc-gate.yml` dùng lại đúng module này để CI cũng gửi được thông báo.
