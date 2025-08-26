#!/usr/bin/env bash
set -euo pipefail

# Non-interactive installer for the fast_todo systemd service.
# It writes /etc/systemd/system/fast_todo.service and /etc/default/fast_todo,
# then enables+starts the unit. Defaults are configured to be sensible;
# override via environment variables or pass --dry-run to preview.

DRY_RUN=0
ASSUME_YES=0
SERVICE_NAME=${SERVICE_NAME:-gpt5_fast_todo}

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes) ASSUME_YES=1 ;;
    --help|-h) echo "Usage: $0 [--dry-run] [--yes]"; exit 0 ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
template_path="$repo_root/systemd/fast_todo.service.template"

if [ ! -f "$template_path" ]; then
  echo "Error: unit template not found at $template_path" >&2
  exit 2
fi

# Defaults requested by user
WORKDIR=${WORKDIR:-/opt/gpt5_fast_todo}
VENV_BIN=${VENV_BIN:-$WORKDIR/.venv/bin}
PORT=${PORT:-10443}
SSL_CERTFILE=${SSL_CERTFILE:-$WORKDIR/.certs/fullchain.pem}
SSL_KEYFILE=${SSL_KEYFILE:-$WORKDIR/.certs/privkey.pem}
RUN_AS_USER=${RUN_AS_USER:-www-data}
RUN_AS_GROUP=${RUN_AS_GROUP:-www-data}

echo "Installer settings:"
cat <<EOF
WORKDIR=$WORKDIR
VENV_BIN=$VENV_BIN
PORT=$PORT
SSL_CERTFILE=$SSL_CERTFILE
SSL_KEYFILE=$SSL_KEYFILE
RUN_AS_USER=$RUN_AS_USER
RUN_AS_GROUP=$RUN_AS_GROUP
EOF

if [ "$DRY_RUN" -eq 0 ] && [ "$ASSUME_YES" -eq 0 ]; then
  echo "Run with --yes to proceed non-interactively, or with --dry-run to preview." >&2
  exit 0
fi

# Render unit file by substituting ${VAR} placeholders using python for portability
TMP_UNIT=$(mktemp)
python3 - "$template_path" "$WORKDIR" "$VENV_BIN" "$PORT" "$SSL_KEYFILE" "$SSL_CERTFILE" "$RUN_AS_USER" "$RUN_AS_GROUP" "$SERVICE_NAME" > "$TMP_UNIT" <<'PY'
import sys, re
path = sys.argv[1]
workdir = sys.argv[2]
venv = sys.argv[3]
port = sys.argv[4]
key = sys.argv[5]
cert = sys.argv[6]
run_user = sys.argv[7]
run_group = sys.argv[8]
service_name = sys.argv[9]
with open(path) as f:
  s = f.read()
subs = {
  'WORKDIR': workdir,
  'VENV_BIN': venv,
  'PORT': port,
  'SSL_KEYFILE': key,
  'SSL_CERTFILE': cert,
  'RUN_AS_USER': run_user,
  'RUN_AS_GROUP': run_group,
  'SERVICE_NAME': service_name,
}

def repl(m):
  name = m.group(1)
  return subs.get(name, '')

# Replace ${VAR} and ${VAR:-default} patterns
s = re.sub(r"\$\{(\w+)(?:[:][-][^}]*)?\}", repl, s)
sys.stdout.write(s)
PY

# Prepare env file contents
ENV_CONTENT=$(cat <<EOF
WORKDIR=${WORKDIR}
VENV_BIN=${VENV_BIN}
PORT=${PORT}
SSL_CERTFILE=${SSL_CERTFILE}
SSL_KEYFILE=${SSL_KEYFILE}
RUN_AS_USER=${RUN_AS_USER}
RUN_AS_GROUP=${RUN_AS_GROUP}
EOF
)

ENV_FILE="/etc/default/${SERVICE_NAME}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "--- Rendered unit file ---"
  cat "$TMP_UNIT"
  echo "--- $ENV_FILE contents ---"
  echo "$ENV_CONTENT"
  rm -f "$TMP_UNIT"
  exit 0
fi

# Write files (requires root)
if ! command -v sudo >/dev/null 2>&1; then
  echo "Warning: sudo not found. You must run this script as root to write to /etc/systemd and to run systemctl." >&2
  SUDO=""
else
  SUDO="sudo"
fi

echo "Installing unit to /etc/systemd/system/${SERVICE_NAME}.service (this requires sudo)"
cat "$TMP_UNIT" | $SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null

ENV_FILE="/etc/default/${SERVICE_NAME}"
echo "Writing environment file $ENV_FILE"
printf "%s\n" "$ENV_CONTENT" | $SUDO tee "$ENV_FILE" >/dev/null

rm -f "$TMP_UNIT"

echo "Reloading systemd daemon"
$SUDO systemctl daemon-reload

echo "Enabling and starting ${SERVICE_NAME}"
$SUDO systemctl enable --now "${SERVICE_NAME}.service"

echo "Installation complete. Use 'sudo journalctl -u ${SERVICE_NAME}.service -f' to follow logs."

exit 0
