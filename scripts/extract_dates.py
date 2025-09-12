#!/usr/bin/env python3
"""
Standalone utility to scan the SQLite database for date-like strings and
export matches to a JSON file. This script purposely does not import the
application code and only uses the SQLite file directly.

Output format: a JSON array of objects:
  {"table": "todo", "id_column": "id", "id": 42, "column": "text", "value": "Buy milk by 2025-09-11", "matches": ["2025-09-11"]}

Usage: python scripts/extract_dates.py --db fast_todo.db --out extracted_dates.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from typing import List, Dict, Any, Tuple


DATE_REGEXES: List[Tuple[str, re.Pattern]] = [
    ("iso_date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("iso_datetime", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?\b")),
    ("dmy_slash", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("dmy_dot", re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b")),
    ("month_name", re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s\.]\d{1,2}(?:,?\s*\d{2,4})?\b", re.I)),
    ("month_name_year", re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b", re.I)),
    ("relative_day", re.compile(r"\b(?:today|tomorrow|yesterday)\b", re.I)),
    ("weekday_next_prev", re.compile(r"\b(?:next|last|this)\s+(?:mon|tues|wednes|thurs|fri|satur|sun)[a-z]*\b", re.I)),
]


def find_text_columns(conn: sqlite3.Connection) -> Dict[str, List[Tuple[str, str]]]:
    """Return a mapping table -> list of (column_name, type) for textual columns.

    We consider columns with declared type containing 'CHAR', 'CLOB', 'TEXT', or
    no declared type (SQLite is dynamic) as candidates to scan.
    """
    out: Dict[str, List[Tuple[str, str]]] = {}
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cur.fetchall()]
    for t in tables:
        try:
            cur.execute(f"PRAGMA table_info('{t}')")
            cols = cur.fetchall()
            candidates: List[Tuple[str, str]] = []
            for col in cols:
                # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
                name = col[1]
                ctype = (col[2] or "").upper()
                if not ctype or any(k in ctype for k in ("CHAR", "CLOB", "TEXT")) or ctype.startswith('VARCHAR'):
                    candidates.append((name, col[2] or ""))
            if candidates:
                out[t] = candidates
        except Exception:
            # ignore problematic tables (virtual tables, etc.)
            continue
    return out


def scan_value_for_dates(value: str) -> List[str]:
    matches: List[str] = []
    if not value:
        return matches
    for name, rx in DATE_REGEXES:
        for m in rx.findall(value):
            # findall may return tuples for complex groups; ensure string
            if isinstance(m, tuple):
                m = " ".join(m).strip()
            if m and m not in matches:
                matches.append(m)
    return matches


def extract_dates(db_path: str, out_path: str, limit_per_table: int | None = None) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB file not found: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        text_cols = find_text_columns(conn)
        results: List[Dict[str, Any]] = []
        cur = conn.cursor()
        for table, cols in text_cols.items():
            # choose an id column if present, otherwise rowid
            id_col = None
            try:
                cur.execute(f"PRAGMA table_info('{table}')")
                tcols = cur.fetchall()
                for col in tcols:
                    if col[5] == 1:  # pk
                        id_col = col[1]
                        break
            except Exception:
                id_col = None

            select_cols = [id_col] if id_col else []
            select_cols += [c[0] for c in cols]
            select_clause = ", ".join([f'"{c}"' for c in select_cols]) if select_cols else '*'
            query = f"SELECT {select_clause} FROM \"{table}\""
            if limit_per_table:
                query += f" LIMIT {limit_per_table}"
            try:
                cur.execute(query)
            except Exception:
                # fallback to selecting all columns
                try:
                    cur.execute(f"SELECT * FROM \"{table}\"")
                except Exception:
                    continue
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description] if cur.description else []
            for row in rows:
                rowd = dict(zip(col_names, row))
                identifier = rowd.get(id_col) if id_col else rowd.get('id') or rowd.get('rowid')
                for col_name, _ in cols:
                    val = rowd.get(col_name)
                    if val is None:
                        continue
                    # ensure it's a string
                    if not isinstance(val, str):
                        try:
                            val = str(val)
                        except Exception:
                            continue
                    matches = scan_value_for_dates(val)
                    if matches:
                        results.append({
                            "table": table,
                            "id_column": id_col or "rowid",
                            "id": identifier,
                            "column": col_name,
                            "value": val,
                            "matches": matches,
                        })
        # write output
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(results)} matches to {out_path}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract date-like strings from SQLite DB into JSON")
    p.add_argument('--db', default=os.getenv('FAST_TODO_DB', 'fast_todo.db'), help='Path to SQLite DB file')
    p.add_argument('--out', default='extracted_dates.json', help='Output JSON file')
    p.add_argument('--limit', type=int, default=None, help='Optional per-table row limit (for debugging)')
    args = p.parse_args(argv)
    try:
        extract_dates(args.db, args.out, args.limit)
        return 0
    except Exception as e:
        print('Error:', e)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
