#!/usr/bin/env python3
"""
Query the SQLite DB for a specific todo id and print its text and
plain_dates_meta JSON snapshot.

Usage:
  python scripts/query_todo_plain_dates.py --id 930 [--db fast_todo.db]
"""
import argparse
import json
import sqlite3
from typing import Any, Optional


def fetch_todo(db_path: str, todo_id: int) -> Optional[dict[str, Any]]:
    con = sqlite3.connect(db_path)
    # Access TEXT as str, and row as dict for convenience
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute("SELECT id, text, note, plain_dates_meta FROM todo WHERE id = ?", (todo_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "text": row["text"],
            "note": row["note"],
            "plain_dates_meta": row["plain_dates_meta"],
        }
    finally:
        try:
            con.close()
        except Exception:
            pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, required=True, help="Todo id to inspect")
    p.add_argument("--db", default="fast_todo.db", help="Path to SQLite database file")
    args = p.parse_args()

    row = fetch_todo(args.db, args.id)
    if row is None:
        print(f"Todo id {args.id} not found in {args.db}")
        return

    print(f"id: {row['id']}")
    print(f"text: {row['text']}")
    note = row.get("note")
    if note:
        trimmed = (note[:120] + "…") if len(note) > 120 else note
    else:
        trimmed = None
    print(f"note: {trimmed}")
    pd = row.get("plain_dates_meta")
    if not pd:
        print("plain_dates_meta: (empty)")
        return
    print("plain_dates_meta (raw):")
    print(pd)
    try:
        j = json.loads(pd)
        print("plain_dates_meta (parsed):")
        for i, m in enumerate(j):
            dt = m.get("dt")
            ye = m.get("year_explicit")
            mon = m.get("month")
            day = m.get("day")
            text = m.get("match_text")
            print(f"  [{i}] year_explicit={ye} month={mon} day={day} dt={dt} match_text={text}")
    except Exception as e:
        print(f"Failed to parse JSON: {e}")


if __name__ == "__main__":
    main()
