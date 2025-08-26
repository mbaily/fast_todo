#!/usr/bin/env python3
"""
Check and optionally create DB tables for occurrence hash persistence models.

Usage examples:
  # dry-run (default)
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_occurrence_hashes.py --db sqlite+aiosqlite:///./test.db.server_copy

  # actually create missing tables
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_occurrence_hashes.py --db sqlite+aiosqlite:///./test.db.server_copy --commit

The script imports `app.models` to register SQLModel metadata, then inspects the
target DB and lists missing tables. If --commit is given it will call
SQLModel.metadata.create_all(engine) to create them.
"""
import argparse
import sys
from sqlmodel import SQLModel


def _sync_sqlite_url(db_url: str) -> str:
    # convert async sqlite driver URL to sync sqlite URL for SQLAlchemy create_engine
    if db_url.startswith('sqlite+aiosqlite://'):
        return db_url.replace('sqlite+aiosqlite://', 'sqlite://', 1)
    return db_url


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True, help='DATABASE_URL (e.g. sqlite+aiosqlite:///./test.db)')
    p.add_argument('--commit', action='store_true', help='create missing tables')
    args = p.parse_args()

    # ensure app models are imported so metadata includes new tables
    try:
        import app.models as _m  # noqa: F401
    except Exception as e:
        print('failed importing app.models:', e, file=sys.stderr)
        sys.exit(2)

    sync_url = _sync_sqlite_url(args.db)
    try:
        from sqlalchemy import create_engine, inspect
    except Exception as e:
        print('sqlalchemy import failed:', e, file=sys.stderr)
        sys.exit(3)

    engine = create_engine(sync_url, echo=False, future=True)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    desired = set(SQLModel.metadata.tables.keys())
    missing = sorted(list(desired - existing))

    print('database url:', args.db)
    print('existing tables count:', len(existing))
    print('models defined tables count:', len(desired))
    if not missing:
        print('No missing tables detected.')
        return

    print('Missing tables:')
    for t in missing:
        print('  -', t)

    if args.commit:
        print('Creating missing tables...')
        SQLModel.metadata.create_all(engine)
        print('Done: created missing tables.')
    else:
        print('\nDry-run: nothing was created. Rerun with --commit to create missing tables.')


if __name__ == '__main__':
    main()
