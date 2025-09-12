#!/usr/bin/env python3
"""
Change a user's password interactively or via CLI args.

Usage:
  scripts/change_user_password.py --username mbaily
  scripts/change_user_password.py --username mbaily --password 'newpass' --db ./fast_todo.db

The script sets DATABASE_URL so it uses the same DB as the app and
reuses the app's password hashing (pwd_context) to keep compatibility.
"""
import argparse
import getpass
import asyncio
import os
import sys


parser = argparse.ArgumentParser(description='Change a user password in the DB')
parser.add_argument('--db', required=False, default='./fast_todo.db', help='SQLite DB path or a full SQLAlchemy URL (sqlite+aiosqlite:///<path>)')
parser.add_argument('--username', required=True, help='username to update')
parser.add_argument('--password', required=False, help='new plaintext password')
args = parser.parse_args()

# Normalize DATABASE_URL
if args.db.startswith('sqlite') or args.db.startswith('postgres'):
    os.environ['DATABASE_URL'] = args.db
else:
    os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{args.db}"

# Import project modules after setting DATABASE_URL
sys.path.insert(0, os.path.abspath(os.getcwd()))
from app.db import async_session
from app.models import User
from app.auth import pwd_context
from sqlmodel import select

async def main():
    new_pass = args.password
    if not new_pass:
        # prompt twice
        p1 = getpass.getpass(f"New password for {args.username}: ")
        p2 = getpass.getpass("Repeat: ")
        if p1 != p2:
            print("Passwords do not match", file=sys.stderr)
            return 2
        new_pass = p1
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == args.username))
        u = q.first()
        if not u:
            print(f"user not found: {args.username}", file=sys.stderr)
            return 3
        new_hash = pwd_context.hash(new_pass)
        u.password_hash = new_hash
        sess.add(u)
        await sess.commit()
        print(f"updated password for user {args.username}")
        return 0

if __name__ == '__main__':
    code = asyncio.get_event_loop().run_until_complete(main())
    sys.exit(code)
