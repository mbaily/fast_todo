#!/usr/bin/env python3
"""
Fetch calendar occurrences by calling the application handler directly (no HTTP).
Usage:
  DATABASE_URL="sqlite+aiosqlite:///./fast_todo.db.server_copy" PYTHONPATH=$(pwd) .venv/bin/python scripts/fetch_calendar_occurrences.py --start 2025-08-01T00:00:00Z --end 2025-08-31T23:59:59Z

This will print occurrences JSON and allow grepping for a todo id or text.
"""
import asyncio
import argparse
import json
import os
from typing import Any

from app.main import calendar_occurrences
from app.db import DATABASE_URL


async def run(start: str | None, end: str | None):
    # call the FastAPI handler directly; it expects Request and Depends in signature but
    # calendar_occurrences accepts parameters and relies on require_login defaulting to
    # an authenticated user; to bypass auth we call it with current_user=None which is allowed
    # for many handlers but calendar_occurrences depends on require_login; we'll import
    # a lightweight dummy user object with id=1 to simulate an authenticated user.
    class DummyUser:
        id = 1

    res = await calendar_occurrences(request=None, start=start, end=end, tz=None, expand=True, max_per_item=100, max_total=10000, current_user=DummyUser())
    print(json.dumps(res, indent=2, default=str))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--start')
    p.add_argument('--end')
    args = p.parse_args()
    # Ensure DB env is set externally if desired
    asyncio.run(run(args.start, args.end))
