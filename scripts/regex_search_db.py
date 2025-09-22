#!/usr/bin/env python3
"""Search the SQLite database for a regular expression and print matching rows.

This script inspects a set of common textual columns (todos, lists, hashtags, etc.)
and prints rows where the provided regex matches any of the basic text fields.

Usage examples:
  python scripts/regex_search_db.py "due\\s+tomorrow" --db ./fast_todo.db
  python scripts/regex_search_db.py --ignore-case "urgent" --tables todo,liststate

"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from typing import Dict, List, Tuple, Optional

# Mapping of table -> (id_col, [text columns to search/display])
DEFAULT_TABLE_COLUMNS: Dict[str, Tuple[str, List[str]]] = {
    'todo': ('id', ['text', 'note']),
    'liststate': ('id', ['name']),
    'hashtag': ('id', ['tag']),
    'category': ('id', ['name']),
    'completiontype': ('id', ['name']),
    'itemlink': ('id', ['label']),
    'eventlog': ('id', ['message']),
    'user': ('id', ['username']),
    'session': ('id', ['session_token']),
    'pushsubscription': ('id', ['subscription_json']),
}


def sqlite_path_from_url(url: Optional[str]) -> Optional[str]:
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


def find_matches(conn: sqlite3.Connection, table: str, id_col: str, cols: List[str], pattern: re.Pattern, limit: Optional[int], truncate: int) -> int:
    cur = conn.cursor()
    # build column list for SELECT; skip missing columns gracefully
    # We'll query sqlite_master to see which columns exist for the table
    cur.execute("PRAGMA table_info('%s')" % table)
    existing = {row[1] for row in cur.fetchall()}
    select_cols = [id_col] + [c for c in cols if c in existing]
    if len(select_cols) <= 1:
        return 0
    col_sql = ', '.join(select_cols)
    try:
        cur.execute(f"SELECT {col_sql} FROM {table}")
    except Exception as e:
        print(f"Skipping {table}: failed to query columns ({e})", file=sys.stderr)
        return 0
    found = 0
    for row in cur:
        # row is a tuple in the same order as select_cols
        row_id = row[0]
        matched = False
        out_fields = []
        for idx, col in enumerate(select_cols[1:], start=1):
            val = row[idx]
            if val is None:
                continue
            try:
                s = str(val)
            except Exception:
                continue
            if pattern.search(s):
                matched = True
            # prepare truncated display
            display = s
            if truncate and len(display) > truncate:
                display = display[:truncate] + '...'
            out_fields.append((col, display))
        if matched:
            found += 1
            fields_str = ' '.join([f"{k}='{v}'" for k, v in out_fields])
            print(f"[{table}] id={row_id} {fields_str}")
            if limit and found >= limit:
                break
    return found


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description='Regex search across common DB text fields')
    p.add_argument('regex', help='Regular expression to search for (Python syntax)')
    p.add_argument('--db', help='Path to SQLite DB file or sqlite URL (e.g. sqlite+aiosqlite:///./fast_todo.db). If omitted, uses $DATABASE_URL or ./fast_todo.db', default=None)
    p.add_argument('--ignore-case', '-i', action='store_true', help='Case-insensitive search')
    p.add_argument('--tables', help='Comma-separated list of tables to search (default: common text tables)', default=None)
    p.add_argument('--limit', type=int, help='Maximum matches per table (default: no limit)', default=None)
    p.add_argument('--truncate', type=int, help='Truncate displayed text fields to this many chars (default: 300)', default=300)
    args = p.parse_args(argv)

    regex = args.regex
    flags = re.MULTILINE
    if args.ignore_case:
        flags |= re.IGNORECASE
    try:
        pattern = re.compile(regex, flags)
    except re.error as e:
        print(f"Invalid regular expression: {e}", file=sys.stderr)
        return 2

    db_path = None
    if args.db:
        # accept direct sqlite path or sqlite URL
        if args.db.startswith('sqlite'):
            db_path = sqlite_path_from_url(args.db) or args.db
        else:
            db_path = args.db
    else:
        env_url = os.getenv('DATABASE_URL')
        if env_url:
            db_path = sqlite_path_from_url(env_url) or env_url
        else:
            db_path = os.path.abspath('./fast_todo.db')

    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}", file=sys.stderr)
        return 3

    selected = DEFAULT_TABLE_COLUMNS.copy()
    if args.tables:
        wanted = [t.strip() for t in args.tables.split(',') if t.strip()]
        selected = {t: selected[t] for t in wanted if t in selected}
        if not selected:
            print(f"No supported tables selected from: {args.tables}", file=sys.stderr)
            return 4

    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:
        print(f"Failed to open DB {db_path}: {e}", file=sys.stderr)
        return 5

    total = 0
    try:
        for table, (id_col, cols) in selected.items():
            cnt = find_matches(conn, table, id_col, cols, pattern, args.limit, args.truncate)
            if cnt:
                total += cnt
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if total == 0:
        print('No matches found.')
    else:
        print(f"Total matches: {total}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
