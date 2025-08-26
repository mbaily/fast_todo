#!/usr/bin/env python3
"""Create the recentlistvisit table if missing.

This script is written to work with the project's async engine and SQLite.
It will create the table using a CREATE TABLE IF NOT EXISTS statement so it's
idempotent and safe to run on older DBs.
"""
import os
import sys
from sqlalchemy import text

# Make project importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.db import engine

async def run():
    async with engine.begin() as conn:
        # create table if not exists; use integer (foreign keys) and datetime text
        create_sql = """
        CREATE TABLE IF NOT EXISTS recentlistvisit (
            user_id INTEGER NOT NULL,
            list_id INTEGER NOT NULL,
            visited_at DATETIME DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, list_id)
        );
        """
        try:
            await conn.execute(text(create_sql))
            # create an index on visited_at to speed up recent retrievals
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recentlistvisit_user_visited_at ON recentlistvisit(user_id, visited_at DESC)"))
            # ensure 'position' column exists (used for top-N ordering)
            try:
                pragma = await conn.execute(text("PRAGMA table_info('recentlistvisit')"))
                cols = [row[1] for row in pragma.fetchall()]
            except Exception:
                # fallback: try reading as result.all()
                try:
                    pragma = await conn.execute(text("PRAGMA table_info('recentlistvisit')"))
                    cols = [row[1] for row in pragma.all()]
                except Exception:
                    cols = []

            if 'position' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE recentlistvisit ADD COLUMN position INTEGER"))
                    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_recentlistvisit_user_position ON recentlistvisit(user_id, position)"))
                except Exception as e:
                    print('failed to add position column to recentlistvisit:', e)

            print('recentlistvisit table ensured')
        except Exception as e:
            print('failed to create recentlistvisit table:', e)

if __name__ == '__main__':
    import asyncio
    asyncio.run(run())
