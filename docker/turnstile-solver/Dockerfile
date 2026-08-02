# syntax=docker/dockerfile:1.6
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=5072 \
    THREAD=2 \
    BROWSER_TYPE=chromium \
    PLAYWRIGHT_BROWSERS_PATH=/opt/patchright-browsers \
    XDG_CACHE_HOME=/data/cache

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates dumb-init \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install -r /app/requirements.txt \
 && python -m patchright install --with-deps chromium

# Camoufox uses Firefox's GTK integration even in headless mode. Keep this in
# a separate layer so browser downloads remain cached when runtime libs change.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgtk-3-0 \
 && rm -rf /var/lib/apt/lists/*

COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
 && mkdir -p /opt/patchright-browsers /data/cache

EXPOSE 5072
VOLUME ["/data"]
ENTRYPOINT ["dumb-init", "--", "/entrypoint.sh"]
