"""Diagnostic script to print hashtag ownership & association counts for a user.
Usage:
  source .venv/bin/activate
  python scripts/debug_user_hashtags.py --user 1
"""
import argparse
import asyncio
from sqlmodel import select
from app.db import async_session
from app.models import ListState, ListHashtag, TodoHashtag, Todo, UserHashtag, Hashtag

async def run(user_id: int):
    async with async_session() as sess:
        # Lists owned
        rl = await sess.exec(select(ListState.id).where(ListState.owner_id == user_id))
        list_ids = [int(r if not isinstance(r, (tuple, list)) else r[0]) for r in rl.all()]
        # Hashtags via lists
        ql = await sess.exec(select(ListHashtag.hashtag_id).join(ListState, ListState.id == ListHashtag.list_id).where(ListState.owner_id == user_id))
        list_hids = {int(r if not isinstance(r, (tuple, list)) else r[0]) for r in ql.all() if (r if not isinstance(r, (tuple, list)) else r[0]) is not None}
        # Hashtags via todos
        qt = await sess.exec(select(TodoHashtag.hashtag_id).join(Todo, Todo.id == TodoHashtag.todo_id).join(ListState, ListState.id == Todo.list_id).where(ListState.owner_id == user_id))
        todo_hids = {int(r if not isinstance(r, (tuple, list)) else r[0]) for r in qt.all() if (r if not isinstance(r, (tuple, list)) else r[0]) is not None}
        assoc_hids = list_hids | todo_hids
        # Ownership rows
        qu = await sess.exec(select(UserHashtag.hashtag_id).where(UserHashtag.user_id == user_id))
        owned_hids = {int(r if not isinstance(r, (tuple, list)) else r[0]) for r in qu.all() if (r if not isinstance(r, (tuple, list)) else r[0]) is not None}
        # Tags resolved
        if owned_hids:
            th = await sess.exec(select(Hashtag.id, Hashtag.tag).where(Hashtag.id.in_(owned_hids)))
            tags = {int(i): t for i, t in th.all()}
        else:
            tags = {}
        print(f"User {user_id} lists={len(list_ids)} list_hids={len(list_hids)} todo_hids={len(todo_hids)} assoc_total={len(assoc_hids)} owned={len(owned_hids)}")
        if assoc_hids and not owned_hids:
            print("Owned set empty but associations exist -> ownership backfill not triggered or failed.")
        missing = assoc_hids - owned_hids
        if missing:
            print(f"Missing ownership for {len(missing)} hashtag ids: {sorted(list(missing))[:20]}{'...' if len(missing)>20 else ''}")
        sample = list(tags.items())[:20]
        if sample:
            print("Sample owned tags:", sample)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', type=int, required=True)
    args = ap.parse_args()
    asyncio.run(run(args.user))
