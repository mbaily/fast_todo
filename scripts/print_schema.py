#!/usr/bin/env python3
"""
Print the SQLite schema (CREATE statements) for the database.

- Avoids using the sqlite3 CLI; uses Python's sqlite3 module instead.
- Prints CREATE statements for tables, indexes, views, and triggers.
- Respects --db path or derives from DATABASE_URL (sqlite:/// or sqlite+aiosqlite:///).

Usage:
  python scripts/print_schema.py [--db /path/to/fast_todo.db] [--include-internal]

Examples:
  # Using default ./fast_todo.db (or DATABASE_URL if set)
  python scripts/print_schema.py

  # Explicit DB file
  python scripts/print_schema.py --db ./fast_todo.db
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from typing import Optional


def _sqlite_path_from_url(url: Optional[str]) -> Optional[str]:
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
    url = os.getenv('DATABASE_URL')
    from_url = _sqlite_path_from_url(url)
    if from_url:
        return from_url
    return os.path.abspath('fast_todo.db')


def _print_section(title: str):
    print()
    print(f"-- {title}")


def print_schema(db_path: str, include_internal: bool) -> int:
    if not os.path.exists(db_path):
        print(f"Error: database file not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        name_filter = '' if include_internal else "AND name NOT LIKE 'sqlite_%'"

        # Tables
        _print_section('Tables')
        for row in cur.execute(
            f"SELECT name, sql FROM sqlite_master WHERE type='table' {name_filter} ORDER BY name"
        ):
            name, sql = row['name'], row['sql']
            print(f"-- table: {name}")
            if sql:
                print(sql.strip() + ';')
            else:
                print(f"-- (no CREATE statement recorded for {name})")
            print()

        # Indexes
        _print_section('Indexes')
        for row in cur.execute(
            f"SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' {name_filter} ORDER BY name"
        ):
            name, tbl, sql = row['name'], row['tbl_name'], row['sql']
            print(f"-- index: {name} on {tbl}")
            if sql:
                print(sql.strip() + ';')
            else:
                # SQLite may auto-create indexes without SQL text
                print(f"-- (no CREATE INDEX statement recorded for {name})")
            print()

        # Views
        _print_section('Views')
        for row in cur.execute(
            f"SELECT name, sql FROM sqlite_master WHERE type='view' {name_filter} ORDER BY name"
        ):
            name, sql = row['name'], row['sql']
            print(f"-- view: {name}")
            if sql:
                print(sql.strip() + ';')
            else:
                print(f"-- (no CREATE VIEW statement recorded for {name})")
            print()

        # Triggers
        _print_section('Triggers')
        for row in cur.execute(
            f"SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger' {name_filter} ORDER BY name"
        ):
            name, tbl, sql = row['name'], row['tbl_name'], row['sql']
            print(f"-- trigger: {name} on {tbl}")
            if sql:
                print(sql.strip() + ';')
            else:
                print(f"-- (no CREATE TRIGGER statement recorded for {name})")
            print()

        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description='Print SQLite schema (CREATE statements).')
    p.add_argument('--db', dest='db', default=None, help='Path to SQLite DB file (defaults to DATABASE_URL or ./fast_todo.db)')
    p.add_argument('--include-internal', action='store_true', help="Include internal sqlite_% objects in output")
    args = p.parse_args(argv)

    db_path = _resolve_db_path(args.db)
    return print_schema(db_path, include_internal=args.include_internal)


if __name__ == '__main__':
    raise SystemExit(main())
#!/usr/bin/env python3
"""
Open the SQLite database `fast_todo.db` in the repository root and print its schema.
Prints CREATE statements for tables, indexes, views, and triggers, plus table column info.
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / 'fast_todo.db'


def print_schema(db_path):
    if not db_path.exists():
        raise SystemExit(f"Database file not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        # Get all sqlite_master rows for tables, indexes, views, triggers
        cur.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql NOT NULL ORDER BY type, name")
        rows = cur.fetchall()
        for typ, name, tbl_name, sql in rows:
            print(f"-- {typ} {name} (table={tbl_name})")
            print(sql.strip())
            print()

        # For each table, show PRAGMA table_info
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        for t in tables:
            print(f"-- Columns for table {t}")
            cur.execute(f"PRAGMA table_info('{t}')")
            cols = cur.fetchall()
            for cid, name, ctype, notnull, dflt_value, pk in cols:
                print(f"{name} | {ctype} | notnull={bool(notnull)} | pk={pk} | default={dflt_value}")
            print()
    finally:
        conn.close()


if __name__ == '__main__':
    print_schema(DB)
