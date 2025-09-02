"""Migration helper: add default_category_id integer column to user if missing.

Safe to run multiple times. Detects the SQLite DB via DATABASE_URL or falls back
to ./fast_todo.db. Uses PRAGMA table_info to check for the column and issues
ALTER TABLE when necessary.
"""
import sqlite3
import sys
import os
from pathlib import Path


def _sqlite_path_from_database_url(url: str | None) -> Path | None:
    if not url:
        return Path('./fast_todo.db').resolve()
    if url.startswith('sqlite'):
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

    # Check user table
    cur.execute("PRAGMA table_info('user')")
    cols = [r[1] for r in cur.fetchall()]
    if 'default_category_id' in cols:
        print('default_category_id already present on user')
        conn.close()
        sys.exit(0)

    print('adding default_category_id column to user')
    try:
        # SQLite supports adding simple columns via ALTER TABLE
        cur.execute("ALTER TABLE user ADD COLUMN default_category_id INTEGER DEFAULT NULL")
        conn.commit()
        print('column added')
    except Exception as e:
        print('failed to add column:', e)
        conn.close()
        sys.exit(2)
    finally:
        conn.close()
