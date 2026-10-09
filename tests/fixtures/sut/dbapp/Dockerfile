# SUT mẫu cần database (S4-09): chỉ chạy được khi có Postgres ở db:5432 và DATABASE_URL trong env (secret SUT_SECRET_ENV của workflow).
# Không cài gì thêm (app.py chỉ dùng thư viện chuẩn), nên build không cần mạng ngoài ảnh nền.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
WORKDIR /app
COPY app.py .
USER nobody
EXPOSE 8000
CMD ["python", "app.py"]
