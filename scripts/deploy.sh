#!/usr/bin/env bash
set -euo pipefail

# Deploy tracked repo files to a local or remote server path using rsync.
# When run on the server (no user@host target) this will deploy locally and chown files to www-data:www-data.
# Usage examples:
#  Local deploy to default path (run on server):
#    sudo ./scripts/deploy.sh
#  Local deploy to custom path:
#    sudo ./scripts/deploy.sh /opt/fast_todo
#  Remote deploy from a checkout to remote host:
#    ./scripts/deploy.sh deploy@server.example.com /opt/fast_todo
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
  REMOTE_PATH="${2:-/opt/fast_todo}"
  shift 2 || true
else
  MODE="local"
  REMOTE_PATH="${1:-/opt/fast_todo}"
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

# Special-case: do not overwrite app/config.py on target. If the target already
# contains app/config.py, we'll avoid sending it and instead write the local
# copy to app/config.py.release on the target so operators can inspect and
# manually merge. If the target does not have app/config.py, allow rsync to
# install it normally.
#
# We implement this by testing presence of $REMOTE_PATH/app/config.py on the
# destination before rsync. If present, we add an --exclude for that path to
# the rsync options and after the transfer copy the local file to
# app/config.py.release on the target (or write locally when MODE=local).

CONFIG_PATH_REL="app/config.py"


# NOTE: .tls/ is intentionally NOT added to the transfer list so existing TLS
# certificates on the target are preserved and not overwritten by deploy.

if [ "$MODE" = "local" ]; then
  echo "Preparing local deploy to: $REMOTE_PATH"
  # Check if destination already has app/config.py
  DEST_CONFIG="$REMOTE_PATH/$CONFIG_PATH_REL"
  if [ -f "$DEST_CONFIG" ]; then
    echo "Target already has $CONFIG_PATH_REL — will not overwrite."
    # Add an exclude so rsync won't overwrite target config
    RSYNC_BASE_OPTS+=(--exclude="$CONFIG_PATH_REL")
    COPY_CONFIG_RELEASE_LOCAL=1
  else
    COPY_CONFIG_RELEASE_LOCAL=0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo rsync --dry-run "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
    rsync --dry-run "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
    if [ "$COPY_CONFIG_RELEASE_LOCAL" -eq 1 ]; then
      echo "(dry-run) Would write local app/config.py to $REMOTE_PATH/$CONFIG_PATH_REL.release"
    fi
  else
    mkdir -p "$REMOTE_PATH"
    rsync "${RSYNC_BASE_OPTS[@]}" "$REMOTE_PATH"
    # If target had an existing config, write local copy to .release
    if [ "$COPY_CONFIG_RELEASE_LOCAL" -eq 1 ] && [ -f "./$CONFIG_PATH_REL" ]; then
      echo "Writing local $CONFIG_PATH_REL to $REMOTE_PATH/$CONFIG_PATH_REL.release"
      cp "./$CONFIG_PATH_REL" "$REMOTE_PATH/$CONFIG_PATH_REL.release"
      chown --from=$(id -u):$(id -g) "$OWNER" "$REMOTE_PATH/$CONFIG_PATH_REL.release" 2>/dev/null || true
      # ensure owner for release file matches desired owner if possible
      if [ -n "$OWNER" ]; then
        chown "$OWNER" "$REMOTE_PATH/$CONFIG_PATH_REL.release" || true
      fi
    fi
    echo "Setting owner to $OWNER for $REMOTE_PATH"
    chown -R "$OWNER" "$REMOTE_PATH"
    # Install Python dependencies on local target if requirements.txt exists
    if [ -f "$REMOTE_PATH/requirements.txt" ]; then
      echo "Installing Python packages from $REMOTE_PATH/requirements.txt"
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "# Would create venv if missing: python3 -m venv $REMOTE_PATH/.venv"
        echo "# Would install: $REMOTE_PATH/.venv/bin/python -m pip install -r $REMOTE_PATH/requirements.txt"
      else
        # Try to use system python3; prefer a venv in the project if present
        if [ -d "$REMOTE_PATH/.venv" ]; then
          echo "Using existing venv at $REMOTE_PATH/.venv to install requirements"
          "$REMOTE_PATH/.venv/bin/python" -m pip install -r "$REMOTE_PATH/requirements.txt"
        else
          echo "No venv found at $REMOTE_PATH/.venv — creating one now"
          if command -v python3 >/dev/null 2>&1; then
            python3 -m venv "$REMOTE_PATH/.venv"
            # ensure pip/setuptools/wheel are up-to-date then install requirements
            "$REMOTE_PATH/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
            "$REMOTE_PATH/.venv/bin/python" -m pip install -r "$REMOTE_PATH/requirements.txt"
            # ensure venv files are owned by desired owner
            if [ -n "$OWNER" ]; then
              echo "Setting owner to $OWNER for $REMOTE_PATH/.venv"
              chown -R "$OWNER" "$REMOTE_PATH/.venv"
            fi
          else
            echo "Error: python3 not available to create virtualenv at $REMOTE_PATH/.venv. Aborting deploy." >&2
            exit 5
          fi
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
    # Check remote config presence for dry-run reporting
    echo "# Checking remote for $CONFIG_PATH_REL"
    echo ssh $SSH_OPTS "$TARGET" "test -f '$REMOTE_PATH/$CONFIG_PATH_REL' && echo exists || echo missing"
  else
    ssh $SSH_OPTS "$TARGET" "mkdir -p '$REMOTE_PATH' && chmod 755 '$REMOTE_PATH'"
    # Test whether remote already has app/config.py
    if ssh $SSH_OPTS "$TARGET" "test -f '$REMOTE_PATH/$CONFIG_PATH_REL'"; then
      echo "Remote target already has $CONFIG_PATH_REL — will not overwrite."
      RSYNC_BASE_OPTS+=(--exclude="$CONFIG_PATH_REL")
      REMOTE_HAS_CONFIG=1
    else
      REMOTE_HAS_CONFIG=0
    fi

    rsync -e "ssh $SSH_OPTS" "${RSYNC_BASE_OPTS[@]}" "$TARGET:$REMOTE_PATH"
    # If remote had config, copy local file to .release on remote
    if [ "$REMOTE_HAS_CONFIG" -eq 1 ] && [ -f "./$CONFIG_PATH_REL" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "(dry-run) Would copy ./$(basename "$CONFIG_PATH_REL") to $TARGET:$REMOTE_PATH/$CONFIG_PATH_REL.release"
      else
  echo "Copying local $CONFIG_PATH_REL to remote $REMOTE_PATH/$CONFIG_PATH_REL.release using rsync"
  rsync -e "ssh $SSH_OPTS" --archive --compress "./$CONFIG_PATH_REL" "$TARGET:$REMOTE_PATH/$CONFIG_PATH_REL.release"
  ssh $SSH_OPTS "$TARGET" "if [ -n '$OWNER' ]; then chown '$OWNER' '$REMOTE_PATH/$CONFIG_PATH_REL.release' || true; fi"
      fi
    fi
    if [ -n "$OWNER" ]; then
      echo "Setting owner to $OWNER on remote"
      ssh $SSH_OPTS "$TARGET" "chown -R '$OWNER' '$REMOTE_PATH'"
    fi
    # Install Python dependencies on remote target if requirements.txt exists
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "# Remote dry-run: would ensure venv then install requirements on $TARGET:$REMOTE_PATH"
      echo "ssh $SSH_OPTS $TARGET \"test -f '$REMOTE_PATH/requirements.txt' && ( test -d '$REMOTE_PATH/.venv' || python3 -m venv '$REMOTE_PATH/.venv' ) && '$REMOTE_PATH/.venv/bin/python' -m pip install -r '$REMOTE_PATH/requirements.txt' || echo no requirements\""
    else
          ssh $SSH_OPTS "$TARGET" "if [ -f '$REMOTE_PATH/requirements.txt' ]; then
        if [ -d '$REMOTE_PATH/.venv' ]; then
          echo 'Using venv at $REMOTE_PATH/.venv to install requirements'
          '$REMOTE_PATH/.venv/bin/python' -m pip install -r '$REMOTE_PATH/requirements.txt'
        else
          echo 'No venv at $REMOTE_PATH/.venv on remote host — creating one now'
          if command -v python3 >/dev/null 2>&1; then
            python3 -m venv '$REMOTE_PATH/.venv'
            '$REMOTE_PATH/.venv/bin/python' -m pip install --upgrade pip setuptools wheel
            '$REMOTE_PATH/.venv/bin/python' -m pip install -r '$REMOTE_PATH/requirements.txt'
            if [ -n "'$OWNER'" ]; then
              echo 'Setting owner to $OWNER for $REMOTE_PATH/.venv on remote host'
              chown -R "'$OWNER'" '$REMOTE_PATH/.venv'
            fi
          else
            echo 'Error: python3 not found on remote host. Cannot create venv.' >&2
            exit 5
          fi
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
