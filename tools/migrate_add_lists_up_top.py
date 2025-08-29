"""Migration helper: add lists_up_top boolean column to liststate and todo if missing.

This script mirrors other simple migration helpers in `tools/` and is safe to
run multiple times. It checks the schema first then issues ALTER TABLE when
needed. It expects DATABASE_URL to be set (sqlite:///./test.db or similar).
"""
import sqlite3
import sys
import os
from pathlib import Path


def _sqlite_path_from_database_url(url: str | None) -> Path | None:
    if not url:
        return Path('./test.db').resolve()
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

    # liststate
    cur.execute("PRAGMA table_info('liststate')")
    cols = [r[1] for r in cur.fetchall()]
    if 'lists_up_top' in cols:
        print('liststate.lists_up_top already present')
    else:
        print('adding lists_up_top column to liststate')
        try:
            cur.execute("ALTER TABLE liststate ADD COLUMN lists_up_top BOOLEAN DEFAULT 0 NOT NULL")
            conn.commit()
            print('liststate.lists_up_top added')
        except Exception as e:
            print('failed to add lists_up_top to liststate:', e)
            conn.close()
            sys.exit(2)

    # todo
    cur.execute("PRAGMA table_info('todo')")
    cols = [r[1] for r in cur.fetchall()]
    if 'lists_up_top' in cols:
        print('todo.lists_up_top already present')
    else:
        print('adding lists_up_top column to todo')
        try:
            cur.execute("ALTER TABLE todo ADD COLUMN lists_up_top BOOLEAN DEFAULT 0 NOT NULL")
            conn.commit()
            print('todo.lists_up_top added')
        except Exception as e:
            print('failed to add lists_up_top to todo:', e)
            conn.close()
            sys.exit(2)

    conn.close()
    print('migration complete')
