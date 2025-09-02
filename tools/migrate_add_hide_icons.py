"""Migration helper: add hide_icons boolean column to liststate if missing.

This script is safe to run multiple times; it checks schema first. It uses
SQLite PRAGMA table_info and ALTER TABLE to add the column where supported.
"""
import sqlite3
import sys
import os
from pathlib import Path

# Determine SQLite file from DATABASE_URL if present, else default to ./fast_todo.db
def _sqlite_path_from_database_url(url: str | None) -> Path | None:
    if not url:
        return Path('./fast_todo.db').resolve()
    # expect forms like sqlite+aiosqlite:///./fast_todo.db or sqlite:///./fast_todo.db
    if url.startswith('sqlite'):
        # split on :/// and take trailing path
        parts = url.split(':///')
        if len(parts) == 2:
            return Path(parts[1]).resolve()
        parts2 = url.split('://')
        if len(parts2) == 2:
            return Path(parts2[1]).resolve()
    return None


if __name__ == '__main__':
    db_url = os.getenv('DATABASE_URL')
    db = _sqlite_path_from_database_url(db_url)
    if db is None:
        print('could not determine sqlite DB path from DATABASE_URL:', db_url)
        sys.exit(1)
    if not db.exists():
        print('database not found at', db)
        sys.exit(1)
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info('liststate')")
    cols = [r[1] for r in cur.fetchall()]
    if 'hide_icons' in cols:
        print('hide_icons already present')
        sys.exit(0)
    print('adding hide_icons column to liststate')
    try:
        cur.execute("ALTER TABLE liststate ADD COLUMN hide_icons BOOLEAN DEFAULT 0")
        conn.commit()
        print('column added')
    except Exception as e:
        print('failed to add column:', e)
        sys.exit(2)
    finally:
        conn.close()
