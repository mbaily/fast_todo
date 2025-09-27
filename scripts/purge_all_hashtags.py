"""Force delete ALL hashtags and related association rows.
Usage:
  source .venv/bin/activate
  python scripts/purge_all_hashtags.py [--dry]

This will:
  - Delete all rows from UserHashtag, ListHashtag, TodoHashtag, Hashtag (in that order)
  - VACUUM not performed (SQLite) to keep script simple.
"""
import argparse, asyncio, sys, pathlib
from sqlalchemy import delete as sa_delete
from sqlmodel import select

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import async_session
from app.models import Hashtag, UserHashtag, ListHashtag, TodoHashtag

async def run(dry: bool):
    async with async_session() as sess:
        # counts before
        h_before = (await sess.exec(select(Hashtag.id))).all()
        uh_before = (await sess.exec(select(UserHashtag.hashtag_id))).all()
        lh_before = (await sess.exec(select(ListHashtag.hashtag_id))).all()
        th_before = (await sess.exec(select(TodoHashtag.hashtag_id))).all()
        print(f"Before: hashtags={len(h_before)} user_hashtags={len(uh_before)} list_hashtags={len(lh_before)} todo_hashtags={len(th_before)}")
        if dry:
            print("Dry run only; no deletions performed.")
            return
        await sess.exec(sa_delete(UserHashtag))
        await sess.exec(sa_delete(ListHashtag))
        await sess.exec(sa_delete(TodoHashtag))
        await sess.exec(sa_delete(Hashtag))
        await sess.commit()
        h_after = (await sess.exec(select(Hashtag.id))).all()
        print(f"After: hashtags={len(h_after)} (all cleared)")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()
    asyncio.run(run(args.dry))
