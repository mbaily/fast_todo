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
  $PY -m venv "$VENV_DIR"
fi

# Activate venv
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

# Upgrade pip
python -m pip install --upgrade pip setuptools wheel >/dev/null

# Install dependencies: prefer a real requirements file if it's valid; otherwise use known set
if [ -f "$REQ_FILE" ]; then
  echo "[run_server] Installing from $REQ_FILE"
  python -m pip install -r "$REQ_FILE"
elif [ -f "$REQ_FILE_DOC" ]; then
  # fallback: the documented requirements file may be human-readable; try to detect
  if grep -E '^[a-zA-Z0-9_.-]+' "$REQ_FILE_DOC" | grep -q -v '^```' ; then
    echo "[run_server] Installing from $REQ_FILE_DOC"
    python -m pip install -r "$REQ_FILE_DOC"
  else
    echo "[run_server] $REQ_FILE_DOC appears to be documentation; installing common packages instead"
    python -m pip install --no-cache-dir "${PIP_PACKAGES[@]}"
  fi
else
  echo "[run_server] No requirements file found; installing common packages"
  python -m pip install --no-cache-dir "${PIP_PACKAGES[@]}"
fi


ENV_FILE="/etc/default/gpt5_fast_todo"

# Load SECRET_KEY only, ignore the rest (avoid sourcing untrusted code)
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  SECRET_KEY=$(grep -E '^SECRET_KEY=' "$ENV_FILE" | head -n1 | cut -d= -f2-)
  # Remove surrounding quotes if present
  SECRET_KEY=${SECRET_KEY#\"}
  SECRET_KEY=${SECRET_KEY%\"}
  export SECRET_KEY
else
  echo "Warning: $ENV_FILE not found; SECRET_KEY not set" >&2
fi

# Ensure SECRET_KEY is set in the environment
if [ -z "${SECRET_KEY-}" ]; then
  echo "[run_server] SECRET_KEY not set; generating a temporary one for this session"
  SECRET_KEY_VAL=$(python - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
  export SECRET_KEY="$SECRET_KEY_VAL"
  echo "[run_server] Generated SECRET_KEY (exported for this process). For production, set SECRET_KEY in the service environment."
fi

# Ensure DATABASE_URL is set (default to local test.db with aiosqlite)
#export DATABASE_URL="sqlite+aiosqlite:///./test.db.server_copy"
export DATABASE_URL="sqlite+aiosqlite:///./test.db"
if [ -z "${DATABASE_URL-}" ]; then
  #export DATABASE_URL="sqlite+aiosqlite:///./test.db.server_copy"
  echo "[run_server] DATABASE_URL not set; defaulting to $DATABASE_URL"
fi

# Start uvicorn
HOST=${HOST-0.0.0.0}
PORT=${PORT-10443}
APP_MODULE=${APP_MODULE-app.main:app}

# Logging: optional log file capture. If LOG_FILE is set, stdout/stderr
# will be redirected to that file. Default: no redirection (prints to console).
LOG_FILE=${LOG_FILE-}

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
