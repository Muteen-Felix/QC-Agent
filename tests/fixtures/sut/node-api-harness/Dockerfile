# SUT mẫu cho harness local (S4-05): Node 22 chạy TypeScript trực tiếp (type stripping), không build, không `npm install`.
# Cùng digest với NODE_IMAGE trong Dockerfile gốc của qc-agent (test tests/test_node_api_harness_fixture.py canh hai chuỗi bằng nhau).
FROM node:22-bookworm-slim@sha256:48e4b67d85f87bd551df43704e24d252f56cc5f8e9718841aace50f19948f0f9
WORKDIR /app
COPY package.json openapi.json ./
COPY src ./src
ENV PORT=3000
EXPOSE 3000
USER node
CMD ["node", "src/server.ts"]
