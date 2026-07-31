# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS codex-cli

ARG CODEX_CLI_VERSION=0.145.0

RUN npm install --global --omit=dev "@openai/codex@${CODEX_CLI_VERSION}" \
    && codex --version \
    && native_path="$(find /usr/local/lib/node_modules/@openai/codex -type f -path '*/vendor/*/bin/codex' -print -quit)" \
    && test -n "${native_path}" \
    && install -m 0755 "${native_path}" /codex \
    && /codex --version \
    && npm cache clean --force

FROM python:3.11.9-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/xray-ops \
    HOME=/tmp/ops-home

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/xray-ops

WORKDIR /opt/xray-ops
COPY components/__init__.py ./components/__init__.py
COPY components/xray_ops ./components/xray_ops
RUN python -m compileall -q ./components/xray_ops

WORKDIR /tmp

FROM runtime AS collector

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["python", "-m", "components.xray_ops.collector"]
CMD ["run"]

FROM runtime AS reporter

USER root
COPY --from=codex-cli /codex /usr/local/bin/codex
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/*
RUN codex --version

ENTRYPOINT ["python", "-m", "components.xray_ops.reporter"]
CMD ["serve"]
