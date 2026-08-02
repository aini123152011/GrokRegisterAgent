#!/usr/bin/env bash
set -euo pipefail

REGISTER_DIR="${REGISTER_DIR:-/app/register}"
REGISTER_HOST_SRC="${REGISTER_HOST_SRC:-/opt/register-host}"
REGISTER_SEED="${REGISTER_SEED:-/opt/register-seed}"

mkdir -p /data
mkdir -p "${SSO_DIR:-/data/sso}"
mkdir -p /data/auth
touch /data/account_tags.json 2>/dev/null || true
mkdir -p "${REGISTER_DIR}/sso" "${REGISTER_DIR}/logs" "${REGISTER_DIR}/data"
mkdir -p /app/register/sso /app/register/logs

register_is_complete() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  [[ -f "$dir/runner.py" || -f "$dir/DrissionPage_example.py" ]]
}

sync_register_from() {
  local src="$1"
  local dst="$2"
  local label="$3"

  if ! register_is_complete "$src"; then
    echo "[entrypoint] skip sync from ${label}: incomplete (need runner.py or DrissionPage_example.py)"
    return 1
  fi

  mkdir -p "$dst"
  echo "[entrypoint] syncing register from ${label} -> ${dst}"

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude 'logs/' \
      --exclude 'sso/' \
      --exclude 'data/' \
      --exclude 'config.json' \
      --exclude 'account_tags.json' \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '.pytest_cache/' \
      --exclude 'bin/sing-box/linux-amd64' \
      --exclude 'bin/sing-box/linux-arm64' \
      --exclude 'bin/sing-box/linux-arm' \
      "${src}/" "${dst}/"
  else
    local sb_tmp
    sb_tmp="$(mktemp -d)"
    for f in linux-amd64 linux-arm64 linux-arm; do
      if [[ -f "${dst}/bin/sing-box/${f}" ]]; then
        mkdir -p "${sb_tmp}/bin/sing-box"
        cp -a "${dst}/bin/sing-box/${f}" "${sb_tmp}/bin/sing-box/${f}" || true
      fi
    done

    find "$dst" -mindepth 1 -maxdepth 1 \
      ! -name 'logs' ! -name 'sso' ! -name 'data' ! -name 'config.json' ! -name 'account_tags.json' \
      -exec rm -rf {} + 2>/dev/null || true

    tar -C "$src" \
      --exclude='logs' \
      --exclude='sso' \
      --exclude='data' \
      --exclude='config.json' \
      --exclude='account_tags.json' \
      --exclude='__pycache__' \
      --exclude='*.pyc' \
      -cf - . | tar -C "$dst" -xf -

    if [[ -d "${sb_tmp}/bin/sing-box" ]]; then
      mkdir -p "${dst}/bin/sing-box"
      for f in linux-amd64 linux-arm64 linux-arm; do
        if [[ -f "${sb_tmp}/bin/sing-box/${f}" && ! -f "${dst}/bin/sing-box/${f}" ]]; then
          cp -a "${sb_tmp}/bin/sing-box/${f}" "${dst}/bin/sing-box/${f}" || true
        fi
      done
    fi
    rm -rf "${sb_tmp}"
  fi

  mkdir -p "${dst}/logs" "${dst}/sso"
  echo "[entrypoint] register sync done from ${label}"
  return 0
}

if register_is_complete "$REGISTER_HOST_SRC"; then
  sync_register_from "$REGISTER_HOST_SRC" "$REGISTER_DIR" "host:${REGISTER_HOST_SRC}" || true
elif ! register_is_complete "$REGISTER_DIR"; then
  echo "[entrypoint] WARN: ${REGISTER_DIR} incomplete"
  if register_is_complete "$REGISTER_SEED"; then
    sync_register_from "$REGISTER_SEED" "$REGISTER_DIR" "seed:${REGISTER_SEED}" || true
  else
    echo "[entrypoint] ERROR: no complete register seed at ${REGISTER_SEED}"
  fi
else
  echo "[entrypoint] using image register at ${REGISTER_DIR} (no host override)"
fi

if register_is_complete "$REGISTER_DIR"; then
  echo "[entrypoint] register ready: $(ls -1 "${REGISTER_DIR}" | tr '\n' ' ')"

  for f in proxy_auth_ext.py proxy_local_forward.py pools.py email_register.py; do
    if [[ -f "${REGISTER_DIR}/${f}" ]]; then
      echo "[entrypoint] OK ${f}"
    else
      echo "[entrypoint] MISSING ${f}; proxy auth / pool rotation may not work"
    fi
  done

  if grep -q '_looks_human_local\|_generate_local_part' "${REGISTER_DIR}/email_register.py" 2>/dev/null; then
    echo "[entrypoint] OK email_register human-local-part patch present"
  else
    echo "[entrypoint] WARN email_register.py may be old; check host register mount and restart"
  fi

  arch="$(uname -m 2>/dev/null || echo x86_64)"
  case "$arch" in
    aarch64|arm64) cfwp_bin="${REGISTER_DIR}/bin/cfwp/linux-arm64" ;;
    *) cfwp_bin="${REGISTER_DIR}/bin/cfwp/linux-amd64" ;;
  esac
  if [[ -f "$cfwp_bin" ]]; then
    chmod +x "$cfwp_bin" 2>/dev/null || true
    echo "[entrypoint] OK cfwp $(basename "$cfwp_bin")"
  else
    echo "[entrypoint] WARN missing cfwp binary at ${cfwp_bin}; cfwp proxy is unavailable"
  fi

  case "$arch" in
    aarch64|arm64) sb_bin="${REGISTER_DIR}/bin/sing-box/linux-arm64" ;;
    *) sb_bin="${REGISTER_DIR}/bin/sing-box/linux-amd64" ;;
  esac
  if [[ -f "$sb_bin" ]]; then
    chmod +x "$sb_bin" 2>/dev/null || true
    echo "[entrypoint] OK sing-box $(basename "$sb_bin")"
  else
    echo "[entrypoint] WARN missing sing-box binary at ${sb_bin}; sing-box proxy is unavailable"
  fi
else
  echo "[entrypoint] ERROR: ${REGISTER_DIR} still incomplete; registration will fail"
fi

if command -v Xvfb >/dev/null 2>&1; then
  export DISPLAY="${DISPLAY:-:99}"
  Xvfb "${DISPLAY}" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
  sleep 0.5
  echo "[entrypoint] Xvfb ready DISPLAY=${DISPLAY}"
fi

if [[ "${AUTH_QUEUE_DAEMON:-1}" == "1" && -f "${REGISTER_DIR}/auth_queue_daemon.py" ]]; then
  export AUTH_QUEUE_DAEMON=1
  export AUTH_QUEUE_DIR="${AUTH_QUEUE_DIR:-/data/auth-queue}"
  mkdir -p "${AUTH_QUEUE_DIR}"
  python3 -u "${REGISTER_DIR}/auth_queue_daemon.py" &
  auth_daemon_pid=$!
  echo "[entrypoint] auth queue daemon started pid=${auth_daemon_pid} dir=${AUTH_QUEUE_DIR}"
else
  echo "[entrypoint] auth queue daemon disabled"
fi

exec node /app/server/dist/server/src/index.js
