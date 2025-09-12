#!/usr/bin/env python3
"""Admin script to add or update a user in the app DB.

Usage:
    python scripts/add_user.py username password [--admin]

This will ensure the DB is initialized, and then create or update a User
record with a hashed password.
"""
# Make the script runnable from the project root or from anywhere by
# adding the project root to sys.path. This locates the top-level `app`
# package (parent of the scripts/ directory).
import os
import sys
proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if proj_root not in sys.path:
        sys.path.insert(0, proj_root)

import argparse
import asyncio
import sys
import getpass
from typing import Optional




async def _create_or_update(username: str, password: str, is_admin: bool = False) -> Optional['User']:
    # Import app modules lazily so running `-h` or other help commands
    # doesn't require installing all runtime dependencies.
    from app.db import init_db, async_session
    from app.models import User
    from app.auth import pwd_context
    from sqlmodel import select
    await init_db()
    ph = pwd_context.hash(password)
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == username))
        existing = q.first()
        if existing:
            existing.password_hash = ph
            existing.is_admin = bool(is_admin)
            sess.add(existing)
            try:
                await sess.commit()
            except Exception:
                await sess.rollback()
                print(f"Failed to update existing user {username}")
                return None
            await sess.refresh(existing)
            return existing
        # create new
        u = User(username=username, password_hash=ph, is_admin=bool(is_admin))
        sess.add(u)
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()
            print(f"Failed to create user {username}")
            return None
        await sess.refresh(u)
        return u


def parse_args(argv):
    p = argparse.ArgumentParser(description="Create or update a user in the app DB")
    p.add_argument("username", help="username to create/update")
    # make password optional; if omitted we'll prompt securely
    p.add_argument("password", nargs="?", help="password for the user (omit to prompt)")
    p.add_argument("--admin", action="store_true", help="mark user as admin")
    p.add_argument("--db", default="./fast_todo.db", help="path to sqlite file to use (default ./fast_todo.db)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    # Allow overriding the DB file used by the app by setting DATABASE_URL.
    # The app/db module reads DATABASE_URL at import time to configure the engine.
    db_path = getattr(args, 'db', None)
    if db_path:
        # Convert a file path into the async sqlite URL used by SQLAlchemy
        os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{db_path}"
    username = args.username
    password = args.password
    # If password not provided on the command line, prompt securely.
    if not password:
        pw = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            print("Passwords do not match", file=sys.stderr)
            return 2
        if pw == "":
            print("Empty password not allowed", file=sys.stderr)
            return 2
        password = pw
    is_admin = args.admin

    user = asyncio.run(_create_or_update(username, password, is_admin))
    if not user:
        print("Operation failed")
        return 2
    print(f"User '{user.username}' ({'admin' if user.is_admin else 'user'}) saved with id={user.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
