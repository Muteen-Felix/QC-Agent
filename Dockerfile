# syntax=docker/dockerfile:1
# qc-agent: CLI (GitHub Actions) + service (API/executor) trong MỘT image, có sẵn mọi công cụ worker:
#   Python 3.11 (+ Schemathesis `st`, DeepEval), Node 22 (+ @midscene/cli, Playwright Chromium), k6.
# Mọi image nền ghim theo DIGEST (tái lập được); phiên bản Python/Node/k6 khớp .python-version/.node-version/.k6-version.
# Cập nhật digest: docker pull <tag> && docker inspect --format '{{index .RepoDigests 0}}' <tag>
#
#   docker build -t qc-agent .
#   docker run --rm qc-agent --help
#   docker run --rm -v "$PWD:/work" -e APP_BASE_URL=... qc-agent run --project <slug> --mode pr --sut-root /work
#   service:  docker run --rm -p 8080:8080 -e QC_DATABASE_URL=... --entrypoint uvicorn qc-agent \
#                 qc_agent.api.app:create_app --factory --host 0.0.0.0 --port 8080        (kiểm tra sức khoẻ: GET /healthz, /readyz)
#   executor: docker run --rm -e QC_DATABASE_URL=... --entrypoint python qc-agent -m qc_agent.jobs.executor

ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9
ARG K6_IMAGE=grafana/k6:2.2.0@sha256:9bd01d6941fca969cb61bb57d2da5ee9b385fe2aa8881df3798c196564d6ace6
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc

FROM ${NODE_IMAGE} AS node
FROM ${K6_IMAGE} AS k6
FROM ${UV_IMAGE} AS uv

# ---- build: cài phụ thuộc + qc-agent (KHÔNG editable) từ uv.lock vào /opt/venv ----
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project      # lớp phụ thuộc: chỉ đổi khi lock đổi
COPY src ./src
COPY schemas ./schemas
COPY workers ./workers
COPY configs ./configs
COPY web ./web
RUN uv sync --frozen --no-dev --no-editable            # contract/workers/configs/web được đóng gói vào wheel (force-include)

# ---- runtime ----
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="qc-agent" \
      org.opencontainers.image.description="QC gate orchestrator: CLI for CI + API/executor service" \
      org.opencontainers.image.source="https://github.com/Muteen-Felix/QC-Agent"
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:/opt/qc-node/node_modules/.bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    NPM_CONFIG_UPDATE_NOTIFIER=false NPM_CONFIG_FUND=false

# git: signature.py lấy commit của SUT bằng `git rev-parse HEAD`
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node + npm/npx: copy từ image node chính thức (đã ghim), không tải tarball ngoài
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
# shim npx: `npx @midscene/cli` chạy bản đã khoá (xem docker/npx); các lời gọi khác chuyển tiếp tới npx thật
COPY docker/npx /usr/local/bin/npx
RUN chmod +x /usr/local/bin/npx

COPY --from=k6 /usr/bin/k6 /usr/bin/k6
COPY --from=build /opt/venv /opt/venv

# @midscene/cli (ghim bằng package-lock.json) + Chromium của Playwright cùng các thư viện hệ thống của nó
WORKDIR /opt/qc-node
COPY package.json package-lock.json ./
RUN npm ci && npx playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm && chmod -R a+rX /opt/ms-playwright /opt/qc-node

# Midscene CLI dùng Puppeteer, mà Puppeteer không tự tìm Chromium của Playwright ('Could not find Chrome'): trỏ tới bản đã cài bằng symlink có tên ổn định
# (đường dẫn thật chứa số phiên bản). Nếu không có bước này mọi task Midscene trong image đều lỗi, gồm cả canary (fail đúng kỳ vọng nhưng vì lý do sai).
RUN ln -s "$(find /opt/ms-playwright -path '*chrome-linux*/chrome' -type f | head -1)" /opt/ms-playwright/chrome && test -x /opt/ms-playwright/chrome
ENV PUPPETEER_EXECUTABLE_PATH=/opt/ms-playwright/chrome

# không chạy bằng root: mã của SUT/PR là input không tin cậy
RUN useradd --uid 10001 --create-home qc && mkdir /work && chown qc:qc /work
# commit của qc-agent mà image này build từ đó: `qc-agent init` dùng để ghim sẵn `uses:` trong qc.yml; `validate` in ra khi phải dùng snapshot policy
ARG QC_AGENT_GIT_SHA=unknown
ENV QC_AGENT_GIT_SHA=${QC_AGENT_GIT_SHA}
WORKDIR /work
USER qc
ENTRYPOINT ["qc-agent"]
CMD ["--help"]
