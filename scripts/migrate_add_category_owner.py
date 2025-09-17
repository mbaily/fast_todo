#!/usr/bin/env python3
"""
Add `owner_id` column to category table if missing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_category_owner.py --db sqlite+aiosqlite:///./fast_todo.db --commit

This simple migration will ALTER TABLE ADD COLUMN owner_id INTEGER and add an index.
For more complex migrations or production DBs consider using Alembic.
"""
import argparse
import sys
import os
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

    if 'category' not in existing_tables:
        print('category table is missing; cannot add owner_id')
        sys.exit(0)

    try:
        cols = [c['name'] for c in insp.get_columns('category')]
    except Exception:
        cols = []

    if 'owner_id' not in cols:
        print('category.owner_id column is missing')
        if args.commit:
            print('Adding owner_id column to category...')
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE category ADD COLUMN owner_id INTEGER'))
                # Create a simple index to speed lookups
                try:
                    conn.execute(text('CREATE INDEX IF NOT EXISTS ix_category_owner_id ON category(owner_id)'))
                except Exception:
                    print('failed to create index ix_category_owner_id (may not be supported)')
            print('Added owner_id column and index (if supported).')
        else:
            print('Dry-run: would ALTER TABLE to add owner_id column. Re-run with --commit to apply.')
    else:
        print('category.owner_id already present.')

    print('\nDone.')


if __name__ == '__main__':
    main()
