#!/usr/bin/env python3
"""
Add a single todo to the 'recurrence-test' list for dev_user and print persisted recurrence fields.
"""
import asyncio
import os
from sqlmodel import select
from app.db import async_session, init_db
from app.models import User, ListState, Todo
from app.utils import parse_text_to_rrule_string
from app import auth as _auth

async def run(text):
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(User).where(User.username == 'dev_user'))
        user = res.first()
        if not user:
            # seed a passlib-compatible hashed password for local dev_user
            user = User(username='dev_user', password_hash=_auth.pwd_context.hash('dev'))
            sess.add(user)
            await sess.commit()
            await sess.refresh(user)
        res = await sess.exec(select(ListState).where(ListState.owner_id == user.id).where(ListState.name == 'recurrence-test'))
        l = res.first()
        if not l:
            l = ListState(name='recurrence-test', owner_id=user.id)
            sess.add(l)
            await sess.commit()
            await sess.refresh(l)
        t = Todo(text=text, list_id=l.id)
        try:
            dt, r = parse_text_to_rrule_string(text)
            if r:
                t.recurrence_rrule = r
            if dt:
                t.recurrence_dtstart = dt
        except Exception:
            pass
        sess.add(t)
        await sess.commit()
        await sess.refresh(t)
        print('Inserted todo id=', t.id)
        print('recurrence_rrule=', t.recurrence_rrule)
        print('recurrence_dtstart=', t.recurrence_dtstart)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--db-url')
    p.add_argument('text')
    args = p.parse_args()
    if args.db_url:
        os.environ['DATABASE_URL'] = args.db_url
    asyncio.run(run(args.text))
