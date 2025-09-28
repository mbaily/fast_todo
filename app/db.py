from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.orm.session import Session as SyncSession
from sqlmodel import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from sqlalchemy.pool import NullPool

import os
import logging
import contextvars
import time
import traceback as _traceback
import atexit
import collections
import threading
import json

logger = logging.getLogger(__name__)

# Lightweight runtime metrics collected during traced runs. These are
# best-effort and use simple locking to remain thread-safe for the
# sqlite background threads that the pool may spawn.
_metrics_lock = threading.Lock()
_metrics = {
    'checkout_count': 0,
    'checkin_count': 0,
    'gc_finalizer_count': 0,
    # exec id -> occurrences
    'exec_counts': collections.Counter(),
    # sample stacks per exec id (store at most N samples)
    'exec_samples': {},
}

# How many stack samples to keep per exec token when tracing
EXEC_SAMPLE_LIMIT = 8
# How many stack frames to capture for each sample (increase to reach
# into user code that may be obscured by SQLAlchemy/framework frames).
EXEC_STACK_DEPTH = 200

def _metrics_record(kind: str, trace_id: str | None, stack: str):
    try:
        with _metrics_lock:
            if kind == 'CHECKOUT':
                _metrics['checkout_count'] += 1
            elif kind == 'CHECKIN':
                _metrics['checkin_count'] += 1
            elif kind == 'GC_FINALIZER':
                _metrics['gc_finalizer_count'] += 1
            # if trace_id contains an exec suffix, extract and count it
            if trace_id and '+exec-' in trace_id:
                try:
                    exec_part = trace_id.split('+exec-')[-1]
                    exec_token = 'exec-' + exec_part
                    _metrics['exec_counts'][exec_token] += 1
                    samples = _metrics['exec_samples'].setdefault(exec_token, [])
                    if len(samples) < EXEC_SAMPLE_LIMIT:
                        samples.append(stack)
                except Exception:
                    pass
    except Exception:
        pass


def _tracing_enabled() -> bool:
    """Central helper to decide if tracing instrumentation should run.

    Keep tracing disabled by default unless TRACE_SQL_CONN=1 and the
    environment authorizes it. This lets us remove the instrumentation
    quickly during test runs while preserving the code for future
    debugging.
    """
    # Require an explicit, new opt-in environment variable so tracing
    # cannot be accidentally enabled by setting TRACE_SQL_CONN. This
    # makes instrumentation off by default. To enable, set
    # ENABLE_DB_TRACING=1 in the environment.
    try:
        return os.getenv('ENABLE_DB_TRACING') == '1'
    except Exception:
        return False

def _write_metrics_summary():
    try:
        os.makedirs('debug_logs', exist_ok=True)
        out = {
            'checkout_count': _metrics['checkout_count'],
            'checkin_count': _metrics['checkin_count'],
            'gc_finalizer_count': _metrics['gc_finalizer_count'],
            'top_execs': _metrics['exec_counts'].most_common(30),
        }
        path = os.path.join('debug_logs', 'checkout_summary.json')
        with open(path, 'w') as f:
            json.dump(out, f, indent=2)
    except Exception:
        pass

try:
    atexit.register(_write_metrics_summary)
except Exception:
    pass

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./fast_todo.db")

# Best-effort import-time safeguard: if using a local SQLite file and the
# database already exists with an older schema, add new lightweight columns
# needed by recent features so tests that don't call init_db() still work.
def _sqlite_path_from_url(url: str | None) -> str | None:
    try:
        if not url:
            return None
        if url.startswith('sqlite+aiosqlite:///'):
            path = url.replace('sqlite+aiosqlite:///', '', 1)
        elif url.startswith('sqlite:///'):
            path = url.replace('sqlite:///', '', 1)
        else:
            return None
        # normalize leading ./
        if path.startswith('./'):
            path = path[2:]
        return os.path.abspath(path)
    except Exception:
        return None

def _ensure_sqlite_minimal_migrations(url: str | None) -> None:
    try:
        db_path = _sqlite_path_from_url(url)
        if not db_path:
            return
        if not os.path.exists(db_path):
            # nothing to migrate yet
            return
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            # Helper to add metadata_json column to a table if missing
            def _ensure_metadata_col(table: str):
                try:
                    cur.execute(f"PRAGMA table_info('{table}')")
                    cols = [row[1] for row in cur.fetchall()]
                    if cols and 'metadata_json' not in cols:
                        try:
                            cur.execute(f"ALTER TABLE {table} ADD COLUMN metadata_json TEXT")
                            conn.commit()
                        except Exception:
                            pass
                except Exception:
                    pass
            # If liststate exists but lacks new sublist columns, add them.
            cur.execute("PRAGMA table_info('liststate')")
            cols = [row[1] for row in cur.fetchall()]
            if cols:
                if 'parent_todo_id' not in cols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN parent_todo_id INTEGER")
                        conn.commit()
                    except Exception:
                        # swallow; may fail on some drivers or when already exists
                        pass
                if 'parent_todo_position' not in cols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN parent_todo_position INTEGER")
                        conn.commit()
                    except Exception:
                        pass
                if 'parent_list_id' not in cols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN parent_list_id INTEGER")
                        conn.commit()
                    except Exception:
                        pass
                if 'parent_list_position' not in cols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN parent_list_position INTEGER")
                        conn.commit()
                    except Exception:
                        pass
                # Add priority column if missing
                if 'priority' not in cols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN priority INTEGER")
                        conn.commit()
                    except Exception:
                        pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_parent_todo_id ON liststate(parent_todo_id)")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_parent_todo_pos ON liststate(parent_todo_id, parent_todo_position)")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_parent_list_id ON liststate(parent_list_id)")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_parent_list_pos ON liststate(parent_list_id, parent_list_position)")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_priority ON liststate(priority)")
                    conn.commit()
                except Exception:
                    pass
            # Ensure 'todo' has search_ignored column for ignored-by-search flag
            try:
                cur.execute("PRAGMA table_info('todo')")
                tcols = [row[1] for row in cur.fetchall()]
                if tcols:
                    # Ensure bookmarked flag exists on todo
                    if 'bookmarked' not in tcols:
                        try:
                            cur.execute("ALTER TABLE todo ADD COLUMN bookmarked INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                        except Exception:
                            pass
                    if 'search_ignored' not in tcols:
                        try:
                            cur.execute("ALTER TABLE todo ADD COLUMN search_ignored INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                            try:
                                cur.execute("CREATE INDEX IF NOT EXISTS ix_todo_search_ignored ON todo(search_ignored)")
                                conn.commit()
                            except Exception:
                                pass
                        except Exception:
                            pass
                    # New: ensure calendar_ignored column exists for calendar filtering
                    if 'calendar_ignored' not in tcols:
                        try:
                            cur.execute("ALTER TABLE todo ADD COLUMN calendar_ignored INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                        except Exception:
                            pass
                    # New: ensure first_date_only column exists for controlling date extraction behavior
                    if 'first_date_only' not in tcols:
                        try:
                            cur.execute("ALTER TABLE todo ADD COLUMN first_date_only INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                        except Exception:
                            pass
                    # New: ensure sublists_hide_done column exists for todo page sublists preference
                    if 'sublists_hide_done' not in tcols:
                        try:
                            cur.execute("ALTER TABLE todo ADD COLUMN sublists_hide_done BOOLEAN DEFAULT 0")
                            conn.commit()
                        except Exception:
                            pass
                    try:
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_todo_calendar_ignored ON todo(calendar_ignored)")
                        conn.commit()
                    except Exception:
                        pass
                    try:
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_todo_first_date_only ON todo(first_date_only)")
                        conn.commit()
                    except Exception:
                        pass
                    try:
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_todo_bookmarked ON todo(bookmarked)")
                        conn.commit()
                    except Exception:
                        pass
                # Ensure metadata_json exists on todo
                try:
                    if 'metadata_json' not in tcols:
                        cur.execute("ALTER TABLE todo ADD COLUMN metadata_json TEXT")
                        conn.commit()
                except Exception:
                    pass
            except Exception:
                pass
            # Ensure new per-user collation fields exist on user table
            try:
                cur.execute("PRAGMA table_info('user')")
                ucols = [row[1] for row in cur.fetchall()]
                if ucols:
                    if 'collation_list_id' not in ucols:
                        try:
                            cur.execute("ALTER TABLE user ADD COLUMN collation_list_id INTEGER")
                            conn.commit()
                        except Exception:
                            pass
                    if 'show_collation_indicator' not in ucols:
                        try:
                            cur.execute("ALTER TABLE user ADD COLUMN show_collation_indicator INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                        except Exception:
                            pass
            except Exception:
                pass
            # Ensure itemlink table exists for cross-entity links
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='itemlink'")
                exists = cur.fetchone() is not None
            except Exception:
                exists = True
            if not exists:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS itemlink (
                          id INTEGER PRIMARY KEY,
                          src_type TEXT NOT NULL,
                          src_id INTEGER NOT NULL,
                          tgt_type TEXT NOT NULL,
                          tgt_id INTEGER NOT NULL,
                          label TEXT,
                          position INTEGER,
                          owner_id INTEGER NOT NULL,
                          created_at DATETIME
                        )
                        """
                    )
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_itemlink_edge ON itemlink(src_type, src_id, tgt_type, tgt_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_itemlink_src ON itemlink(src_type, src_id, position)")
                except Exception:
                    pass
            # Ensure usercollation table exists for per-user collations
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usercollation'")
                exists_uc = cur.fetchone() is not None
            except Exception:
                exists_uc = True
            if not exists_uc:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS usercollation (
                          user_id INTEGER NOT NULL,
                          list_id INTEGER NOT NULL,
                          active INTEGER DEFAULT 1 NOT NULL,
                          created_at DATETIME,
                          PRIMARY KEY (user_id, list_id)
                        )
                        """
                    )
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_usercollation_active ON usercollation(active)")
                except Exception:
                    pass
                # Ensure recenttodovisit table exists for per-user recent todos
                try:
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recenttodovisit'")
                    exists_rtv = cur.fetchone() is not None
                except Exception:
                    exists_rtv = True
                if not exists_rtv:
                    try:
                        cur.execute(
                            """
                            CREATE TABLE IF NOT EXISTS recenttodovisit (
                              user_id INTEGER NOT NULL,
                              todo_id INTEGER NOT NULL,
                              visited_at DATETIME,
                              position INTEGER,
                              PRIMARY KEY (user_id, todo_id)
                            )
                            """
                        )
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_recenttodovisit_user_pos ON recenttodovisit(user_id, position)")
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_recenttodovisit_user_visited ON recenttodovisit(user_id, visited_at DESC)")
                    except Exception:
                        pass
            # Ensure userlistprefs table exists for per-user list prefs
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='userlistprefs'")
                exists_ulp = cur.fetchone() is not None
            except Exception:
                exists_ulp = True
            if not exists_ulp:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS userlistprefs (
                          user_id INTEGER NOT NULL,
                          list_id INTEGER NOT NULL,
                          completed_after INTEGER DEFAULT 0 NOT NULL,
                          created_at DATETIME,
                          PRIMARY KEY (user_id, list_id)
                        )
                        """
                    )
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_userlistprefs_completed_after ON userlistprefs(completed_after)")
                except Exception:
                    pass
            # Ensure treeview and treeviewitem tables exist for saved tree views
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='treeview'")
                exists_tv = cur.fetchone() is not None
            except Exception:
                exists_tv = True
            if not exists_tv:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS treeview (
                          id INTEGER PRIMARY KEY,
                          user_id INTEGER NOT NULL,
                          name TEXT NOT NULL,
                          created_at DATETIME,
                          metadata_json TEXT
                        )
                        """
                    )
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_treeview_user_name ON treeview(user_id, name)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_treeview_user ON treeview(user_id)")
                except Exception:
                    pass
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='treeviewitem'")
                exists_tvi = cur.fetchone() is not None
            except Exception:
                exists_tvi = True
            if not exists_tvi:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS treeviewitem (
                          id INTEGER PRIMARY KEY,
                          view_id INTEGER NOT NULL,
                          item_type TEXT NOT NULL,
                          item_id INTEGER NOT NULL,
                          position INTEGER,
                          created_at DATETIME,
                          metadata_json TEXT
                        )
                        """
                    )
                    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_treeviewitem_unique ON treeviewitem(view_id, item_type, item_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_treeviewitem_view_pos ON treeviewitem(view_id, position)")
                except Exception:
                    pass
            # Ensure itemlink.owner_id column exists for older DBs and backfill from liststate
            try:
                cur.execute("PRAGMA table_info('itemlink')")
                il_cols = [row[1] for row in cur.fetchall()]
                if il_cols and 'owner_id' not in il_cols:
                    try:
                        cur.execute("ALTER TABLE itemlink ADD COLUMN owner_id INTEGER")
                        # Best-effort backfill: for links where source is a list, set owner_id to that list's owner
                        try:
                            cur.execute(
                                """
                                UPDATE itemlink
                                SET owner_id = (
                                  SELECT liststate.owner_id FROM liststate WHERE liststate.id = itemlink.src_id
                                )
                                WHERE itemlink.src_type = 'list' AND owner_id IS NULL
                                """
                            )
                        except Exception:
                            pass
                        # Create index for owner_id to speed lookups
                        try:
                            cur.execute("CREATE INDEX IF NOT EXISTS ix_itemlink_owner ON itemlink(owner_id)")
                        except Exception:
                            pass
                        conn.commit()
                    except Exception:
                        pass
            except Exception:
                pass
            # Ensure category table has sort_alphanumeric column for older DBs
            try:
                cur.execute("PRAGMA table_info('category')")
                cat_cols = [row[1] for row in cur.fetchall()]
                if cat_cols:
                    # Ensure helpful owner+position index exists for ordering per user
                    try:
                        cur.execute("CREATE INDEX IF NOT EXISTS ix_category_owner_position ON category(owner_id, position)")
                        conn.commit()
                    except Exception:
                        pass
                    # Add new owner_id column for user-specific categories
                    if 'owner_id' not in cat_cols:
                        try:
                            cur.execute("ALTER TABLE category ADD COLUMN owner_id INTEGER")
                            conn.commit()
                        except Exception:
                            # swallow; may fail on some drivers or when already exists
                            pass
                        # Best-effort index on owner_id to speed per-user queries
                        try:
                            cur.execute("CREATE INDEX IF NOT EXISTS ix_category_owner_id ON category(owner_id)")
                            conn.commit()
                        except Exception:
                            pass
                    # Best-effort: create unique index for (owner_id, name). If it fails due to duplicates, ignore here.
                    try:
                        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_category_owner_name ON category(owner_id, name)")
                        conn.commit()
                    except Exception:
                        pass
                    # Ensure sort_alphanumeric exists (older DBs)
                    if 'sort_alphanumeric' not in cat_cols:
                        try:
                            cur.execute("ALTER TABLE category ADD COLUMN sort_alphanumeric INTEGER DEFAULT 0 NOT NULL")
                            conn.commit()
                        except Exception:
                            # swallow; may fail on some drivers or when already exists
                            pass
                    # Ensure metadata_json on category
                    try:
                        if 'metadata_json' not in cat_cols:
                            cur.execute("ALTER TABLE category ADD COLUMN metadata_json TEXT")
                            conn.commit()
                    except Exception:
                        pass
            except Exception:
                pass

            # Ensure metadata_json on core/user-scoped tables (best-effort)
            try:
                for tbl in (
                    'serverstate','liststate','hashtag','completiontype','user','session','syncoperation',
                    'tombstone','recentlistvisit','recenttodovisit','completedoccurrence','trashmeta','listtrashmeta',
                    'ignoredscope','sshpublickey','pushsubscription','itemlink','usercollation','userlistprefs','eventlog'
                ):
                    _ensure_metadata_col(tbl)
            except Exception:
                pass
            # Ensure eventlog table exists for per-user html_no_js event logs
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='eventlog'")
                exists_el = cur.fetchone() is not None
            except Exception:
                exists_el = True
            if not exists_el:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS eventlog (
                          id INTEGER PRIMARY KEY,
                          user_id INTEGER NOT NULL,
                          message TEXT NOT NULL,
                          item_type TEXT,
                          item_id INTEGER,
                          url TEXT,
                          label TEXT,
                          created_at DATETIME,
                          metadata_json TEXT
                        )
                        """
                    )
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_eventlog_user_created ON eventlog(user_id, created_at DESC)")
                except Exception:
                    pass
            # Ensure journalentry table exists for per-todo journals
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='journalentry'")
                exists_je = cur.fetchone() is not None
            except Exception:
                exists_je = True
            if not exists_je:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS journalentry (
                          id INTEGER PRIMARY KEY,
                          todo_id INTEGER NOT NULL,
                          user_id INTEGER NOT NULL,
                          content TEXT NOT NULL,
                          created_at DATETIME,
                          modified_at DATETIME,
                          metadata_json TEXT
                        )
                        """
                    )
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_journalentry_todo_id ON journalentry(todo_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_journalentry_user_id ON journalentry(user_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_journalentry_created ON journalentry(created_at DESC)")
                except Exception:
                    pass
            # Ensure listnote table exists for per-list notes
            try:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='listnote'")
                exists_ln = cur.fetchone() is not None
            except Exception:
                exists_ln = True
            if not exists_ln:
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS listnote (
                          id INTEGER PRIMARY KEY,
                          list_id INTEGER NOT NULL,
                          user_id INTEGER NOT NULL,
                          content TEXT NOT NULL,
                          created_at DATETIME,
                          modified_at DATETIME,
                          metadata_json TEXT
                        )
                        """
                    )
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_listnote_list_id ON listnote(list_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_listnote_user_id ON listnote(user_id)")
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_listnote_created ON listnote(created_at DESC)")
                except Exception:
                    pass
            # Ensure bookmarked flag exists on liststate and helpful index
            try:
                cur.execute("PRAGMA table_info('liststate')")
                lcols = [row[1] for row in cur.fetchall()]
                if lcols and 'bookmarked' not in lcols:
                    try:
                        cur.execute("ALTER TABLE liststate ADD COLUMN bookmarked INTEGER DEFAULT 0 NOT NULL")
                        conn.commit()
                    except Exception:
                        pass
                # Ensure pinned flag exists on liststate and helpful index
                try:
                    if lcols and 'pinned' not in lcols:
                        cur.execute("ALTER TABLE liststate ADD COLUMN pinned INTEGER DEFAULT 0 NOT NULL")
                        conn.commit()
                except Exception:
                    pass
                # Ensure sublists_hide_done preference column exists
                try:
                    if lcols and 'sublists_hide_done' not in lcols:
                        cur.execute("ALTER TABLE liststate ADD COLUMN sublists_hide_done BOOLEAN DEFAULT 0")
                        conn.commit()
                        # refresh lcols so subsequent logic sees it
                        try:
                            cur.execute("PRAGMA table_info('liststate')")
                            lcols = [row[1] for row in cur.fetchall()]
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_bookmarked ON liststate(bookmarked)")
                    conn.commit()
                except Exception:
                    pass
                try:
                    cur.execute("CREATE INDEX IF NOT EXISTS ix_liststate_pinned ON liststate(pinned)")
                    conn.commit()
                except Exception:
                    pass
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        # best-effort; do not raise during import
        pass

_ensure_sqlite_minimal_migrations(DATABASE_URL)

# Use NullPool to avoid connection-pool objects being bound to a specific
# event loop (which can cause 'bound to a different event loop' errors
# during heavy concurrency in tests).
engine = create_async_engine(DATABASE_URL, echo=False, future=True, poolclass=NullPool)

# Context var used to propagate a short-lived exec trace id from session
# execute call into the pool checkout event listener so we can correlate
# high-level coroutine callstacks with low-level connection checkout logs
current_exec_id: contextvars.ContextVar[str | None] = contextvars.ContextVar('current_exec_id', default=None)

# For sync code paths that may execute on other threads (e.g. aiosqlite
# adapters), contextvars won't propagate. Keep a best-effort mapping from
# thread id to the most-recent exec id so the pool listener can correlate
# checkouts that happen on a different thread.
_thread_exec_map_lock = threading.Lock()
_thread_exec_map: dict[int, str] = {}


class TracedAsyncSession(AsyncSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # flag set in __aenter__/__aexit__ to indicate this session is
        # being used as an async context manager. If False, we treat the
        # session as ephemeral and auto-close after a single exec/execute
        # to avoid leaking connections when callers forget to use
        # "async with async_session() as sess".
        self._in_context = False

    async def execute(self, *args, **kwargs):
        # create a short-lived exec id and record the coroutine stack
        exec_id = f"exec-{id(self)}-{int(time.time() * 1e6)}"
        token = None
        if _tracing_enabled():
            token = current_exec_id.set(exec_id)
            # record on this thread so checkout listeners on other
            # threads can still correlate using thread id
            try:
                tid = threading.get_ident()
                with _thread_exec_map_lock:
                    _thread_exec_map[tid] = exec_id
            except Exception:
                pass
        try:
            try:
                # best-effort log of the coroutine stack for correlation
                if _tracing_enabled():
                    stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
                    try:
                        os.makedirs('debug_logs', exist_ok=True)
                        with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                            f.write(f"EXEC {exec_id}\n")
                            # also record a single caller frame outside of this db module
                            caller = None
                            for line in stack.splitlines():
                                if '/home/mbaily/fast_todo/app/db.py' in line:
                                    continue
                                if '/home/mbaily/fast_todo/' in line:
                                    caller = line.strip()
                                    break
                            if caller:
                                f.write(f"CALLER {caller}\n")
                            f.write(stack)
                            f.write("--- end ---\n\n")
                    except Exception:
                        pass
            except Exception:
                pass
            res = await super().execute(*args, **kwargs)
            # Note: do not close the session here. Closing a session
            # prematurely (after a single execute) can detach instances
            # or break callers that expect to reuse the session. We
            # preserve tracing but leave lifecycle management to callers
            # or explicit context-manager use.
            return res
        finally:
            try:
                # clear thread map entry when resetting
                try:
                    tid = threading.get_ident()
                    with _thread_exec_map_lock:
                        _thread_exec_map.pop(tid, None)
                except Exception:
                    pass
                if token is not None:
                    current_exec_id.reset(token)
            except Exception:
                pass

    async def exec(self, *args, **kwargs):
        # Mirror SQLModel AsyncSession.exec entrypoint so we capture most
        # session queries that drive pool checkouts.
        exec_id = f"exec-{id(self)}-{int(time.time() * 1e6)}"
        token = None
        if _tracing_enabled():
            token = current_exec_id.set(exec_id)
        try:
            if _tracing_enabled():
                try:
                    stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
                    try:
                        os.makedirs('debug_logs', exist_ok=True)
                        with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                            f.write(f"EXEC {exec_id}\n")
                            f.write(stack)
                            f.write("--- end ---\n\n")
                    except Exception:
                        pass
                except Exception:
                    pass
            res = await super().exec(*args, **kwargs)
            # See note in execute(): do not auto-close the session here.
            return res
        finally:
            try:
                try:
                    tid = threading.get_ident()
                    with _thread_exec_map_lock:
                        _thread_exec_map.pop(tid, None)
                except Exception:
                    pass
                if token is not None:
                    current_exec_id.reset(token)
            except Exception:
                pass

    async def __aenter__(self):
        # Log session context enter to help map where sessions are created
        try:
            if _tracing_enabled():
                sid = f'sess-{id(self)}'
                stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"CALLSITE_SESSION_ENTER {sid}\n")
                        f.write(stack)
                        f.write('--- end ---\n\n')
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._in_context = True
        except Exception:
            pass
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if _tracing_enabled():
                sid = f'sess-{id(self)}'
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"CALLSITE_SESSION_EXIT {sid}\n--- end ---\n\n")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._in_context = False
        except Exception:
            pass
        return await super().__aexit__(exc_type, exc, tb)


class TracedSyncSession(SyncSession):
    def exec(self, *args, **kwargs):
        exec_id = f"exec-sync-{id(self)}-{int(time.time() * 1e6)}"
        token = None
        try:
            # set contextvar so the engine/pool checkout listener can pick up
            # this exec id when the sync code path performs a connection
            # checkout.
            if _tracing_enabled():
                try:
                    token = current_exec_id.set(exec_id)
                    try:
                        tid = threading.get_ident()
                        with _thread_exec_map_lock:
                            _thread_exec_map[tid] = exec_id
                    except Exception:
                        pass
                except Exception:
                    token = None
                stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"EXEC {exec_id}\n")
                        # record a single caller frame outside of this db module
                        caller = None
                        for line in stack.splitlines():
                            if '/home/mbaily/fast_todo/app/db.py' in line:
                                continue
                            if '/home/mbaily/fast_todo/' in line:
                                caller = line.strip()
                                break
                        if caller:
                            f.write(f"CALLER {caller}\n")
                        f.write(stack)
                        f.write("--- end ---\n\n")
                except Exception:
                    pass
            res = super().exec(*args, **kwargs)
            # Note: do not auto-close the session here. Closing a session
            # prematurely (after a single exec) can detach instances or
            # break callers that expect to reuse the session. We preserve
            # tracing but leave lifecycle management to callers or explicit
            # context-manager use.
            return res
        finally:
            try:
                if token is not None:
                    current_exec_id.reset(token)
            except Exception:
                pass

    def __enter__(self):
        try:
            if _tracing_enabled():
                sid = f'sess-sync-{id(self)}'
                stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"CALLSITE_SESSION_ENTER {sid}\n")
                        f.write(stack)
                        f.write('--- end ---\n\n')
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._in_context = True
        except Exception:
            pass
        return super().__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            if _tracing_enabled():
                sid = f'sess-sync-{id(self)}'
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"CALLSITE_SESSION_EXIT {sid}\n--- end ---\n\n")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._in_context = False
        except Exception:
            pass
        return super().__exit__(exc_type, exc, tb)

# Ensure AsyncSession uses our traced sync session class for its sync_session
TracedAsyncSession.sync_session_class = TracedSyncSession


async_session = sessionmaker(engine, class_=TracedAsyncSession, expire_on_commit=False)


# Optional runtime tracing: attach pool/engine event listeners only when
# tracing is enabled. Guarded via _tracing_enabled() so instrumentation
# can be toggled off quickly without removing the code.
if _tracing_enabled():
    try:
        from sqlalchemy import event
        import traceback as _traceback
        import threading as _threading
        import weakref as _weakref

        def _write_log(kind: str, trace_id: str | None, stack: str):
            try:
                os.makedirs('debug_logs', exist_ok=True)
                log_path = os.path.join('debug_logs', 'sql_checkout_traces.log')
                with open(log_path, 'a') as f:
                    f.write(f"{kind} {trace_id or 'unknown'}\n")
                    f.write(stack)
                    f.write("--- end ---\n\n")
                try:
                    # record lightweight runtime metrics
                    _metrics_record(kind, trace_id, stack)
                except Exception:
                    pass
            except Exception:
                # swallow; best-effort only
                pass


        def _on_checkout(dbapi_con, con_record, con_proxy):
            stack = ''.join(_traceback.format_stack(limit=EXEC_STACK_DEPTH))
            exec_id = None
            try:
                exec_id = current_exec_id.get() if 'current_exec_id' in globals() else None
            except Exception:
                exec_id = None
            # If contextvar didn't propagate (likely because checkout runs on
            # a different thread), check the thread->exec map for a recent
            # exec id recorded by the caller thread.
            if not exec_id:
                try:
                    tid = _threading.get_ident()
                    with _thread_exec_map_lock:
                        exec_id = _thread_exec_map.get(tid)
                except Exception:
                    exec_id = exec_id
            trace_id = f"trace-{id(con_record)}-{int(_threading.get_ident())}" + (f"+{exec_id}" if exec_id else "")
            try:
                con_record.info['trace_id'] = trace_id
            except Exception:
                pass
            _write_log('CHECKOUT', trace_id, stack)

            def _finalizer(s=stack, tid=trace_id):
                _write_log('GC_FINALIZER', tid, s)

            try:
                _weakref.finalize(con_record, _finalizer)
            except Exception:
                pass

        def _on_checkin(dbapi_con, con_record):
            try:
                tid = con_record.info.get('trace_id') if hasattr(con_record, 'info') else None
            except Exception:
                tid = None
            _write_log('CHECKIN', tid, 'checkin')

        try:
            # Attach to the engine's pool and pool classes
            try:
                event.listen(engine.sync_engine.pool, 'checkout', _on_checkout)
            except Exception:
                pass
            try:
                event.listen(engine.sync_engine, 'checkout', _on_checkout)
            except Exception:
                pass
            try:
                event.listen(engine.sync_engine, 'checkin', _on_checkin)
            except Exception:
                pass
            # Pool classes
            try:
                from sqlalchemy.pool import Pool as _Pool, NullPool as _NullPool
                event.listen(_Pool, 'checkout', _on_checkout)
                event.listen(_Pool, 'checkin', _on_checkin)
                event.listen(_NullPool, 'checkout', _on_checkout)
                event.listen(_NullPool, 'checkin', _on_checkin)
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        pass

async def init_db():
    async with engine.begin() as conn:
        # create tables
        await conn.run_sync(SQLModel.metadata.create_all)
        # Ensure new recurrence columns exist on the todo table for older DBs.
        # SQLite's CREATE TABLE won't alter existing tables, so add columns
        # if they are missing. This keeps tests and in-place DBs working
        # without a full Alembic migration during development.
        try:
            res = await conn.execute(text("PRAGMA table_info('todo')"))
            cols = [r[1] for r in res.fetchall()]
            add_sql = []
            # Add bookmarked flag to todo if missing
            if 'bookmarked' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN bookmarked INTEGER DEFAULT 0 NOT NULL")
            if 'recurrence_rrule' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN recurrence_rrule TEXT")
            if 'recurrence_meta' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN recurrence_meta TEXT")
            if 'recurrence_dtstart' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN recurrence_dtstart DATETIME")
            if 'recurrence_parser_version' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN recurrence_parser_version TEXT")
            # Add priority column to todo table if missing
            if 'priority' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN priority INTEGER")
            # metadata_json column
            if 'metadata_json' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN metadata_json TEXT")
            # plain_dates_meta column for persisted non-recurring date matches
            if 'plain_dates_meta' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN plain_dates_meta TEXT")
            # first_date_only toggle column
            if 'first_date_only' not in cols:
                add_sql.append("ALTER TABLE todo ADD COLUMN first_date_only INTEGER DEFAULT 0 NOT NULL")
            for s in add_sql:
                try:
                    await conn.execute(text(s))
                except Exception:
                    logger.exception('failed to add column during init_db: %s', s)
        except Exception:
            # Best-effort only; do not fail init_db if PRAGMA isn't supported
            logger.exception('failed to ensure recurrence columns in init_db')
        # Ensure new recursive-lists column exists on liststate for older DBs.
        # This keeps tests and dev DBs working without requiring a manual migration.
        try:
            res = await conn.execute(text("PRAGMA table_info('liststate')"))
            cols = [r[1] for r in res.fetchall()]
            # Add bookmarked flag to liststate if missing
            if 'bookmarked' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN bookmarked INTEGER DEFAULT 0 NOT NULL"))
                except Exception:
                    logger.exception('failed to add bookmarked to liststate during init_db')
            if 'parent_todo_id' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN parent_todo_id INTEGER"))
                except Exception:
                    logger.exception('failed to add parent_todo_id to liststate during init_db')
            if 'parent_todo_position' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN parent_todo_position INTEGER"))
                except Exception:
                    logger.exception('failed to add parent_todo_position to liststate during init_db')
            if 'parent_list_id' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN parent_list_id INTEGER"))
                except Exception:
                    logger.exception('failed to add parent_list_id to liststate during init_db')
            if 'parent_list_position' not in cols:
                try:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN parent_list_position INTEGER"))
                except Exception:
                    logger.exception('failed to add parent_list_position to liststate during init_db')
            # Ensure index exists (SQLite supports IF NOT EXISTS)
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_liststate_parent_todo_id ON liststate(parent_todo_id)"))
            except Exception:
                logger.exception('failed to create ix_liststate_parent_todo_id during init_db')
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_liststate_parent_todo_pos ON liststate(parent_todo_id, parent_todo_position)"))
            except Exception:
                logger.exception('failed to create ix_liststate_parent_todo_pos during init_db')
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_liststate_parent_list_id ON liststate(parent_list_id)"))
            except Exception:
                logger.exception('failed to create ix_liststate_parent_list_id during init_db')
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_liststate_parent_list_pos ON liststate(parent_list_id, parent_list_position)"))
            except Exception:
                logger.exception('failed to create ix_liststate_parent_list_pos during init_db')
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_todo_priority ON todo(priority)"))
            except Exception:
                logger.exception('failed to create ix_todo_priority during init_db')
            # Indices for bookmarked flags
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_todo_bookmarked ON todo(bookmarked)"))
            except Exception:
                logger.exception('failed to create ix_todo_bookmarked during init_db')
            try:
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_liststate_bookmarked ON liststate(bookmarked)"))
            except Exception:
                logger.exception('failed to create ix_liststate_bookmarked during init_db')
            # metadata_json column on liststate
            try:
                if 'metadata_json' not in cols:
                    await conn.execute(text("ALTER TABLE liststate ADD COLUMN metadata_json TEXT"))
            except Exception:
                pass
        except Exception:
            logger.exception('failed to ensure parent_todo_id on liststate in init_db')
        # Ensure metadata_json on other tables (best-effort)
        try:
            tables = (
                'serverstate','hashtag','completiontype','user','session','syncoperation','tombstone',
                'recentlistvisit','recenttodovisit','completedoccurrence','trashmeta','listtrashmeta',
                'ignoredscope','sshpublickey','pushsubscription','itemlink','usercollation','userlistprefs','category'
            )
            for tbl in tables:
                try:
                    res = await conn.execute(text(f"PRAGMA table_info('{tbl}')"))
                    cols = [r[1] for r in res.fetchall()]
                    if cols and 'metadata_json' not in cols:
                        try:
                            await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN metadata_json TEXT"))
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        # Ensure ItemLink indices exist
        try:
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_itemlink_edge ON itemlink(src_type, src_id, tgt_type, tgt_id)"))
        except Exception:
            pass
        try:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_itemlink_src ON itemlink(src_type, src_id, position)"))
        except Exception:
            pass
        # Ensure journalentry table/indices exist (best-effort for older DBs)
        try:
            res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='journalentry'"))
            exists = res.fetchone() is not None
        except Exception:
            exists = True
        if not exists:
            try:
                await conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS journalentry (
                      id INTEGER PRIMARY KEY,
                      todo_id INTEGER NOT NULL,
                      user_id INTEGER NOT NULL,
                      content TEXT NOT NULL,
                      created_at DATETIME,
                      modified_at DATETIME,
                      metadata_json TEXT
                    )
                    """
                ))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_journalentry_todo_id ON journalentry(todo_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_journalentry_user_id ON journalentry(user_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_journalentry_created ON journalentry(created_at DESC)"))
            except Exception:
                pass
        # Ensure listnote table/indices exist (best-effort for older DBs)
        try:
            res = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='listnote'"))
            exists_ln = res.fetchone() is not None
        except Exception:
            exists_ln = True
        if not exists_ln:
            try:
                await conn.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS listnote (
                      id INTEGER PRIMARY KEY,
                      list_id INTEGER NOT NULL,
                      user_id INTEGER NOT NULL,
                      content TEXT NOT NULL,
                      created_at DATETIME,
                      modified_at DATETIME,
                      metadata_json TEXT
                    )
                    """
                ))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listnote_list_id ON listnote(list_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listnote_user_id ON listnote(user_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_listnote_created ON listnote(created_at DESC)"))
            except Exception:
                pass
        # defensive dedupe: if earlier test runs created duplicate rows
        # (possible before unique indexes were present), remove duplicates
        # keeping the first row for each unique key, then create the
        # unique indexes.
        try:
            # completiontype: keep the smallest rowid per (list_id, name)
            await conn.execute(text(
                "DELETE FROM completiontype WHERE rowid NOT IN (SELECT MIN(rowid) FROM completiontype GROUP BY list_id, name)"
            ))
        except Exception:
            # best-effort cleanup; log unexpected failures
            logger.exception("failed to dedupe completiontype rows during init_db")
        try:
            # hashtag: keep the smallest rowid per tag
            await conn.execute(text(
                "DELETE FROM hashtag WHERE rowid NOT IN (SELECT MIN(rowid) FROM hashtag GROUP BY tag)"
            ))
        except Exception:
            logger.exception("failed to dedupe hashtag rows during init_db")
        # categories: dedupe per (owner_id, name) before creating unique index
        try:
            # Remap liststate.category_id to the smallest category.id per (owner_id, name)
            await conn.execute(text(
                """
                UPDATE liststate
                SET category_id = (
                  SELECT MIN(c2.id)
                  FROM category c2
                  WHERE c2.owner_id = (SELECT owner_id FROM category c WHERE c.id = liststate.category_id)
                    AND c2.name = (SELECT name FROM category c WHERE c.id = liststate.category_id)
                )
                WHERE category_id IS NOT NULL
                  AND category_id NOT IN (
                    SELECT MIN(id) FROM category GROUP BY owner_id, name
                  )
                """
            ))
            # Delete duplicate category rows after remapping
            await conn.execute(text(
                "DELETE FROM category WHERE id NOT IN (SELECT MIN(id) FROM category GROUP BY owner_id, name)"
            ))
        except Exception:
            # best-effort; continue even if the above fails
            logger.exception("failed to dedupe category rows during init_db")
    # We no longer dedupe liststate rows here because the application now
    # allows multiple lists with the same name per user. Leaving the old
    # dedupe SQL would remove legitimate duplicate lists created by the
    # application.

        # create unique indexes (IF NOT EXISTS). If creation fails due to any
        # race or leftover constraint issue, ignore and continue.
        try:
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_completiontype_list_id_name ON completiontype(list_id, name)"))
        except Exception:
            logger.exception("failed to create ix_completiontype_list_id_name index during init_db")
        try:
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_hashtag_tag ON hashtag(tag)"))
        except Exception:
            logger.exception("failed to create ix_hashtag_tag index during init_db")
        # Category per-user uniqueness and helpful ordering index
        try:
            await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_category_owner_name ON category(owner_id, name)"))
        except Exception:
            logger.exception("failed to create uq_category_owner_name index during init_db")
        try:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_category_owner_position ON category(owner_id, position)"))
        except Exception:
            logger.exception("failed to create ix_category_owner_position during init_db")
        try:
            # Ensure any previous owner-scoped unique index is removed so the
            # DB allows multiple lists with the same name per user.
            await conn.execute(text("DROP INDEX IF EXISTS ix_liststate_owner_id_name"))
        except Exception:
            logger.exception("failed to drop ix_liststate_owner_id_name during init_db")
        try:
            # Also remove the old global unique index on name if it exists.
            await conn.execute(text("DROP INDEX IF EXISTS ix_liststate_name"))
        except Exception:
            logger.exception("failed to drop ix_liststate_name during init_db")
    # ensure ServerState exists
    from .models import ServerState
    async with async_session() as sess:
        res = await sess.exec(select(ServerState))
        if not res.first():
            ss = ServerState()
            sess.add(ss)
            await sess.commit()


# Ensure engine sync pool is disposed at interpreter exit to avoid pool
# finalizer warnings about non-checked-in connections during pytest
# teardown or interpreter shutdown. Use sync_engine.dispose() which is a
# synchronous operation on the underlying sync Engine/pool.
try:
    import atexit

    def _dispose_sync_engine():
        try:
            # engine.sync_engine is available on AsyncEngine
            if hasattr(engine, 'sync_engine') and engine.sync_engine is not None:
                try:
                    engine.sync_engine.dispose()
                except Exception:
                    pass
        except Exception:
            pass

    atexit.register(_dispose_sync_engine)
except Exception:
    pass
