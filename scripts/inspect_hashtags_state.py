"""Inspect current hashtag state directly from the DB.
Usage:
  source .venv/bin/activate
  python scripts/inspect_hashtags_state.py
"""
import asyncio, sys, pathlib
from sqlmodel import select

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import async_session
from app.models import Hashtag, UserHashtag, ListHashtag, TodoHashtag

async def main():
    async with async_session() as sess:
        h_res = await sess.exec(select(Hashtag.id, Hashtag.tag))
        hashtags = h_res.all()
        uh_res = await sess.exec(select(UserHashtag.user_id, UserHashtag.hashtag_id))
        user_tags = uh_res.all()
        lh_res = await sess.exec(select(ListHashtag.list_id, ListHashtag.hashtag_id))
        list_tags = lh_res.all()
        th_res = await sess.exec(select(TodoHashtag.todo_id, TodoHashtag.hashtag_id))
        todo_tags = th_res.all()
        print(f"Hashtag rows: {len(hashtags)}")
        print(f"UserHashtag rows: {len(user_tags)}   ListHashtag rows: {len(list_tags)}   TodoHashtag rows: {len(todo_tags)}")
        print("Sample hashtags (first 15):")
        for row in hashtags[:15]:
            # row may be tuple
            if isinstance(row, (tuple, list)):
                hid, tag = row
            else:
                hid, tag = row.id, row.tag  # type: ignore
            print(f"  id={hid} tag={tag}")

        # Identify orphan IDs
        assoc_ids = {r[1] for r in list_tags if isinstance(r,(tuple,list))} | {r[1] for r in todo_tags if isinstance(r,(tuple,list))}
        owned_ids = {r[1] for r in user_tags if isinstance(r,(tuple,list))}
        orphan_ids = []
        for row in hashtags:
            hid = row[0] if isinstance(row,(tuple,list)) else row.id  # type: ignore
            if hid not in assoc_ids and hid not in owned_ids:
                orphan_ids.append(hid)
        print(f"Orphan hashtag ids (first 30 of {len(orphan_ids)}): {orphan_ids[:30]}")

if __name__ == '__main__':
    asyncio.run(main())
