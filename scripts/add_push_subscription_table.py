"""Simple migration: add PushSubscription table for PWA push subscriptions.

This script uses the same DATABASE_URL logic as the app to find the sqlite
file, and then creates the table if it doesn't exist. Run with the project
virtualenv Python: `./.venv/bin/python scripts/add_push_subscription_table.py`.
"""
import sqlite3
import os
from app import db as app_db


def main():
    url = getattr(app_db, 'DATABASE_URL', None)
    path = app_db._sqlite_path_from_url(url)
    if not path:
        print('No sqlite DB path detected from DATABASE_URL; aborting')
        return
    if not os.path.exists(path):
        print(f'Database file {path} does not exist; aborting')
        return

    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        # Create table if not exists
        cur.execute('''
        CREATE TABLE IF NOT EXISTS pushsubscription (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            subscription_json TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT
        )
        ''')
        conn.commit()
        print('pushsubscription table ensured')
        # create an index on user_id for lookup efficiency
        try:
            cur.execute('CREATE INDEX IF NOT EXISTS ix_pushsubscription_user_id ON pushsubscription(user_id)')
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


if __name__ == '__main__':
    main()
