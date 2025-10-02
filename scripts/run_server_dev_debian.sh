#!/usr/bin/env bash
set -euo pipefail

# run_server_debian.sh
# Debian-friendly helper to create a venv, install dependencies, ensure SECRET_KEY,
# and run the FastAPI app with uvicorn.
# Usage:
#   scripts/run_server_debian.sh         # install and run server (production mode)
#   SECRET_KEY=yourkey scripts/run_server_debian.sh   # run with explicit secret
#   scripts/run_server_debian.sh --dev   # run with reload for development

RELOAD=1    # New default as this script only used for dev
DEBUGPY=0
DEBUGPY_WAIT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dev)
      RELOAD=1
      shift
      ;;
    --debug)
      DEBUGPY=1
      shift
      ;;
    --debug-wait)
      DEBUGPY=1
      DEBUGPY_WAIT=1
      shift
      ;;
    *)
      # unknown arg: ignore (preserve backwards compat)
      shift
      ;;
  esac
done

PY=python3
VENV_DIR=".venv"
REQ_FILE_DOC="requirements_server.txt"
REQ_FILE="requirements.txt"

PIP_PACKAGES=(
  fastapi
  "uvicorn[standard]"
  sqlmodel
  aiosqlite
  "passlib[bcrypt]"
  "python-jose[cryptography]"
  httpx
)

echo "[run_server] Using Python: $($PY --version 2>&1)"

# Ensure python3 is available
if ! command -v $PY >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.x (Debian: apt install python3 python3-venv python3-pip)" >&2
  exit 2
fi

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
  echo "[run_server] Creating virtualenv in $VENV_DIR"
  uv venv "$VENV_DIR"
fi

# Activate venv
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# Upgrade pip
#python -m pip install --upgrade pip setuptools wheel >/dev/null

# Install dependencies: prefer a real requirements file if it's valid; otherwise use known set
#if [ -f "$REQ_FILE" ]; then
#  echo "[run_server] Installing from $REQ_FILE"
#  uv pip install -r "$REQ_FILE"
#elif [ -f "$REQ_FILE_DOC" ]; then
#  # fallback: the documented requirements file may be human-readable; try to detect
#  if grep -E '^[a-zA-Z0-9_.-]+' "$REQ_FILE_DOC" | grep -q -v '^```' ; then
#    echo "[run_server] Installing from $REQ_FILE_DOC"
#    uv pip install -r "$REQ_FILE_DOC"
#  else
#    echo "[run_server] $REQ_FILE_DOC appears to be documentation; installing common packages instead"
#     uv pi install --no-cache-dir "${PIP_PACKAGES[@]}"
#  fi
#else
#  echo "[run_server] No requirements file found; installing common packages"
#  uv pip install --no-cache-dir "${PIP_PACKAGES[@]}"
#fi


ENV_FILE_SYSTEM="/etc/default/fast_todo"
ENV_FILE_LOCAL="fast_todo.env"

# Load SECRET_KEY (and optionally CSRF_VERIFY_KEYS) from environment files.
# Priority: system env file (if you run as a service) -> repo-local fast_todo.env.
if [ -f "$ENV_FILE_SYSTEM" ]; then
  SECRET_KEY=$(grep -E '^SECRET_KEY=' "$ENV_FILE_SYSTEM" | head -n1 | cut -d= -f2- || true)
  CSRF_VERIFY_KEYS=$(grep -E '^CSRF_VERIFY_KEYS=' "$ENV_FILE_SYSTEM" | head -n1 | cut -d= -f2- || true)
  SECRET_KEY=${SECRET_KEY#\"}; SECRET_KEY=${SECRET_KEY%\"}
  CSRF_VERIFY_KEYS=${CSRF_VERIFY_KEYS#\"}; CSRF_VERIFY_KEYS=${CSRF_VERIFY_KEYS%\"}
  export SECRET_KEY CSRF_VERIFY_KEYS
fi

if [ -z "${SECRET_KEY-}" ] && [ -f "$ENV_FILE_LOCAL" ]; then
  SECRET_KEY=$(grep -E '^SECRET_KEY=' "$ENV_FILE_LOCAL" | head -n1 | cut -d= -f2- || true)
  CSRF_VERIFY_KEYS=$(grep -E '^CSRF_VERIFY_KEYS=' "$ENV_FILE_LOCAL" | head -n1 | cut -d= -f2- || true)
  SECRET_KEY=${SECRET_KEY#\"}; SECRET_KEY=${SECRET_KEY%\"}
  CSRF_VERIFY_KEYS=${CSRF_VERIFY_KEYS#\"}; CSRF_VERIFY_KEYS=${CSRF_VERIFY_KEYS%\"}
  export SECRET_KEY CSRF_VERIFY_KEYS
fi

# Ensure SECRET_KEY is set in the environment
if [ -z "${SECRET_KEY-}" ]; then
  echo "[run_server] SECRET_KEY not set; generating and persisting to $ENV_FILE_LOCAL"
  SECRET_KEY_VAL=$(python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
  export SECRET_KEY="$SECRET_KEY_VAL"
  # Write to repo-local env file so dev restarts reuse the same key
  {
    echo "SECRET_KEY=$SECRET_KEY_VAL"
    # Preserve existing CSRF_VERIFY_KEYS if present, otherwise leave blank
    if [ -n "${CSRF_VERIFY_KEYS-}" ]; then echo "CSRF_VERIFY_KEYS=$CSRF_VERIFY_KEYS"; fi
  } > "$ENV_FILE_LOCAL"
  echo "[run_server] Wrote SECRET_KEY to $ENV_FILE_LOCAL (do not commit this file)."
fi

# Ensure DATABASE_URL is set (default to local fast_todo.db with aiosqlite)
#export DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db.server_copy"
#export DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db"
if [ -z "${DATABASE_URL-}" ]; then
  export DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db"
  echo "[run_server] DATABASE_URL not set; defaulting to $DATABASE_URL"
fi

# Start uvicorn
HOST=${HOST-0.0.0.0}
PORT=${PORT-10443}
APP_MODULE=${APP_MODULE-app.main:app}

# Logging: optional log file capture. If LOG_FILE is set, stdout/stderr
# will be redirected to that file. Default: server.log in project root
LOG_FILE=${LOG_FILE-server.log}

# Uvicorn log level and access log control
UVICORN_LOG_LEVEL=${UVICORN_LOG_LEVEL-info}
UVICORN_ACCESS_LOG=${UVICORN_ACCESS_LOG-true}

# Enforce HTTPS using a self-signed cert (for local/dev usage)
CERT_DIR=".certs"
CERT_KEY="$CERT_DIR/privkey.pem"
CERT_PUB="$CERT_DIR/fullchain.pem"

if ! command -v openssl >/dev/null 2>&1; then
  echo "ERROR: openssl is required to generate a self-signed certificate for HTTPS. Install with: sudo apt install openssl" >&2
  exit 3
fi

mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_KEY" ] || [ ! -f "$CERT_PUB" ]; then
  echo "[run_server] Generating self-signed certificate in $CERT_DIR"
  # generate a simple self-signed cert (2048-bit key, valid 365 days)
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$CERT_KEY" -out "$CERT_PUB" -subj "/CN=localhost"
fi

if [ "$RELOAD" -eq 1 ]; then
  echo "[run_server] Starting uvicorn in dev mode (reload) on https://${HOST}:${PORT} (log_level=${UVICORN_LOG_LEVEL} access_log=${UVICORN_ACCESS_LOG})"
  # Mark this process as development so templates can show a banner
  export DEV_MODE=1
  if [ "$DEBUGPY" -eq 1 ]; then
    echo "[run_server] Enabling debugpy (wait=${DEBUGPY_WAIT})"
    export ENABLE_DEBUGPY=1
    export DEBUGPY_PORT=${DEBUGPY_PORT-5678}
    if [ "$DEBUGPY_WAIT" -eq 1 ]; then
      export DEBUGPY_WAIT=1
    fi
  fi
  UVICORN_CMD=(uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" --reload --ssl-keyfile "$CERT_KEY" --ssl-certfile "$CERT_PUB" --log-level "$UVICORN_LOG_LEVEL")
  if [ "$UVICORN_ACCESS_LOG" != "true" ]; then
    UVICORN_CMD+=(--access-log "false")
  fi
  if [ -n "$LOG_FILE" ]; then
    # ensure logs directory exists
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "[run_server] Redirecting output to $LOG_FILE"
    exec "${UVICORN_CMD[@]}" >> "$LOG_FILE" 2>&1
  else
    exec "${UVICORN_CMD[@]}"
  fi
else
  echo "[run_server] Starting uvicorn on https://${HOST}:${PORT} (log_level=${UVICORN_LOG_LEVEL} access_log=${UVICORN_ACCESS_LOG})"
  UVICORN_CMD=(uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" --workers 1 --ssl-keyfile "$CERT_KEY" --ssl-certfile "$CERT_PUB" --log-level "$UVICORN_LOG_LEVEL")
  if [ "$UVICORN_ACCESS_LOG" != "true" ]; then
    UVICORN_CMD+=(--access-log "false")
  fi
  if [ -n "$LOG_FILE" ]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    echo "[run_server] Redirecting output to $LOG_FILE"
    exec "${UVICORN_CMD[@]}" >> "$LOG_FILE" 2>&1
  else
    exec "${UVICORN_CMD[@]}"
  fi
fi
