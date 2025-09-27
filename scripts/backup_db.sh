#!/bin/bash
set -euo pipefail

# File and backup directory
DB_FILE="fast_todo.db"
BACKUP_DIR="backups"

# Create backup dir if missing
mkdir -p "$BACKUP_DIR"

# Timestamped filename
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/fast_todo_${TIMESTAMP}.db"

# Copy the file
if [[ -f "$DB_FILE" ]]; then
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "Backup created: $BACKUP_FILE"
else
    echo "Error: $DB_FILE not found!"
    exit 1
fi

# Rotate (keep only 10 newest)
cd "$BACKUP_DIR"
ls -1t fast_todo_*.db 2>/dev/null | tail -n +11 | xargs -r rm --

