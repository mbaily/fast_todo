#!/usr/bin/env python3
"""Seed the project's database with many ListState rows like the server's
create_list handler would.

Usage:
    export DATABASE_URL="sqlite+aiosqlite:///fast_todo.db"
    python tools/seed_test_db.py --count 3000 [--owner-id 42]

The script will:
 - call init_db() to ensure migrations/tables exist
 - create `count` ListState rows with names seed-list-1 ... seed-list-N
 - for each created list, ensure a CompletionType named "default" exists
 - if ServerState.default_list_id is empty, set it to the first created list

This intentionally commits per-list to keep memory low and to behave like
server create_list semantics.
"""
import asyncio
import argparse
import os
import sys

from sqlmodel import select
from sqlalchemy.exc import IntegrityError

# Import project DB and models
# Adjust import paths if you run this script from a different CWD
try:
    from app.db import async_session, init_db
    from app.models import ListState, CompletionType, ServerState
except Exception:
    # If package imports fail when executed from repo root, try relative import
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from app.db import async_session, init_db
    from app.models import ListState, CompletionType, ServerState


def parse_args():
    p = argparse.ArgumentParser(description='Seed test DB with many lists')
    p.add_argument('--count', '-n', type=int, default=3000, help='number of lists to create')
    p.add_argument('--owner-id', type=int, default=None, help='optional owner_id for the lists (default: public lists)')
    p.add_argument('--username', type=str, default=None, help='optional username to own the lists (overrides --owner-id)')
    p.add_argument('--start', type=int, default=1, help='start index for naming (default: 1)')
    return p.parse_args()


async def create_one(name: str, owner_id: int | None = None):
    async with async_session() as sess:
        lst = ListState(name=name, owner_id=owner_id)
        sess.add(lst)
        try:
            await sess.commit()
        except IntegrityError:
            await sess.rollback()
            return None
        await sess.refresh(lst)
        # ensure default completion type exists for this list
        qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == lst.id).where(CompletionType.name == 'default'))
        if not qc.first():
            c = CompletionType(name='default', list_id=lst.id)
            sess.add(c)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
        # set server default list if unset
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        if ss and not ss.default_list_id:
            ss.default_list_id = lst.id
            sess.add(ss)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
        return lst.id


async def main():
    args = parse_args()
    count = args.count
    owner_id = args.owner_id
    start = args.start
    username = args.username

    # If username provided, resolve to owner_id (requires the user to exist)
    if username:
        async with async_session() as sess:
            user = None
            try:
                # prefer existing helper if available
                from app.auth import get_user_by_username
                user = await get_user_by_username(username)
            except Exception:
                # fallback to direct DB query
                try:
                    from app.models import User
                    q = await sess.exec(select(User).where(User.username == username))
                    user = q.first()
                except Exception:
                    user = None
        if not user:
            print(f"error: username '{username}' not found in DB; create the user first or use --owner-id")
            return
        owner_id = user.id

    print(f"Initializing DB (DATABASE_URL={os.getenv('DATABASE_URL')})")
    await init_db()
    created = 0
    first_id = None
    for i in range(start, start + count):
        name = f"seed-list-{i}"
        lid = await create_one(name, owner_id)
        if lid:
            created += 1
            if first_id is None:
                first_id = lid
        if created % 100 == 0:
            print(f"created {created} lists...")
    print(f"done: created {created} lists; first_id={first_id}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nInterrupted')
        sys.exit(1)
