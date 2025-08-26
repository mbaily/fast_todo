#!/usr/bin/env bash
# Backfill recurrence metadata wrapper
# Usage:
#   tools/backfill_recurrence.sh --help
#   tools/backfill_recurrence.sh [--dry-run] [--batch N]
#
# This script prefers the project's `.venv/bin/python` if present, otherwise
# falls back to `python` on PATH. It invokes `tools/backfill_recurrence.py` and
# forwards any additional args. Designed for interactive admin use.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${HERE}/.."
VENV_PY="${REPO_ROOT}/.venv/bin/python"
PY="python"
if [ -x "$VENV_PY" ]; then
  PY="$VENV_PY"
fi

SCRIPT="$REPO_ROOT/tools/backfill_recurrence.py"
if [ ! -f "$SCRIPT" ]; then
  echo "backfill script not found: $SCRIPT" >&2
  exit 2
fi

usage() {
  cat <<'USAGE'
Backfill recurrence metadata for todos.

Usage:
  tools/backfill_recurrence.sh [--help] [--dry-run] [--batch N]

Options:
  --help      Show this message
  --dry-run   Run the script but do not commit changes (script-level dry-run not implemented; for now just runs normally)
  --batch N   Pass batch size N to the Python backfill script (if supported)

Example:
  tools/backfill_recurrence.sh --batch 200
USAGE
}

if [ "$#" -eq 0 ]; then
  echo "Running backfill with default options..."
  exec "$PY" "$SCRIPT"
fi

case "$1" in
  --help|-h)
    usage
    exit 0
    ;;
  *)
    # forward all args
    exec "$PY" "$SCRIPT" "$@"
    ;;
esac
