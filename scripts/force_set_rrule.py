#!/usr/bin/env python3
"""
Force-set recurrence_rrule for a todo matching exact text.
"""
import asyncio, os
from sqlmodel import select
from app.db import async_session, init_db
from app.models import Todo
from app.utils import parse_text_to_rrule_string

async def run(text, rrule):
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(Todo).where(Todo.text == text))
        t = res.first()
        if not t:
            print('todo not found')
            return
        dt, _ = parse_text_to_rrule_string(text)
        if not dt:
            from app.utils import now_utc
            dt = now_utc()
        t.recurrence_rrule = rrule
        t.recurrence_dtstart = dt
        sess.add(t)
        await sess.commit()
        await sess.refresh(t)
        print('updated todo id=', t.id)
        print('recurrence_rrule=', t.recurrence_rrule)
        print('recurrence_dtstart=', t.recurrence_dtstart)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('text')
    p.add_argument('rrule')
    args = p.parse_args()
    os.environ['PYTHONPATH'] = os.environ.get('PYTHONPATH','') + ':' + os.getcwd()
    asyncio.run(run(args.text, args.rrule))
