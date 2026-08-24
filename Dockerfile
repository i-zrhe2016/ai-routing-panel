FROM ghcr.io/xtls/xray-core:26.5.3 AS xray-core

FROM debian:bookworm-slim

ARG CODEX_CLI_VERSION=0.145.0

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
# SPA bundles are versioned deployment artifacts. The control plane has no
# frontend build toolchain at runtime.
COPY app/static/admin /app/static/admin
COPY app/static/portal /app/static/portal
COPY app/static/landing /app/static/landing
COPY --from=xray-core /usr/local/bin/xray /usr/local/bin/xray
# The AI-domain manager uses the standalone Codex binary. No Node runtime or
# package manager is needed in the application image.
ADD https://github.com/openai/codex/releases/download/rust-v${CODEX_CLI_VERSION}/codex /usr/local/bin/codex
RUN chmod 0755 /usr/local/bin/codex

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
