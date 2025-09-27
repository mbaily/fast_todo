"""Backfill UserHashtag rows for a specific user from existing list/todo associations.
Usage:
  source .venv/bin/activate
  python scripts/backfill_user_hashtags_on_demand.py --user 1
"""
import argparse
import asyncio
from sqlmodel import select
from app.db import async_session
from app.models import ListState, ListHashtag, Todo, TodoHashtag, UserHashtag
from sqlalchemy.exc import IntegrityError

async def run(user_id: int):
    async with async_session() as sess:
        # Collect hashtag IDs from user's lists
        ql = await sess.exec(
            select(ListHashtag.hashtag_id)
            .join(ListState, ListState.id == ListHashtag.list_id)
            .where(ListState.owner_id == user_id)
        )
        list_hids = set()
        for row in ql.all():
            hid = row[0] if isinstance(row, (tuple, list)) else row
            if hid is not None:
                list_hids.add(int(hid))
        # Collect from todos
        qt = await sess.exec(
            select(TodoHashtag.hashtag_id)
            .join(Todo, Todo.id == TodoHashtag.todo_id)
            .join(ListState, ListState.id == Todo.list_id)
            .where(ListState.owner_id == user_id)
        )
        todo_hids = set()
        for row in qt.all():
            hid = row[0] if isinstance(row, (tuple, list)) else row
            if hid is not None:
                todo_hids.add(int(hid))
        assoc = list_hids | todo_hids
        if not assoc:
            print(f"No associated hashtags found for user {user_id}.")
            return
        # Existing ownership
        qo = await sess.exec(select(UserHashtag.hashtag_id).where(UserHashtag.user_id == user_id))
        owned = {int(row[0] if isinstance(row, (tuple, list)) else row) for row in qo.all()}
        missing = [hid for hid in assoc if hid not in owned]
        if not missing:
            print(f"All {len(assoc)} associated hashtags already owned for user {user_id}.")
            return
        print(f"Inserting ownership rows for user {user_id}: missing {len(missing)} / total assoc {len(assoc)}")
        for hid in missing:
            sess.add(UserHashtag(user_id=user_id, hashtag_id=hid))
        try:
            await sess.commit()
        except IntegrityError:
            await sess.rollback()
            print("IntegrityError during insert; retrying individually.")
            for hid in missing:
                try:
                    sess.add(UserHashtag(user_id=user_id, hashtag_id=hid))
                    await sess.commit()
                except IntegrityError:
                    await sess.rollback()
        # Final count
        final = await sess.exec(select(UserHashtag.hashtag_id).where(UserHashtag.user_id == user_id))
        final_ids = {int(row[0] if isinstance(row, (tuple, list)) else row) for row in final.all()}
        print(f"Done. Ownership count now {len(final_ids)}. Newly added {len(final_ids - owned)}.")

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--user', type=int, required=True)
    args = ap.parse_args()
    asyncio.run(run(args.user))
