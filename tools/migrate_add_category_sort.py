#!/usr/bin/env python3
"""Simple migration: add `sort_alphanumeric` boolean column to category table if missing.

This migration is written to work with SQLite and general SQLAlchemy engines.
It checks for the column and issues an `ALTER TABLE` where supported.
"""
import os
import sys
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

# Import project's DB engine
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import engine

async def run(dry_run: bool = True):
    async with engine.begin() as conn:
        try:
            res = await conn.execute(text("PRAGMA table_info('category')"))
            cols = [r[1] for r in res.fetchall()]
        except Exception:
            print('failed to read table info for category (PRAGMA unsupported?)')
            return
        if 'sort_alphanumeric' in cols:
            print('sort_alphanumeric column already present on category')
            return
        print('Adding sort_alphanumeric column to category table...')
        stmt = "ALTER TABLE category ADD COLUMN sort_alphanumeric INTEGER DEFAULT 0 NOT NULL"
        print('SQL:', stmt)
        if dry_run:
            print('Dry-run; no changes made. Re-run with --commit to apply.')
            return
        try:
            await conn.execute(text(stmt))
            print('Migration applied: sort_alphanumeric added')
        except OperationalError as e:
            print('ALTER TABLE failed:', e)

if __name__ == '__main__':
    import argparse
    import asyncio
    p = argparse.ArgumentParser()
    p.add_argument('--commit', action='store_true', help='actually apply the migration')
    args = p.parse_args()
    asyncio.run(run(dry_run=not args.commit))
