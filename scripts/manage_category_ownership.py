#!/usr/bin/env python3
"""Manage category ownerships.

Usage examples:
  List categories and users:
    PYTHONPATH=. .venv/bin/python scripts/manage_category_ownership.py --list

  Assign categories [1,2,3] to user id 5:
    PYTHONPATH=. .venv/bin/python scripts/manage_category_ownership.py --assign-user-id 5 --categories-json '[1,2,3]'

This script uses the project's SQLModel async_session. It performs a simple
synchronous run of async code via asyncio.run.
"""
import argparse
import asyncio
import json
import sys

from sqlmodel import select, text


def _parse_args():
    p = argparse.ArgumentParser(description='List categories/users or assign category ids to a user')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--list', action='store_true', help='List all categories and users (id, name, owner_id)')
    g.add_argument('--assign-user-id', type=int, help='Assign categories to this user id (requires --categories-json)')
    p.add_argument('--categories-json', type=str, help='JSON array of category ids to assign to the user, e.g. "[1,2,3]"')
    p.add_argument('--dry-run', action='store_true', help='Show SQL that would be run without applying changes')
    return p.parse_args()


def main():
    args = _parse_args()
    if args.list:
        asyncio.run(_do_list())
        return

    if args.assign_user_id is not None:
        if not args.categories_json:
            print('error: --categories-json is required when using --assign-user-id', file=sys.stderr)
            sys.exit(2)
        try:
            cat_ids = json.loads(args.categories_json)
            if not isinstance(cat_ids, (list, tuple)):
                raise ValueError('expected a JSON array')
            cat_ids = [int(x) for x in cat_ids]
        except Exception as e:
            print('failed to parse --categories-json:', e, file=sys.stderr)
            sys.exit(3)
        asyncio.run(_do_assign(args.assign_user_id, cat_ids, dry_run=args.dry_run))
        return


async def _do_list():
    # import inside function so script can run as standalone
    from app.db import async_session
    from app.models import Category, User

    async with async_session() as sess:
        q = await sess.exec(select(Category).order_by(Category.id))
        # SQLModel/sqlalchemy may return either a Result or a ScalarResult
        # depending on versions; handle both cases.
        if hasattr(q, 'scalars'):
            cats = q.scalars().all()
        else:
            cats = q.all()
        print('Categories:')
        for c in cats:
            print(f'  id={c.id} name={c.name!r} owner_id={c.owner_id}')

        q2 = await sess.exec(select(User).order_by(User.id))
        if hasattr(q2, 'scalars'):
            users = q2.scalars().all()
        else:
            users = q2.all()
        print('\nUsers:')
        for u in users:
            print(f'  id={u.id} username={u.username!r} default_category_id={u.default_category_id}')


async def _do_assign(user_id: int, cat_ids: list[int], dry_run: bool = False):
    from app.db import async_session
    from app.models import Category, User

    async with async_session() as sess:
        # check user exists
        u = await sess.get(User, user_id)
        if not u:
            print(f'user id {user_id} not found', file=sys.stderr)
            sys.exit(4)

        # verify categories exist
        q = await sess.exec(select(Category).where(Category.id.in_(cat_ids)))
        if hasattr(q, 'scalars'):
            found_list = q.scalars().all()
        else:
            found_list = q.all()
        found = {c.id: c for c in found_list}
        missing = [cid for cid in cat_ids if cid not in found]
        if missing:
            print('some category ids not found:', missing, file=sys.stderr)
            sys.exit(5)

        if dry_run:
            print(f'DRY RUN: would assign categories {cat_ids} to user id {user_id}')
            for cid in cat_ids:
                print(f'  UPDATE category SET owner_id = {user_id} WHERE id = {cid} -- currently owner_id={found[cid].owner_id}')
            return

        # perform updates one-by-one to keep explicit history in DB logs and ensure triggers (if any) run per-row
        for cid in cat_ids:
            c = found[cid]
            print(f'Assigning category id={cid} name={c.name!r} (was owner_id={c.owner_id}) to user id={user_id}')
            c.owner_id = user_id
            sess.add(c)
        try:
            await sess.commit()
        except Exception as e:
            print('failed to commit updates:', e, file=sys.stderr)
            await sess.rollback()
            sys.exit(6)
        print('Done.')


if __name__ == '__main__':
    main()
