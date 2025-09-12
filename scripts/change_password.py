#!/usr/bin/env python3
"""
Change a user's password in the specified SQLite DB.

Usage:
  ./scripts/change_password.py --db ./fast_todo.db --username alice --password 'newpass'

This script uses the project's async DB session and the same password
hashing (passlib CryptContext) as the app so password hashes are compatible.
"""
import argparse
import asyncio
import os
import sys


parser = argparse.ArgumentParser(description='Change a user password in the DB')
parser.add_argument('--db', required=False, default='./fast_todo.db', help='SQLAlchemy DATABASE_URL (sqlite+aiosqlite:///./fast_todo.db) or path')
parser.add_argument('--username', required=True, help='username to update')
parser.add_argument('--password', required=True, help='new plaintext password')
args = parser.parse_args()

# Set DATABASE_URL env var so app.db picks it up
if args.db.startswith('sqlite'):
    os.environ['DATABASE_URL'] = args.db
else:
    # treat as local path
    os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{args.db}"

# Import project modules after setting DATABASE_URL
sys.path.insert(0, os.path.abspath(os.getcwd()))
from app.db import async_session
from app.models import User
from app.auth import pwd_context
from sqlmodel import select

async def main():
    async with async_session() as sess:
        q = await sess.exec(select(User).where(User.username == args.username))
        u = q.first()
        if not u:
            print(f"user not found: {args.username}")
            return 2
        # hash password using app's pwd_context
        new_hash = pwd_context.hash(args.password)
        u.password_hash = new_hash
        sess.add(u)
        await sess.commit()
        print(f"updated password for user {args.username}")
        return 0

if __name__ == '__main__':
    code = asyncio.get_event_loop().run_until_complete(main())
    sys.exit(code)
