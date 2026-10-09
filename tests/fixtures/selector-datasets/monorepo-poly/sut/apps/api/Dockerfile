FROM python:3.12-slim
WORKDIR /app
COPY apps/api/requirements.txt .
RUN pip install -r requirements.txt
COPY apps/api .
CMD ["uvicorn", "main:app"]
