#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5001}"

if [[ -x "${ROOT_DIR}/.venv/bin/gunicorn" ]]; then
  GUNICORN_BIN="${ROOT_DIR}/.venv/bin/gunicorn"
elif command -v gunicorn >/dev/null 2>&1; then
  GUNICORN_BIN="$(command -v gunicorn)"
else
  echo "Error: gunicorn not found in .venv/bin or PATH. Install with: pip install -e '.[deploy]'" >&2
  exit 1
fi

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "Error: python not found in .venv/bin or PATH." >&2
  exit 1
fi

if [[ -z "${GITHUB_CLIENT_ID:-}" || -z "${GITHUB_CLIENT_SECRET:-}" ]]; then
  echo "Error: GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET must be set." >&2
  exit 1
fi

if [[ -n "${PLANNER_DOMAIN:-}" ]]; then
  if command -v caddy >/dev/null 2>&1; then
    CADDY_BIN="$(command -v caddy)"
  elif [[ -x /opt/homebrew/bin/caddy ]]; then
    CADDY_BIN="/opt/homebrew/bin/caddy"
  else
    echo "Error: caddy not found on PATH or at /opt/homebrew/bin/caddy." >&2
    exit 1
  fi

  export COOKIE_SECURE=1
  export TRUST_PROXY=1
  export OAUTH_REDIRECT_URI="${OAUTH_REDIRECT_URI:-https://${PLANNER_DOMAIN}/auth/callback}"
  export UPSTREAM_ADDR="${HOST}:${PORT}"

  if [[ -z "${SECRET_KEY:-}" ]]; then
    echo "Warning: SECRET_KEY is not set; generating an ephemeral key. Sessions will not survive restarts unless SECRET_KEY is persisted." >&2
    SECRET_KEY="$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  export SECRET_KEY

  gunicorn_pid=""
  caddy_pid=""
  cleanup() {
    trap - EXIT INT TERM
    if [[ -n "${caddy_pid}" ]] && kill -0 "${caddy_pid}" 2>/dev/null; then
      kill "${caddy_pid}" 2>/dev/null || true
      wait "${caddy_pid}" 2>/dev/null || true
    fi
    if [[ -n "${gunicorn_pid}" ]] && kill -0 "${gunicorn_pid}" 2>/dev/null; then
      kill "${gunicorn_pid}" 2>/dev/null || true
      wait "${gunicorn_pid}" 2>/dev/null || true
    fi
  }
  trap cleanup EXIT
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM

  "${GUNICORN_BIN}" -w 1 --threads 4 -b "${HOST}:${PORT}" 'holiday_planner:create_app()' &
  gunicorn_pid=$!

  "${CADDY_BIN}" run --config Caddyfile &
  caddy_pid=$!
  wait "${caddy_pid}"
else
  export COOKIE_SECURE=0
  export TRUST_PROXY=0
  export OAUTH_REDIRECT_URI="${OAUTH_REDIRECT_URI:-http://${HOST}:${PORT}/auth/callback}"

  if [[ -z "${SECRET_KEY:-}" ]]; then
    SECRET_KEY="$("${PYTHON_BIN}" -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  export SECRET_KEY

  exec "${GUNICORN_BIN}" -w 1 --threads 4 -b "${HOST}:${PORT}" 'holiday_planner:create_app()'
fi
