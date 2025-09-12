#!/usr/bin/env python3
"""
Toggle the search_ignored flag on a Todo by id.

Usage:
  python scripts/toggle_search_ignore.py <todo_id> [--set true|false]
If --set is omitted, the flag will be toggled.
"""
import argparse
import asyncio
import os


# Ensure app is importable when running from repo root
import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, REPO_ROOT)

from app.db import async_session
from app.models import Todo


async def _toggle(todo_id: int, set_value: str | None):
    # First session: apply change and commit
    async with async_session() as sess:
        t = await sess.get(Todo, todo_id)
        if not t:
            print(f"Todo {todo_id} not found")
            return 1
        prev = bool(getattr(t, 'search_ignored', False))
        if set_value is None:
            new_val = not prev
        else:
            s = str(set_value).strip().lower()
            if s in ("1", "true", "yes", "on"):  # truthy
                new_val = True
            elif s in ("0", "false", "no", "off", "none", "null"):  # falsy
                new_val = False
            else:
                print(f"Unrecognized boolean value for --set: {set_value}")
                return 2
        t.search_ignored = new_val
        sess.add(t)
        await sess.commit()
    # Second session: verify persisted state and print summary
    async with async_session() as sess2:
        t2 = await sess2.get(Todo, todo_id)
        print("Updated Todo:")
        print(f"  id:             {t2.id}")
        print(f"  search_ignored: {getattr(t2, 'search_ignored', None)} (prev: {prev} -> new: {new_val})")
        print(f"  text:           {getattr(t2, 'text', '')}")
        print(f"  note:           {getattr(t2, 'note', None)}")
        print(f"  list_id:        {getattr(t2, 'list_id', None)}")
        print(f"  priority:       {getattr(t2, 'priority', None)}")
        print(f"  pinned:         {getattr(t2, 'pinned', None)}")
        print(f"  created_at:     {getattr(t2, 'created_at', None)}")
        print(f"  modified_at:    {getattr(t2, 'modified_at', None)}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("todo_id", type=int)
    p.add_argument("--set", dest="set_value", default=None, help="Explicit value true/false; omit to toggle")
    args = p.parse_args()
    rc = asyncio.run(_toggle(args.todo_id, args.set_value))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
