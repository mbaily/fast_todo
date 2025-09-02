#!/usr/bin/env python3
"""
Add Category table and category_id column on liststate if missing.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_categories.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_categories.py --db sqlite+aiosqlite:///./fast_todo.db.server_copy --commit

The script will import `app.models` so the SQLModel metadata includes the new Category model.
If the `category` table is missing it will create it. If `liststate` lacks the
`category_id` column the script will attempt to ALTER TABLE to add it. For SQLite
this is a simple ALTER; for more complex schema changes consider using Alembic.
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

    # Ensure category table exists
    if 'category' not in existing_tables:
        print('category table is missing')
        if args.commit:
            print('Creating category table...')
            SQLModel.metadata.create_all(engine, tables=[SQLModel.metadata.tables['category']])
            print('Created category table.')
        else:
            print('Dry-run: category table would be created. Re-run with --commit to create it.')
    else:
        print('category table already exists.')

    # Ensure liststate has category_id column
    try:
        cols = [c['name'] for c in insp.get_columns('liststate')]
    except Exception:
        cols = []
    if 'category_id' not in cols:
        print('liststate.category_id column is missing')
        if args.commit:
            print('Adding category_id column to liststate...')
            with engine.begin() as conn:
                # Use simple ALTER TABLE which works for SQLite (add column) and PostgreSQL
                conn.execute(text('ALTER TABLE liststate ADD COLUMN category_id INTEGER'))
            print('Added category_id column.')
        else:
            print('Dry-run: would run ALTER TABLE to add category_id. Re-run with --commit to apply.')
    else:
        print('liststate.category_id already present.')

    print('\nDone. Note: for complex schema changes or production DBs prefer Alembic migrations.')


if __name__ == '__main__':
    main()
