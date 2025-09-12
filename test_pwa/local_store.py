"""Local storage module for offline PWA client data."""

import sqlite3
import json
import os
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path


class LocalStore:
    """SQLite-based local storage for PWA client data."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), 'local_data.db')
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS lists (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_id INTEGER,
                    created_at TEXT,
                    modified_at TEXT,
                    expanded BOOLEAN DEFAULT 1,
                    hide_done BOOLEAN DEFAULT 0,
                    lists_up_top BOOLEAN DEFAULT 0,
                    hide_icons BOOLEAN DEFAULT 0,
                    completed BOOLEAN DEFAULT 0,
                    category_id INTEGER,
                    parent_todo_id INTEGER,
                    parent_todo_position INTEGER,
                    parent_list_id INTEGER,
                    parent_list_position INTEGER,
                    priority INTEGER
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    sort_alphanumeric BOOLEAN DEFAULT 0
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY,
                    text TEXT NOT NULL,
                    note TEXT,
                    pinned BOOLEAN DEFAULT 0,
                    search_ignored BOOLEAN DEFAULT 0,
                    created_at TEXT,
                    modified_at TEXT,
                    deferred_until TEXT,
                    recurrence_rrule TEXT,
                    recurrence_meta TEXT,
                    recurrence_dtstart TEXT,
                    recurrence_parser_version TEXT,
                    list_id INTEGER NOT NULL,
                    priority INTEGER,
                    lists_up_top BOOLEAN DEFAULT 0,
                    sort_links BOOLEAN DEFAULT 0
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS pending_ops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    op_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            conn.commit()

    def clear_all(self) -> None:
        """Clear all local data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM lists')
            conn.execute('DELETE FROM todos')
            conn.execute('DELETE FROM categories')
            conn.execute('DELETE FROM pending_ops')
            conn.execute('DELETE FROM sync_state')
            conn.commit()

    def store_lists(self, lists: List[Dict[str, Any]]) -> None:
        """Store lists data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO lists (
                    id, name, owner_id, created_at, modified_at, expanded, hide_done,
                    lists_up_top, hide_icons, completed, category_id, parent_todo_id,
                    parent_todo_position, parent_list_id, parent_list_position, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                (
                    lst['id'], lst['name'], lst.get('owner_id'),
                    lst.get('created_at'), lst.get('modified_at'),
                    lst.get('expanded', True), lst.get('hide_done', False),
                    lst.get('lists_up_top', False), lst.get('hide_icons', False),
                    lst.get('completed', False), lst.get('category_id'),
                    lst.get('parent_todo_id'), lst.get('parent_todo_position'),
                    lst.get('parent_list_id'), lst.get('parent_list_position'),
                    lst.get('priority')
                )
                for lst in lists
            ])
            conn.commit()

    def store_todos(self, todos: List[Dict[str, Any]]) -> None:
        """Store todos data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO todos (
                    id, text, note, created_at, modified_at, list_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', [
                (
                    todo['id'], todo['text'], todo.get('note'),
                    todo.get('created_at'), todo.get('modified_at'), todo['list_id']
                )
                for todo in todos
            ])
            conn.commit()

    def get_lists(self) -> List[Dict[str, Any]]:
        """Get all stored lists."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT * FROM lists').fetchall()
            return [
                {
                    'id': row[0], 'name': row[1], 'owner_id': row[2],
                    'created_at': row[3], 'modified_at': row[4],
                    'expanded': bool(row[5]), 'hide_done': bool(row[6]),
                    'lists_up_top': bool(row[7]), 'hide_icons': bool(row[8]),
                    'completed': bool(row[9]), 'category_id': row[10],
                    'parent_todo_id': row[11], 'parent_todo_position': row[12],
                    'parent_list_id': row[13], 'parent_list_position': row[14],
                    'priority': row[15]
                }
                for row in rows
            ]

    def store_categories(self, categories: List[Dict[str, Any]]) -> None:
        """Store categories data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO categories (
                    id, name, position, sort_alphanumeric
                ) VALUES (?, ?, ?, ?)
            ''', [
                (
                    cat['id'], cat['name'], cat.get('position', 0),
                    cat.get('sort_alphanumeric', False)
                )
                for cat in categories
            ])
            conn.commit()

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get all stored categories."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT * FROM categories ORDER BY position').fetchall()
            return [
                {
                    'id': row[0], 'name': row[1], 'position': row[2],
                    'sort_alphanumeric': bool(row[3])
                }
                for row in rows
            ]

    def get_todos(self, list_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get all stored todos, optionally filtered by list_id."""
        with sqlite3.connect(self.db_path) as conn:
            if list_id is not None:
                rows = conn.execute('SELECT * FROM todos WHERE list_id = ?', (list_id,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM todos').fetchall()
            return [
                {
                    'id': row[0], 'text': row[1], 'note': row[2],
                    'pinned': bool(row[3]), 'search_ignored': bool(row[4]),
                    'created_at': row[5], 'modified_at': row[6],
                    'deferred_until': row[7], 'recurrence_rrule': row[8],
                    'recurrence_meta': row[9], 'recurrence_dtstart': row[10],
                    'recurrence_parser_version': row[11], 'list_id': row[12],
                    'priority': row[13], 'lists_up_top': bool(row[14]),
                    'sort_links': bool(row[15])
                }
                for row in rows
            ]

    def get_list_by_id(self, list_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific list by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT * FROM lists WHERE id = ?', (list_id,)).fetchone()
            if row:
                return {
                    'id': row[0], 'name': row[1], 'owner_id': row[2],
                    'created_at': row[3], 'modified_at': row[4],
                    'expanded': bool(row[5]), 'hide_done': bool(row[6]),
                    'lists_up_top': bool(row[7]), 'hide_icons': bool(row[8]),
                    'completed': bool(row[9]), 'category_id': row[10],
                    'parent_todo_id': row[11], 'parent_todo_position': row[12],
                    'parent_list_id': row[13], 'parent_list_position': row[14],
                    'priority': row[15]
                }
        return None

    def get_todo_by_id(self, todo_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific todo by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT * FROM todos WHERE id = ?', (todo_id,)).fetchone()
            if row:
                return {
                    'id': row[0], 'text': row[1], 'note': row[2],
                    'pinned': bool(row[3]), 'search_ignored': bool(row[4]),
                    'created_at': row[5], 'modified_at': row[6],
                    'deferred_until': row[7], 'recurrence_rrule': row[8],
                    'recurrence_meta': row[9], 'recurrence_dtstart': row[10],
                    'recurrence_parser_version': row[11], 'list_id': row[12],
                    'priority': row[13], 'lists_up_top': bool(row[14]),
                    'sort_links': bool(row[15])
                }
        return None

    def queue_pending_op(self, op_type: str, data: Dict[str, Any]) -> None:
        """Queue a pending operation for sync."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT INTO pending_ops (op_type, data) VALUES (?, ?)',
                (op_type, json.dumps(data))
            )
            conn.commit()

    def get_pending_ops(self) -> List[Dict[str, Any]]:
        """Get all pending operations."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT id, op_type, data, created_at FROM pending_ops ORDER BY created_at').fetchall()
            return [
                {'id': row[0], 'op_type': row[1], 'data': json.loads(row[2]), 'created_at': row[3]}
                for row in rows
            ]

    def remove_pending_op(self, op_id: int) -> None:
        """Remove a pending operation after successful sync."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM pending_ops WHERE id = ?', (op_id,))
            conn.commit()

    def set_sync_state(self, key: str, value: Any) -> None:
        """Set sync state value."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                'INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)',
                (key, json.dumps(value))
            )
            conn.commit()

    def get_sync_state(self, key: str) -> Any:
        """Get sync state value."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT value FROM sync_state WHERE key = ?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def list_counts(self) -> Dict[str, int]:
        """Get counts of stored items."""
        with sqlite3.connect(self.db_path) as conn:
            list_count = conn.execute('SELECT COUNT(*) FROM lists').fetchone()[0]
            todo_count = conn.execute('SELECT COUNT(*) FROM todos').fetchone()[0]
            pending_count = conn.execute('SELECT COUNT(*) FROM pending_ops').fetchone()[0]
            return {
                'lists': list_count,
                'todos': todo_count,
                'pending': pending_count
            }


# Global instance
local_store = LocalStore()
