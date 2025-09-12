#!/usr/bin/env python3
"""Simple migration: add `pinned` boolean column to todo table if missing.

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

async def run():
    async with engine.begin() as conn:
        # Check if column exists (SQLite pragma)
        try:
            res = await conn.execute(text("PRAGMA table_info('todo')"))
            cols = [r[1] for r in res.fetchall()]
        except Exception:
            print('failed to read table info')
            return
        if 'pinned' in cols:
            print('pinned column already present')
            return
        print('adding pinned column to todo...')
        try:
            await conn.execute(text("ALTER TABLE todo ADD COLUMN pinned INTEGER DEFAULT 0 NOT NULL"))
            print('done')
        except OperationalError as e:
            print('ALTER TABLE failed:', e)

if __name__ == '__main__':
    import asyncio
    asyncio.run(run())
