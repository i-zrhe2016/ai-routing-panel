FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cron \
        ca-certificates \
        docker.io \
        python3 \
        python3-flask \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /app/xray/runtime /app/xray/logs

COPY app /app
COPY scripts /app/scripts

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
