#!/usr/bin/env python3
"""
Search the SQLite database for a regex and print matching rows.

Usage:
  PYTHONPATH=$(pwd) .venv/bin/python scripts/search_db.py --db ./fast_todo.db --regex "starfield 22/8" --ignore-case

By default this will inspect common text columns across these tables:
  todo(text, note), liststate(name), hashtag(tag), completiontype(name), user(username)

This script opens the SQLite file directly (no server API required).
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path


TABLES_TO_COLUMNS = {
    'todo': ['id', 'text', 'note'],
    'liststate': ['id', 'name'],
    'hashtag': ['id', 'tag'],
    'completiontype': ['id', 'name'],
    'user': ['id', 'username'],
}


def compile_pattern(pattern: str, ignore_case: bool, use_regex: bool):
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    if use_regex:
        return re.compile(pattern, flags)
    # Escape plain text for literal search
    return re.compile(re.escape(pattern), flags)


def search_db(db_path: Path, pattern: re.Pattern, tables=None, limit_per_table: int = 0):
    if not db_path.exists():
        print(f"Error: database file not found: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    found = 0
    tables = tables or list(TABLES_TO_COLUMNS.keys())
    for table in tables:
        cols = TABLES_TO_COLUMNS.get(table)
        if not cols:
            continue
        # Build a projection including id and candidate text columns that exist
        try:
            # fetch one row to see which columns exist
            cur.execute(f"PRAGMA table_info('{table}')")
            existing_cols = [r['name'] for r in cur.fetchall()]
        except Exception:
            # table doesn't exist
            continue
        text_cols = [c for c in cols if c in existing_cols and c != 'id']
        if 'id' not in existing_cols:
            # nothing we can do without id
            continue
        if not text_cols:
            continue
        proj = ', '.join(['id'] + text_cols)
        try:
            cur.execute(f"SELECT {proj} FROM {table}")
        except Exception as e:
            print(f"Skipping table {table}: failed to select columns: {e}", file=sys.stderr)
            continue
        for row in cur.fetchall():
            rowid = row['id']
            for col in text_cols:
                val = row[col]
                if val is None:
                    continue
                if pattern.search(str(val)):
                    print(f"{table}	{rowid}	{col}	{val}")
                    found += 1
                    if limit_per_table and found >= limit_per_table:
                        break
            if limit_per_table and found >= limit_per_table:
                break

    conn.close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Regex search across common tables in the SQLite DB")
    p.add_argument('--db', '-d', default='./fast_todo.db', help='Path to sqlite DB file')
    p.add_argument('--regex', '-r', required=True, help='Regex or literal text to search for')
    p.add_argument('--ignore-case', action='store_true', help='Case-insensitive search')
    p.add_argument('--literal', action='store_true', help='Treat the pattern as literal text (not a regex)')
    p.add_argument('--limit', type=int, default=0, help='Limit total matches (0 = no limit)')
    args = p.parse_args(argv)

    db_path = Path(args.db)
    pattern = compile_pattern(args.regex, ignore_case=args.ignore_case, use_regex=not args.literal)

    ret = search_db(db_path, pattern, limit_per_table=args.limit)
    return ret


if __name__ == '__main__':
    raise SystemExit(main())
