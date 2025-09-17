"""Debug helper: print index context (categories and lists_by_category) for a given user id.

Usage: python scripts/debug_index_context.py --user-id 1

This script reuses the app's async_session and models to fetch the computed
`categories` and `lists_by_category` similar to the html_index handler.
"""
import argparse
import asyncio
from sqlmodel import select
from app.db import async_session
from app.models import Category, ListState, ListHashtag, Hashtag


async def run(user_id: int):
    async with async_session() as sess:
        owner_id = user_id
        # fetch visible lists (owned by user)
        q = select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
        res = await sess.exec(q)
        lists = res.all()
        list_rows = [{
            'id': l.id,
            'name': l.name,
            'category_id': l.category_id,
        } for l in lists]
        lists_by_category = {}
        for r in list_rows:
            cid = r.get('category_id') or 0
            lists_by_category.setdefault(cid, []).append(r)
        # fetch categories visible to this user
        qcat = select(Category).where((Category.owner_id == owner_id) | (Category.owner_id == None)).order_by(Category.position.asc())
        cres = await sess.exec(qcat)
        categories = [{'id': c.id, 'name': c.name, 'position': c.position, 'owner_id': getattr(c, 'owner_id', None)} for c in cres.all()]
        print('categories (%d):' % len(categories))
        for c in categories:
            print('  ', c)
        print('\nlists_by_category keys:')
        for k in sorted(lists_by_category.keys()):
            print('  ', k, '->', len(lists_by_category[k]), 'lists')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--user-id', type=int, required=True)
    args = p.parse_args()
    asyncio.run(run(args.user_id))
