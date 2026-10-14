ARG QC_RUNTIME
FROM ${QC_RUNTIME}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git openssl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /data && chown qc:qc /data
COPY src /onboarding/src
COPY tools/github_app.py tools/github_app_service.py /onboarding/tools/
ENV PYTHONPATH=/onboarding/src:/onboarding
WORKDIR /onboarding
USER qc
EXPOSE 8080
# One worker per SQLite volume. This process only does onboarding, never gate/test execution.
ENTRYPOINT ["python", "-m", "uvicorn", "tools.github_app_service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
