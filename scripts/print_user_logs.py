#!/usr/bin/env python3
"""
Print all EventLog rows for a given username, oldest to newest.

- Connects directly to the SQLite database (default: ./fast_todo.db)
- Filters logs by exact username match
- Prints all fields from the eventlog table for each row

Usage:
  python scripts/print_user_logs.py <username> [--db /path/to/fast_todo.db]

Environment:
  DATABASE_URL can be set (e.g., sqlite+aiosqlite:///./fast_todo.db). If provided,
  it will be parsed to extract the SQLite file path unless --db is passed.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Optional


def _sqlite_path_from_url(url: Optional[str]) -> Optional[str]:
    """Extract a filesystem path from a (a)iosqlite DATABASE_URL."""
    try:
        if not url:
            return None
        if url.startswith('sqlite+aiosqlite:///'):
            path = url.replace('sqlite+aiosqlite:///', '', 1)
        elif url.startswith('sqlite:///'):
            path = url.replace('sqlite:///', '', 1)
        else:
            return None
        if path.startswith('./'):
            path = path[2:]
        return os.path.abspath(path)
    except Exception:
        return None


def _resolve_db_path(cli_path: Optional[str]) -> str:
    if cli_path:
        return os.path.abspath(cli_path)
    # Try DATABASE_URL
    url = os.getenv('DATABASE_URL')
    from_url = _sqlite_path_from_url(url)
    if from_url:
        return from_url
    # Fallback to repo-local default
    return os.path.abspath('fast_todo.db')


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def print_user_logs(username: str, db_path: str) -> int:
    if not os.path.exists(db_path):
        print(f"Error: database file not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        # Ensure tables exist; if not, fail fast with a helpful message
        # Query all columns from eventlog for the given username, oldest first
        sql = (
            'SELECT e.id, e.user_id, e.message, e.item_type, e.item_id, e.url, e.label, '
            'e.created_at, e.metadata_json '
            'FROM eventlog e JOIN "user" u ON u.id = e.user_id '
            'WHERE u.username = ? '
            'ORDER BY e.created_at ASC, e.id ASC'
        )
        cur.execute(sql, (username,))
        rows = cur.fetchall()
        if not rows:
            print(f"No logs found for user '{username}'.")
            return 0

        print(f"Found {len(rows)} log(s) for user '{username}' in {db_path}:\n")
        for i, row in enumerate(rows, 1):
            data = _row_to_dict(row)
            # Pretty print with stable key order
            print(f"#{i}")
            print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
            print('-' * 60)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description='Print EventLog rows for a user (oldest to newest).')
    p.add_argument('username', help='Exact username to filter logs by')
    p.add_argument('--db', dest='db', default=None, help='Path to SQLite DB file (defaults to DATABASE_URL or ./fast_todo.db)')
    args = p.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    return print_user_logs(args.username, db_path)


if __name__ == '__main__':
    raise SystemExit(main())
