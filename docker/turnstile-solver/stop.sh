#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/app"
pid_file=logs/turnstile_solver.pid
if [[ ! -f "$pid_file" ]]; then
  echo "solver is not running"
  exit 0
fi
pid="$(<"$pid_file")"
kill "$pid" 2>/dev/null || true
rm -f "$pid_file"
echo "solver stopped pid=$pid"
