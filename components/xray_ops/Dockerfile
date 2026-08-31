# syntax=docker/dockerfile:1.7

ARG CODEX_CLI_VERSION=0.145.0

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
ARG CODEX_CLI_VERSION=0.145.0
# The `codex` release asset is a dotslash manifest. Extract the actual
# statically linked Linux binary from the platform package instead; the
# reporter image intentionally does not install the dotslash runtime.
ADD https://github.com/openai/codex/releases/download/rust-v${CODEX_CLI_VERSION}/codex-package-x86_64-unknown-linux-musl.tar.zst /tmp/codex-package.tar.zst
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap git openssh-client tar zstd \
    && mkdir -p /opt/codex \
    && tar --zstd -xf /tmp/codex-package.tar.zst -C /opt/codex \
    && test -x /opt/codex/bin/codex \
    && ln -s /opt/codex/bin/codex /usr/local/bin/codex \
    && rm -f /tmp/codex-package.tar.zst \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/codex --version

ENTRYPOINT ["python", "-m", "components.xray_ops.reporter"]
CMD ["serve"]
