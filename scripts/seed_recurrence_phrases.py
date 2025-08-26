"""Seed the dev DB with todos from tests/recurrence_phrases.json.

Usage:
  .venv/bin/python scripts/seed_recurrence_phrases.py --db-url sqlite+aiosqlite:///./test.db

The script will:
- create (or reuse) a list named 'recurrence-test' owned by a special local user
  (create a user 'dev_user' if missing)
- insert one Todo per JSON entry with text=item['text'] and note=item.get('note')
- commit and print a summary count

This uses the project's SQLModel async_session and models.
"""

import asyncio
import json
import os
from pathlib import Path

from sqlmodel import select

from app.db import async_session, init_db
from app.models import User, ListState, Todo
from app.utils import parse_text_to_rrule_string
from app import auth as _auth


JSON_FILE = (Path(__file__).resolve().parent.parent / 'tests' / 'recurrence_phrases.json')


async def seed(db_url: str | None = None):
    # init DB (creates tables / adds recurrence columns if needed)
    await init_db()
    async with async_session() as sess:
        # ensure dev user exists
        res = await sess.exec(select(User).where(User.username == 'dev_user'))
        user = res.first()
        if not user:
            # seed a passlib-compatible hashed password for local dev_user
            user = User(username='dev_user', password_hash=_auth.pwd_context.hash('dev'))
            sess.add(user)
            await sess.commit()
            await sess.refresh(user)
        # ensure list exists
        res = await sess.exec(select(ListState).where(ListState.owner_id == user.id).where(ListState.name == 'recurrence-test'))
        l = res.first()
        if not l:
            l = ListState(name='recurrence-test', owner_id=user.id)
            sess.add(l)
            await sess.commit()
            await sess.refresh(l)
        # load JSON
        items = json.load(open(JSON_FILE))
        inserted = 0
        for it in items:
            text = it.get('text') if isinstance(it, dict) else it
            note = it.get('note') if isinstance(it, dict) else None
            # create todo
            t = Todo(text=text, note=note, list_id=l.id)
            # parse recurrence and store rrule/dtstart (optional)
            try:
                dt, r = parse_text_to_rrule_string(text)
                if r:
                    t.recurrence_rrule = r
                if dt:
                    t.recurrence_dtstart = dt
            except Exception:
                pass
            sess.add(t)
            inserted += 1
        await sess.commit()
        print(f"Inserted {inserted} todos into list 'recurrence-test' (owner dev_user)")


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('--db-url', help='Optional database URL (overrides DATABASE_URL env var)')
    args = p.parse_args()
    if args.db_url:
        os.environ['DATABASE_URL'] = args.db_url
    asyncio.run(seed())
