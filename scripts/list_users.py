#!/usr/bin/env python3
"""List users in the database.

Usage:
  DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db" python scripts/list_users.py

This script reads DATABASE_URL from the environment (falls back to app default),
initializes the DB schema (best-effort), and prints username and password_hash.
"""
import os
import asyncio
from sqlmodel import select

def _get_db_url():
    return os.getenv('DATABASE_URL')

async def main():
    # import here so we pick up DATABASE_URL if set
    from app.db import init_db, async_session
    from app.models import User

    db_url = _get_db_url() or os.getenv('DATABASE_URL')
    print(f"Using DATABASE_URL={os.getenv('DATABASE_URL')}")
    # ensure schema exists (best-effort)
    try:
        await init_db()
    except Exception as e:
        print(f"init_db() failed (continuing): {e}")

    async with async_session() as sess:
        q = await sess.exec(select(User))
        users = q.all()
        if not users:
            print("No users found in DB.")
            return
        print(f"Found {len(users)} users:\n")
        for u in users:
            print(f"username: {u.username}\n  password_hash: {u.password_hash}\n  is_admin: {bool(getattr(u, 'is_admin', False))}\n")

if __name__ == '__main__':
    asyncio.run(main())
