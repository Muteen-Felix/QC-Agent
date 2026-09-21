> Orchestrator "ngu" là orchestrator đúng. Routing bằng LLM là thừa và có hại. [arch §6.2]

- **Dựng môi trường** (STEP 04): `powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1`, rồi `.\scripts\doctor.ps1` phải in toàn `OK` + `ENV-FINGERPRINT`.
- **Kiểm tra fingerprint trên macOS**: `bash scripts/doctor_macos.sh` (cần `.venv` và các dependency/tool như cấu hình dự án).
- **Chạy toy app** (STEP 11): `.\scripts\toyapp.ps1` (mặc định `http://127.0.0.1:8000`, mở `/docs`).
- **Chạy orchestrator** (STEP 20): `python orchestrator.py --plan plans/demo.yaml`.

Mỗi terminal mới: `. .\scripts\env.ps1` trước. Tài liệu: `docs/architecture.md`, `docs/plan-execution.md`.
