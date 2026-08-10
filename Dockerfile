FROM ghcr.io/xtls/xray-core:26.5.3 AS xray-core

# Build the admin + portal SPA bundles in a Node stage so the built JS/CSS is not
# committed to the repo. Vite emits to ../app/static/{admin,portal} (see
# frontend/vite.config.js + vite.portal.config.js), i.e. /build/app/static/* here.
FROM node:20-bookworm-slim AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

FROM debian:bookworm-slim

# Runtime deps. python3-pip replaces the apt python3-flask package (the Python
# deps are pinned in requirements.txt and installed with pip).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        ca-certificates \
        docker.io \
        python3 \
        python3-pip \
        openssh-client \
        tar \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /app/xray/runtime /app/xray/logs

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY app /app
COPY components /app/components
COPY scripts /app/scripts
# Built SPA bundles come from the frontend builder, not from the repo.
COPY --from=frontend-builder /build/app/static/admin /app/static/admin
COPY --from=frontend-builder /build/app/static/portal /app/static/portal
COPY --from=frontend-builder /build/app/static/landing /app/static/landing
COPY --from=xray-core /usr/local/bin/xray /usr/local/bin/xray

ENV DATA_DIR=/data \
    DB_PATH=/data/panel.db \
    XRAY_ENV_FILE_PATH=/app/xray/.env \
    XRAY_CONFIG_PATH=/app/xray/runtime/config.json \
    XRAY_PANEL_PORTS_PATH=/app/xray/runtime/panel-ports.json \
    XRAY_ACCESS_LOG_PATH=/app/xray/logs/access.log \
    XRAY_API_SERVER=127.0.0.1:10085 \
    XRAY_CONTAINER_NAME=xray-reality-local \
    XRAY_DOCKER_BIN=docker \
    XRAY_STATS_QUERY_TIMEOUT=5 \
    XRAY_PROBE_HOST=127.0.0.1 \
    XRAY_CLIENT_CONFIG_PATH=/app/xray/runtime/client-test.json \
    PANEL_HOST=0.0.0.0 \
    PANEL_PORT=18080 \
    PANEL_PUBLIC_URL= \
    DEFAULT_UPSTREAM_HOST=127.0.0.1 \
    DEFAULT_UPSTREAM_PORT=443 \
    SEED_LISTEN_PORT=31098 \
    MAINTENANCE_INTERVAL=10

CMD ["python3", "/app/panel.py"]
