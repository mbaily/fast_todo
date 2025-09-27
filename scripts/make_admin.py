"""Promote a user to admin (is_admin=True).
Usage:
  source .venv/bin/activate
  python scripts/make_admin.py --username mbaily
"""
import argparse
import asyncio
import sys, pathlib
from sqlmodel import select

# Ensure project root (parent of scripts/) is on sys.path
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import async_session
from app.models import User

async def run(username: str):
    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == username))
        user = res.one_or_none()
        if not user:
            print(f'User {username} not found')
            return
        if user.is_admin:
            print(f'User {username} already admin')
            return
        user.is_admin = True
        sess.add(user)
        await sess.commit()
        print(f'User {username} promoted to admin.')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--username', required=True)
    args = ap.parse_args()
    asyncio.run(run(args.username))
