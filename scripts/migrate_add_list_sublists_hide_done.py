#!/usr/bin/env python3
"""Add sublists_hide_done column to liststate table if missing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_list_sublists_hide_done.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_list_sublists_hide_done.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy --commit

If the `liststate` table lacks the `sublists_hide_done` column the script will attempt to ALTER TABLE to add it.
Prefers simple ALTER (works for SQLite). For production/complex DBs prefer Alembic.
"""
import argparse
import sys


def _sync_sqlite_url(db_url: str) -> str:
    if db_url.startswith('sqlite+aiosqlite://'):
        return db_url.replace('sqlite+aiosqlite://', 'sqlite://', 1)
    return db_url


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True, help='DATABASE_URL (e.g. sqlite+aiosqlite:///./fast_todo.db)')
    p.add_argument('--commit', action='store_true', help='apply schema changes')
    args = p.parse_args()

    try:
        import app.models as _m  # noqa: F401 ensure metadata import
    except Exception as e:
        print('failed importing app.models:', e, file=sys.stderr)
        sys.exit(2)

    try:
        from sqlalchemy import create_engine, inspect, text
    except Exception as e:
        print('sqlalchemy import failed:', e, file=sys.stderr)
        sys.exit(3)

    sync_url = _sync_sqlite_url(args.db)
    engine = create_engine(sync_url, echo=False, future=True)
    insp = inspect(engine)

    tables = set(insp.get_table_names())
    print('database url:', args.db)
    print('existing tables:', sorted(tables))

    if 'liststate' not in tables:
        print('liststate table is missing - unexpected', file=sys.stderr)
        sys.exit(4)

    try:
        cols = [c['name'] for c in insp.get_columns('liststate')]
    except Exception:
        cols = []
    if 'sublists_hide_done' not in cols:
        print('liststate.sublists_hide_done column is missing')
        if args.commit:
            print('Adding sublists_hide_done column to liststate...')
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE liststate ADD COLUMN sublists_hide_done BOOLEAN DEFAULT 0'))
            print('Added sublists_hide_done column.')
        else:
            print('Dry-run: would add sublists_hide_done (re-run with --commit).')
    else:
        print('liststate.sublists_hide_done already present.')

    print('\nDone.')


if __name__ == '__main__':
    main()
