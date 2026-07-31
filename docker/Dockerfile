# syntax=docker/dockerfile:1.6

FROM node:20-bookworm AS builder
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
RUN npm run server:build
RUN npm prune --omit=dev

FROM node:20-bookworm-slim AS node-runtime

FROM python:3.13-slim-bookworm AS runtime

ENV NODE_ENV=production \
    PORT=6657 \
    BIND_HOST=0.0.0.0 \
    DATA_DIR=/data \
    STATIC_ROOT=/app/out/renderer \
    PYTHON_PATH=/usr/local/bin/python3 \
    REGISTER_DIR=/app/register \
    SSO_DIR=/data/sso \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99

WORKDIR /app

# Chromium + Xvfb：容器内跑有头浏览器，供 DrissionPage / Turnstile 流程使用。
# 附带 CJK 字体与 GL 相关库，降低页面渲染/指纹缺失导致的失败率。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      chromium \
      xvfb \
      ca-certificates \
      curl \
      dumb-init \
      fonts-noto-cjk \
      fonts-liberation \
      fonts-dejavu-core \
      libnss3 \
      libatk-bridge2.0-0 \
      libgtk-3-0 \
      libx11-xcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxrandr2 \
      libgbm1 \
      libasound2 \
      libpangocairo-1.0-0 \
      libxshmfence1 \
 && rm -rf /var/lib/apt/lists/*

# /app/register 是运行目录；/opt/register-seed 是宿主热更新源缺失时的恢复种子。
COPY register /app/register
COPY register /opt/register-seed

ARG SING_BOX_VERSION=1.13.14
ARG TARGETARCH
RUN set -eux; \
    python -m pip install --upgrade pip; \
    python -m pip install -r /app/register/requirements.txt; \
    apt-get update; \
    apt-get install -y --no-install-recommends rsync; \
    arch="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "$arch" in amd64|arm64) ;; *) echo "unsupported TARGETARCH=$arch"; exit 1 ;; esac; \
    sb="/app/register/bin/sing-box/linux-${arch}"; \
    if [ ! -f "$sb" ]; then \
      tmp="$(mktemp -d)"; \
      url="https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${arch}.tar.gz"; \
      echo "fetching $url"; \
      curl -fsSL "$url" -o "${tmp}/sing-box.tgz"; \
      tar -xzf "${tmp}/sing-box.tgz" -C "$tmp"; \
      bin="$(find "$tmp" -type f -name sing-box | head -n1)"; \
      test -n "$bin"; \
      mkdir -p /app/register/bin/sing-box /opt/register-seed/bin/sing-box; \
      install -m 755 "$bin" "$sb"; \
      install -m 755 "$bin" "/opt/register-seed/bin/sing-box/linux-${arch}"; \
      rm -rf "$tmp"; \
    elif [ ! -f "/opt/register-seed/bin/sing-box/linux-${arch}" ]; then \
      mkdir -p /opt/register-seed/bin/sing-box; \
      cp -a "$sb" "/opt/register-seed/bin/sing-box/linux-${arch}"; \
    fi; \
    chmod +x /app/register/bin/cfwp/linux-amd64 /app/register/bin/cfwp/linux-arm64 2>/dev/null || true; \
    chmod +x /app/register/bin/sing-box/linux-amd64 /app/register/bin/sing-box/linux-arm64 2>/dev/null || true; \
    chmod +x /opt/register-seed/bin/cfwp/linux-amd64 /opt/register-seed/bin/cfwp/linux-arm64 2>/dev/null || true; \
    chmod +x /opt/register-seed/bin/sing-box/linux-amd64 /opt/register-seed/bin/sing-box/linux-arm64 2>/dev/null || true; \
    rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

COPY --from=builder /app/out /app/out
COPY --from=builder /app/server/dist /app/server/dist
COPY --from=builder /app/node_modules /app/node_modules
COPY --from=builder /app/package.json /app/package.json

ARG REGISTER_BUILD=unknown
ENV REGISTER_BUILD=$REGISTER_BUILD
RUN printf '%s\n' "$REGISTER_BUILD" > /app/register/BUILD_ID \
 && printf '%s\n' "$REGISTER_BUILD" > /opt/register-seed/BUILD_ID || true

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data /data/sso /app/register/sso /app/register/logs \
 && mkdir -p /opt/register-host /opt/register-seed

EXPOSE 6657
VOLUME ["/data"]

ENTRYPOINT ["dumb-init", "--", "/usr/local/bin/entrypoint.sh"]
