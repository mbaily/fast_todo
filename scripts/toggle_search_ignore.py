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

from sqlmodel import select

# Ensure app is importable when running from repo root
import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, REPO_ROOT)

from app.db import async_session
from app.models import Todo


async def _toggle(todo_id: int, set_value: str | None):
    async with async_session() as sess:
        t = await sess.get(Todo, todo_id)
        if not t:
            print(f"Todo {todo_id} not found")
            return 1
        if set_value is None:
            t.search_ignored = not bool(getattr(t, 'search_ignored', False))
        else:
            val = str(set_value).lower() in ("1", "true", "yes", "on")
            t.search_ignored = val
        sess.add(t)
        await sess.commit()
        await sess.refresh(t)
        print(f"Todo {t.id} search_ignored={t.search_ignored}")
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
