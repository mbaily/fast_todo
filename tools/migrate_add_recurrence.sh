#!/usr/bin/env bash
# Simple DB migration wrapper to add recurrence columns to `todo` table for
# SQLite databases. This is intended for small deployments and development
# environments. For production, prefer a proper migration tool like Alembic.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${HERE}/.."
VENV_PY="${REPO_ROOT}/.venv/bin/python"
PY="python"
if [ -x "$VENV_PY" ]; then
  PY="$VENV_PY"
fi

usage() {
  cat <<'USAGE'
Usage: tools/migrate_add_recurrence.sh [--help] --db <sqlite_db_path> [--dry-run]

This script will add the following columns to the `todo` table if missing:
  - recurrence_rrule TEXT
  - recurrence_meta TEXT
  - recurrence_dtstart DATETIME
  - recurrence_parser_version TEXT

Only SQLite is supported by this helper. For other DBs, run a controlled
schema migration with your team's tooling (Alembic, liquibase, etc.).
USAGE
}

DB_PATH=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage; exit 0;;
    --db)
      shift; DB_PATH="$1"; shift;;
    --dry-run)
      DRY_RUN=1; shift;;
    *)
      echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [ -z "$DB_PATH" ]; then
  echo "--db <sqlite_db_path> is required" >&2
  usage
  exit 2
fi

if [ ! -f "$DB_PATH" ]; then
  echo "DB file not found: $DB_PATH" >&2
  exit 2
fi

echo "Inspecting SQLite DB: $DB_PATH"

SQLITE3_BIN=$(command -v sqlite3 || true)
if [ -z "$SQLITE3_BIN" ]; then
  echo "sqlite3 binary not found on PATH. Please install sqlite3 to use this script." >&2
  exit 2
fi

cols=$($SQLITE3_BIN "$DB_PATH" "PRAGMA table_info('todo');")
if [ -z "$cols" ]; then
  echo "Could not read table info for 'todo' — ensure the DB is correct and accessible." >&2
  exit 2
fi

has_col() {
  echo "$cols" | awk -F'|' '{print $2}' | grep -x "$1" >/dev/null 2>&1
}

declare -a stmts=()
if ! has_col recurrence_rrule; then
  stmts+=("ALTER TABLE todo ADD COLUMN recurrence_rrule TEXT;")
fi
if ! has_col recurrence_meta; then
  stmts+=("ALTER TABLE todo ADD COLUMN recurrence_meta TEXT;")
fi
if ! has_col recurrence_dtstart; then
  stmts+=("ALTER TABLE todo ADD COLUMN recurrence_dtstart DATETIME;")
fi
if ! has_col recurrence_parser_version; then
  stmts+=("ALTER TABLE todo ADD COLUMN recurrence_parser_version TEXT;")
fi

if [ ${#stmts[@]} -eq 0 ]; then
  echo "No migration needed: recurrence columns already present in 'todo' table."
  exit 0
fi

echo "The following statements will be applied to $DB_PATH:" 
for s in "${stmts[@]}"; do
  echo "  $s"
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry-run mode: not executing statements.";
  exit 0
fi

for s in "${stmts[@]}"; do
  echo "Executing: $s"
  $SQLITE3_BIN "$DB_PATH" "$s"
done

echo "Migration complete."
