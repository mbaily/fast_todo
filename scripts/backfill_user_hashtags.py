"""Backfill script to populate UserHashtag rows from existing list/todo associations.

Usage:
  source .venv/bin/activate
  python scripts/backfill_user_hashtags.py

Idempotent: uses INSERT best-effort; ignores IntegrityError.
"""
from app.db import async_session, init_db
from app.models import UserHashtag, Hashtag, ListHashtag, TodoHashtag, ListState, Todo
from sqlmodel import select
import asyncio

async def backfill():
    async with async_session() as sess:
        # Collect (user_id, hashtag_id) from list associations
        user_tag_pairs = set()
        try:
            ql = select(ListState.owner_id, ListHashtag.hashtag_id).join(ListHashtag, ListHashtag.list_id == ListState.id)
            resl = await sess.exec(ql)
            for owner_id, hid in resl.all():
                if owner_id is None or hid is None:
                    continue
                user_tag_pairs.add((int(owner_id), int(hid)))
        except Exception:
            pass
        try:
            qt = select(ListState.owner_id, TodoHashtag.hashtag_id).join(Todo, Todo.list_id == ListState.id).join(TodoHashtag, TodoHashtag.todo_id == Todo.id)
            rest = await sess.exec(qt)
            for owner_id, hid in rest.all():
                if owner_id is None or hid is None:
                    continue
                user_tag_pairs.add((int(owner_id), int(hid)))
        except Exception:
            pass
        # Insert
        inserted = 0
        for uid, hid in user_tag_pairs:
            sess.add(UserHashtag(user_id=uid, hashtag_id=hid))
            try:
                await sess.commit()
                inserted += 1
            except Exception:
                await sess.rollback()
        print(f"Backfill complete. candidate_pairs={len(user_tag_pairs)} inserted={inserted}")

if __name__ == '__main__':
    asyncio.run(backfill())
