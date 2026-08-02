#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/app"
mkdir -p logs

if [[ -f logs/turnstile_solver.pid ]] && kill -0 "$(<logs/turnstile_solver.pid)" 2>/dev/null; then
  echo "solver already running pid=$(<logs/turnstile_solver.pid)"
  exit 0
fi

args=(api_solver.py --host "${HOST:-127.0.0.1}" --port "${PORT:-5072}" --thread "${THREAD:-2}" --browser-type "${BROWSER_TYPE:-chromium}")
[[ "${TURNSTILE_HEADED:-0}" == "1" ]] && args+=(--no-headless)
[[ "${DEBUG:-0}" == "1" ]] && args+=(--debug)
[[ "${PROXY:-1}" == "1" ]] && args+=(--proxy)

nohup python "${args[@]}" >logs/turnstile_solver.log 2>&1 &
echo $! >logs/turnstile_solver.pid
echo "solver started pid=$(<logs/turnstile_solver.pid) log=app/logs/turnstile_solver.log"
