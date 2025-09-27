"""Demote a user (set is_admin False).
Usage:
  source .venv/bin/activate
  python scripts/demote_admin.py --username mbaily
"""
import argparse, asyncio, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.db import async_session
from app.models import User
from sqlmodel import select

async def run(username: str):
    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == username))
        u = res.one_or_none()
        if not u:
            print('User not found')
            return
        u.is_admin = False
        sess.add(u)
        await sess.commit()
        print(f'Demoted {username}: is_admin={u.is_admin}')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--username', required=True)
    args = ap.parse_args()
    asyncio.run(run(args.username))
