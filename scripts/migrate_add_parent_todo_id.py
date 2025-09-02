#!/usr/bin/env python3
"""
Add ListState.parent_todo_id (nullable INTEGER) and an index.

Usage:
  # dry-run
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_parent_todo_id.py --db sqlite+aiosqlite:///./fast_todo.db
  # apply
  PYTHONPATH=. .venv/bin/python scripts/migrate_add_parent_todo_id.py --db sqlite+aiosqlite:///./fast_todo.db --commit

Notes:
- Safe for SQLite and Postgres: simple ALTER TABLE ADD COLUMN and CREATE INDEX IF NOT EXISTS.
- Existing rows default to NULL (root lists).
"""
import argparse
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text


async def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', required=True, help='SQLAlchemy DB URL, e.g. sqlite+aiosqlite:///./fast_todo.db')
    p.add_argument('--commit', action='store_true', help='apply schema changes')
    args = p.parse_args()

    engine = create_async_engine(args.db, future=True)
    async with engine.begin() as conn:
        # Inspect liststate columns
        res = await conn.execute(text("PRAGMA table_info('liststate')"))
        cols = [r[1] for r in res.fetchall()] if res else []
        need_add = 'parent_todo_id' not in cols
        if need_add:
            if args.commit:
                await conn.execute(text('ALTER TABLE liststate ADD COLUMN parent_todo_id INTEGER'))
                print('Added column parent_todo_id to liststate')
            else:
                print('Dry-run: would add column parent_todo_id to liststate')
        else:
            print('Column parent_todo_id already present')
        # Create index (SQLite supports IF NOT EXISTS)
        if args.commit:
            try:
                await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_liststate_parent_todo_id ON liststate(parent_todo_id)'))
                print('Ensured index ix_liststate_parent_todo_id')
            except Exception as e:
                print('Failed to create index:', e)
        else:
            print('Dry-run: would create index ix_liststate_parent_todo_id')
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(main())
