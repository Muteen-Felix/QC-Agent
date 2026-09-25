FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VAHAN_API_HOST=0.0.0.0 \
    VAHAN_API_PORT=8000

# Copy dependencies specification and application source
COPY apps/api-server/pyproject.toml ./
COPY apps/api-server/app ./app
COPY apps/api-server/README.md ./

# Install dependencies and the application package
RUN pip install --no-cache-dir .

# Create runtime directories for logs and reports
RUN mkdir -p runtime/ui-health-logs runtime/excel-reports

EXPOSE 8000

# Run uvicorn server, supporting custom port via PORT or VAHAN_API_PORT (default: 8000)
CMD ["sh", "-c", "exec uvicorn app.main:application --host 0.0.0.0 --port ${PORT:-${VAHAN_API_PORT:-8000}}"]
