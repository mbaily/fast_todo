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
