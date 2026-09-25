# Web UI tĩnh của SUT tham chiếu, để kiểm chứng đường `sut_ui_*` của workflow tái sử dụng (container `ui` cạnh container `sut`).
# UI thật (vd. Vite) dùng ARG như VITE_API_URL theo đúng cách này: giá trị nhúng lúc build, KHÔNG đặt bí mật.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b
ARG MARKER=none
WORKDIR /srv
RUN printf '<!doctype html><title>ui</title><p id="marker">%s</p>' "$MARKER" > index.html
USER nobody
EXPOSE 8080
CMD ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0"]
