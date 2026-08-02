#!/bin/sh
set -eu
cd /app

backend="${BROWSER_TYPE:-chromium}"
if [ "$backend" = "camoufox" ] && [ "${TURNSTILE_BROWSER_AUTO_FETCH:-1}" != "0" ]; then
  python -m camoufox fetch
fi

exec python api_solver.py \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-5072}" \
  --thread "${THREAD:-2}" \
  --browser-type "$backend"
