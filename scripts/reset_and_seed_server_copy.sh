#!/usr/bin/env bash
set -euo pipefail
ROOT=$(dirname "$0")/..
ROOT=$(cd "$ROOT" && pwd)
DB_COPY="$ROOT/fast_todo.db.server_copy"
DB_SRC="$ROOT/fast_todo.db"
if [ -f "$DB_COPY" ]; then
  echo "Deleting existing $DB_COPY"
  rm -f "$DB_COPY"
fi
if [ -f "$DB_SRC" ]; then
  echo "Copying $DB_SRC -> $DB_COPY"
  cp "$DB_SRC" "$DB_COPY"
else
  echo "Source DB $DB_SRC not found; creating empty copy"
  sqlite3 "$DB_COPY" "VACUUM;"
fi
export DATABASE_URL="sqlite+aiosqlite:///$DB_COPY"
# ensure python can find the local package when running from a plain konsole
export PYTHONPATH="$ROOT":${PYTHONPATH-}
echo ROOT is "$ROOT"
echo PYTHONPATH is "$PYTHONPATH"
# activate venv and seed
source "$ROOT/.venv/bin/activate"
python3 "$ROOT/scripts/seed_recurrence_phrases.py" --db-url "sqlite+aiosqlite:///$DB_COPY"

echo "Seed complete. DB = $DB_COPY"
