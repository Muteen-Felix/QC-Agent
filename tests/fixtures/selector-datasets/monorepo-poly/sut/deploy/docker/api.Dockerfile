FROM python:3.12-slim
WORKDIR /srv
COPY apps/api /srv
CMD ["python", "main.py"]
