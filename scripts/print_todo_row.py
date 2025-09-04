#!/usr/bin/env python3
"""Print a Todo row from a SQLite file using SQLModel.

Usage:
  scripts/print_todo_row.py /path/to/fast_todo.db 39

This script accepts a path to a SQLite file and a todo id. It constructs a
SQLModel engine for the SQLite file, imports the application's `Todo` model,
queries the row, and pretty-prints the fields including recurrence metadata.
"""
import sys
from pathlib import Path
from sqlmodel import SQLModel, create_engine, select
from sqlalchemy.exc import OperationalError
import json


def iso(dt):
    if dt is None:
        return None
    try:
        return dt.isoformat(sep=' ')
    except Exception:
        return str(dt)


def main():
    if len(sys.argv) != 3:
        print("Usage: scripts/print_todo_row.py /path/to/db.sqlite TODO_ID")
        sys.exit(2)
    db_path = Path(sys.argv[1])
    todo_id = int(sys.argv[2])
    if not db_path.exists():
        print(f"DB file not found: {db_path}")
        sys.exit(2)

    # ensure project root on path so `app` imports succeed
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    # import Todo model after adjusting path
    try:
        from app.models import Todo
    except Exception as e:
        print('Failed to import Todo model from app.models:', e)
        sys.exit(3)

    sqlite_url = f"sqlite:///{db_path}"
    engine = create_engine(sqlite_url, echo=False)

    try:
        with engine.connect() as conn:
            SQLModel.metadata.create_all(conn)
    except OperationalError:
        # ignore; file may be locked or mismatched
        pass

    from sqlmodel import Session

    with Session(engine) as sess:
        stmt = select(Todo).where(Todo.id == todo_id)
        res = sess.exec(stmt)
        row = res.one_or_none()
        if not row:
            print(f"Todo id={todo_id} not found in {db_path}")
            sys.exit(1)

        out = {
            'id': row.id,
            'text': row.text,
            'note': row.note,
            'list_id': row.list_id,
            'created_at': iso(row.created_at),
            'modified_at': iso(row.modified_at),
            'recurrence_rrule': row.recurrence_rrule,
            'recurrence_dtstart': iso(row.recurrence_dtstart),
            'recurrence_meta': None,
        }
        try:
            if row.recurrence_meta:
                out['recurrence_meta'] = json.loads(row.recurrence_meta)
        except Exception:
            out['recurrence_meta'] = row.recurrence_meta

        print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
