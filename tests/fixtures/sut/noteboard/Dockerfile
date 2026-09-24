# SUT tham chiếu `noteboard` dưới dạng container: đây là cách một repo SUT thật cung cấp SUT cho workflow tái sử dụng
# (input sut_dockerfile). QC_BUGS bật/tắt các lỗi cài sẵn (none = sạch; 1,2,3 = có lỗi) — đặt qua input sut_env.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
RUN pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.53.0 pydantic==2.13.5
WORKDIR /app
COPY toyapp ./toyapp
ENV QC_BUGS=none
USER nobody
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "toyapp.app:app", "--host", "0.0.0.0", "--port", "8000"]
