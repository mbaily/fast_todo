#!/usr/bin/env python3
"""
Set a hashed password for dev_user in the specified DB copy so token login works.
Usage: python3 scripts/set_dev_user_password.py --db /path/to/test.db.server_copy --password dev
"""
import asyncio, os
from sqlmodel import select

from app.db import async_session, init_db
from app.models import User
from app import auth as _auth

async def run(db_path, password):
    # ensure DB initialized
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == 'dev_user'))
        user = res.first()
        if not user:
            print('dev_user not found')
            return
        new_hash = _auth.pwd_context.hash(password)
        user.password_hash = new_hash
        sess.add(user)
        await sess.commit()
        print('Updated dev_user password hash')

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--db', help='path to sqlite DB copy', default='test.db.server_copy')
    p.add_argument('--password', default='dev')
    args = p.parse_args()
    if args.db:
        os.environ['DATABASE_URL'] = f"sqlite+aiosqlite:///{os.path.abspath(args.db)}"
    asyncio.run(run(args.db, args.password))
