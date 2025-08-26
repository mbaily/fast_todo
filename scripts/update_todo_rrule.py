#!/usr/bin/env python3
"""
Find the todo by text and persist recurrence_rrule and recurrence_dtstart using parser.
"""
import asyncio, os
from sqlmodel import select
from app.db import async_session, init_db
from app.models import Todo, User, ListState
from app.utils import parse_text_to_rrule_string, parse_recurrence_phrase, parse_date_and_recurrence, now_utc

async def run(text):
    await init_db()
    async with async_session() as sess:
        res = await sess.exec(select(Todo).where(Todo.text == text))
        t = res.first()
        if not t:
            print('todo not found')
            return
        dt, r = parse_text_to_rrule_string(text)
        # If no rrule found but a recurrence phrase exists anywhere, synthesize
        # an rrule and dtstart (use parsed date if available else now_utc()).
        if not r:
            rec = parse_recurrence_phrase(text)
            if rec:
                if not dt:
                    dt = now_utc()
                # convert rec dict to rrule string using existing utility
                from app.utils import recurrence_dict_to_rrule_string
                r = recurrence_dict_to_rrule_string(rec)
        print('parsed dt=', dt, 'rrule=', r)
        if r:
            t.recurrence_rrule = r
        if dt:
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
    args = p.parse_args()
    os.environ['PYTHONPATH'] = os.environ.get('PYTHONPATH','') + ':' + os.getcwd()
    asyncio.run(run(args.text))
