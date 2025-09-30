#!/usr/bin/env python3
"""
Add sublists_hide_done column to todo table if missing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_todo_sublists_hide_done.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_todo_sublists_hide_done.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy --commit

The script will import `app.models` so the SQLModel metadata includes the updated Todo model.
If the `todo` table lacks the `sublists_hide_done` column the script will attempt to ALTER TABLE to add it.
"""
import argparse
import sys
from sqlmodel import SQLModel


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
        import app.models as _m  # noqa: F401
    except Exception as e:
        print('failed importing app.models:', e, file=sys.stderr)
        sys.exit(2)

    sync_url = _sync_sqlite_url(args.db)
    try:
        from sqlalchemy import create_engine, inspect, text
    except Exception as e:
        print('sqlalchemy import failed:', e, file=sys.stderr)
        sys.exit(3)

    engine = create_engine(sync_url, echo=False, future=True)
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    print('database url:', args.db)
    print('existing tables:', sorted(existing_tables))

    # Ensure todo table exists (should already exist)
    if 'todo' not in existing_tables:
        print('todo table is missing - this is unexpected', file=sys.stderr)
        sys.exit(4)

    # Ensure todo has sublists_hide_done column
    try:
        cols = [c['name'] for c in insp.get_columns('todo')]
    except Exception:
        cols = []
    if 'sublists_hide_done' not in cols:
        print('todo.sublists_hide_done column is missing')
        if args.commit:
            print('Adding sublists_hide_done column to todo...')
            with engine.begin() as conn:
                # Use simple ALTER TABLE which works for SQLite (add column with default)
                conn.execute(text('ALTER TABLE todo ADD COLUMN sublists_hide_done BOOLEAN DEFAULT 0'))
            print('Added sublists_hide_done column.')
        else:
            print('Dry-run: would run ALTER TABLE to add sublists_hide_done. Re-run with --commit to apply.')
    else:
        print('todo.sublists_hide_done already present.')

    print('\nDone. Note: for complex schema changes or production DBs prefer Alembic migrations.')


if __name__ == '__main__':
    main()