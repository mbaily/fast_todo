usr/bin/env bash
set -euo pipefail

# Deploy tracked repo files to a local or remote server path using rsync.
# When run on the server (no user@host target) this will deploy locally and chown files to www-data:www-data.
# Usage examples:
#  Local deploy to default path (run on server):
#    sudo ./scripts/deploy.sh
#  Local deploy to custom path:
#    sudo ./scripts/deploy.sh /opt/gpt5_fast_todo
#  Remote deploy from a checkout to remote host:
#    ./scripts/deploy.sh deploy@server.example.com /opt/gpt5_fast_todo
# Options:
#    --dry-run            show what would be copied
#    --owner=user:group   owner to apply (default: www-data:www-data)

SSH_PORT=${SSH_PORT:-22}
DRY_RUN=0
OWNER="www-data:www-data"
RESTART_CMD=""

# Tip: for installing a systemd service on the target host, see
# scripts/install_systemd_service.sh which renders
# systemd/fast_todo.service.template and installs/starts the unit.

print_usage(){
  sed -n '1,200p' "$0"
}

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  print_usage
  exit 0
fi

if [[ ${1:-} == --dry-run ]]; then
  DRY_RUN=1
  shift || true
fi

# parse owner option early if provided
if [[ ${1:-} == --owner=* ]]; then
  OWNER="${1#--owner=}"
  shift || true
fi

# Basic argument parsing: if first arg contains an @, treat as remote target; otherwise local path (or none -> default)
MODE="local"
if [ $# -ge 1 ] && [[ "$1" == *"@"* ]]; then
  MODE="remote"
  TARGET="$1"
  REMOTE_PATH="${2:-/opt/gpt5_fast_todo}"
  shift 2 || true
else
  MODE="local"
  REMOTE_PATH="${1:-/opt/gpt5_fast_todo}"
  shift || true
fi

# parse remaining args for restart-cmd or dry-run/owner
for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN=1 ;;
    --owner=*) OWNER="${arg#--owner=}" ;;
    --restart-cmd=*) RESTART_CMD="${arg#--restart-cmd=}" ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

echo "Mode: $MODE"
echo "Destination: $REMOTE_PATH"
echo "Owner: $OWNER"
if [ "$DRY_RUN" -eq 1 ]; then echo "(dry-run)"; fi

# ensure we are in a git repository
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Error: not in a git repository. Run this script from a git checkout." >&2
  exit 3
fi

# build list of files to transfer (only tracked files)
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT
git ls-files > "$TMPFILE"

if [ ! -s "$TMPFILE" ]; then
  echo "No tracked files found; aborting." >&2
  exit 4
fi

# Exclude .tls/ so TLS files on the destination are preserved and not overwritten by deploy.
RSYNC_BASE_OPTS=(--archive --compress --human-readable --links --perms --times --delete --files-from="$TMPFILE" --exclude='.git' --exclude='.tls/' --exclude='.certs/' --exclude='debug_logs/' --exclude='*.db' --exclude='*.sqlite' --exclude='*.sqlite3' ./)

# NOTE: .tls/ is intentionally NOT added to the transfer list so existing TLS
# certificates on the target are preserved and not overwritten by deploy.

if [ "$MODE" = "local" ]; then
  echo "Preparing local deploy to: $REMOTE_PATH"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo rsync --dry-run "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
    rsync --dry-run "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
  else
    mkdir -p "$REMOTE_PATH"
    rsync "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
    echo "Setting owner to $OWNER for $REMOTE_PATH"
    chown -R "$OWNER" "$REMOTE_PATH"
    # Install Python dependencies on local target if requirements.txt exists
    if [ -f "$REMOTE_PATH/requirements.txt" ]; then
      echo "Installing Python packages from $REMOTE_PATH/requirements.txt"
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "pip install -r $REMOTE_PATH/requirements.txt"
      else
        # Try to use system python3; prefer a venv in the project if present
        if [ -d "$REMOTE_PATH/.venv" ]; then
          echo "Using existing venv at $REMOTE_PATH/.venv to install requirements"
          "$REMOTE_PATH/.venv/bin/python" -m pip install -r "$REMOTE_PATH/requirements.txt"
        else
          echo "Error: virtualenv not found at $REMOTE_PATH/.venv. Aborting deploy.\nCreate a venv at that location or run a deploy that provisions one." >&2
          exit 5
        fi
      fi
    else
      echo "No requirements.txt found in deployed path; skipping pip install."
    fi
  fi
else
  echo "Preparing remote deploy to: $TARGET:$REMOTE_PATH"
  SSH_OPTS="-p ${SSH_PORT} -o StrictHostKeyChecking=accept-new"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo ssh $SSH_OPTS "$TARGET" "mkdir -p '$REMOTE_PATH' && chmod 755 '$REMOTE_PATH'"
    echo rsync --dry-run -e "ssh $SSH_OPTS" "${RSYNC_BASE_OPTS[@]}" "$TARGET:$REMOTE_PATH"
    rsync --dry-run -e "ssh $SSH_OPTS" "${RSYNC_BASE_OPTS[@]}" "$TARGET:$REMOTE_PATH"
  else
    ssh $SSH_OPTS "$TARGET" "mkdir -p '$REMOTE_PATH' && chmod 755 '$REMOTE_PATH'"
    rsync -e "ssh $SSH_OPTS" "${RSYNC_BASE_OPTS[@]}" "$TARGET:$REMOTE_PATH"
    if [ -n "$OWNER" ]; then
      echo "Setting owner to $OWNER on remote"
      ssh $SSH_OPTS "$TARGET" "chown -R '$OWNER' '$REMOTE_PATH'"
    fi
    # Install Python dependencies on remote target if requirements.txt exists
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "ssh $SSH_OPTS $TARGET 'test -f "$REMOTE_PATH/requirements.txt" && echo pip install -r $REMOTE_PATH/requirements.txt || echo no requirements'"
    else
      ssh $SSH_OPTS "$TARGET" "if [ -f '$REMOTE_PATH/requirements.txt' ]; then
        if [ -d '$REMOTE_PATH/.venv' ]; then
          echo 'Using venv at $REMOTE_PATH/.venv to install requirements'
          '$REMOTE_PATH/.venv/bin/python' -m pip install -r '$REMOTE_PATH/requirements.txt'
        else
          echo 'Error: virtualenv not found at $REMOTE_PATH/.venv on remote host. Aborting deploy.' >&2
          exit 5
        fi
      else
        echo 'No requirements.txt at $REMOTE_PATH; skipping pip install'
      fi"
    fi
  fi
fi

echo "Deploy finished."

if [ -n "$RESTART_CMD" ]; then
  echo "Executing restart command: $RESTART_CMD"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "$RESTART_CMD"
  else
    if [ "$MODE" = "local" ]; then
      eval "$RESTART_CMD"
    else
      ssh $SSH_OPTS "$TARGET" "$RESTART_CMD"
    fi
  fi
fi

echo "Done."
