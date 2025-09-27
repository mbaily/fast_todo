"""Purge all orphan hashtags (no UserHashtag, ListHashtag, or TodoHashtag references).
Usage:
  source .venv/bin/activate
  python scripts/purge_orphan_hashtags.py [--dry]

Dry run prints counts only.
"""
import argparse
import asyncio
import sys, pathlib
from sqlmodel import select

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import async_session
from app.models import Hashtag, UserHashtag, ListHashtag, TodoHashtag
from sqlalchemy import func

async def run(dry: bool):
    async with async_session() as sess:
        # Left joins to detect usage counts
        # We'll manually gather ids then delete in batches
        res = await sess.exec(select(Hashtag.id))
        all_ids = [int(r[0] if isinstance(r,(tuple,list)) else r) for r in res.all()]
        orphan_ids = []
        for hid in all_ids:
            # check ownership
            ou = await sess.exec(select(func.count()).select_from(UserHashtag).where(UserHashtag.hashtag_id==hid))
            c = ou.one()
            if isinstance(c, (tuple, list)):
                c = c[0]
            if c != 0:
                continue
            ll = await sess.exec(select(func.count()).select_from(ListHashtag).where(ListHashtag.hashtag_id==hid))
            c = ll.one()
            if isinstance(c, (tuple, list)):
                c = c[0]
            if c != 0:
                continue
            tt = await sess.exec(select(func.count()).select_from(TodoHashtag).where(TodoHashtag.hashtag_id==hid))
            c = tt.one()
            if isinstance(c, (tuple, list)):
                c = c[0]
            if c != 0:
                continue
            orphan_ids.append(hid)
        if dry:
            print(f"Dry run: total={len(all_ids)} orphan={len(orphan_ids)} (no deletions)")
            return
        if not orphan_ids:
            print("No orphan hashtags found.")
            return
        # delete in chunks
        from sqlalchemy import delete as sa_delete
        CHUNK=100
        for i in range(0, len(orphan_ids), CHUNK):
            chunk = orphan_ids[i:i+CHUNK]
            await sess.exec(sa_delete(Hashtag).where(Hashtag.id.in_(chunk)))
        await sess.commit()
        print(f"Deleted {len(orphan_ids)} orphan hashtags (remaining total={len(all_ids)-len(orphan_ids)})")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()
    asyncio.run(run(args.dry))
