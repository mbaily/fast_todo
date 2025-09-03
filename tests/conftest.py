import sys
import pathlib
import pytest
import pytest_asyncio
import httpx
import os
from sqlmodel import select
import warnings
import os
import traceback
import weakref
try:
    from sqlalchemy.exc import SAWarning
    warnings.filterwarnings('ignore', category=SAWarning)
except Exception:
    # If SQLAlchemy not available at import, ignore
    pass

# Ensure a secure SECRET_KEY is available during tests so the app lifespan
# check in `app.main` doesn't raise. Tests may run in environments where the
# normal env isn't set; set a deterministic test-only key here.
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-unit-tests')
# Some SQLAlchemy connection cleanup messages are emitted during async teardown
# in a way that bypasses category-only filters; add a message-based ignore to
# reduce noisy test output.
warnings.filterwarnings('ignore', message='The garbage collector is trying to clean up non-checked-in connection')

# Reduce SQLAlchemy logger verbosity during tests (silences connection cleanup logs)
import logging as _logging
for _name in ('sqlalchemy', 'sqlalchemy.engine', 'sqlalchemy.pool', 'sqlmodel'):
    try:
        _logging.getLogger(_name).setLevel(_logging.ERROR)
    except Exception:
        pass


# Optional diagnostic: capture stacktraces when SQLAlchemy checks out a
# connection from the pool. Enable by setting environment variable
# TRACE_SQL_CONN=1 when running pytest. This helps locate the high-level
# caller that checks out a connection but never returns it (which triggers
# the pool finalizer warnings seen during test teardown).
if os.getenv('TRACE_SQL_CONN') == '1':
    try:
        import threading
        from sqlalchemy import event
        # import the engine lazily so tests that don't enable tracing are
        # unaffected during collection.
        from app import db as app_db

        def _trace_checkout(dbapi_con, con_record, con_proxy):
            # Capture a concise stack trace and attach it to the connection
            # record so it appears alongside other debug output if needed.
            stack = ''.join(traceback.format_stack(limit=40))
            try:
                con_record.info['checkout_stack'] = stack
            except Exception:
                # best-effort only; don't fail test collection if this errors
                pass
            # persist the stack to a log file so it's available even if
            # interpreter shutdown interleaves output.
            try:
                os.makedirs('debug_logs', exist_ok=True)
                trace_id = f"trace-{id(con_record)}-{int(threading.get_ident())}"
                log_path = os.path.join('debug_logs', 'sql_checkout_traces.log')
                with open(log_path, 'a') as f:
                    f.write(f"CHECKOUT {trace_id}\n")
                    f.write(stack)
                    f.write("--- end checkout ---\n\n")
                # attach trace id to the record for correlation
                try:
                    con_record.info['trace_id'] = trace_id
                except Exception:
                    pass
            except Exception:
                # fall back to printing if file write fails
                print('\n--- SQL CONNECTION CHECKOUT TRACE ---')
                print(stack)
                print('--- end checkout trace ---\n')
            try:
                # Register a finalizer that will write to the same log if the
                # connection record is garbage-collected without being
                # returned to the pool. This helps correlate the pool finalizer
                # SAWarning with the original checkout.
                def _finalizer(s=stack, tid=None):
                    try:
                        os.makedirs('debug_logs', exist_ok=True)
                        log_path = os.path.join('debug_logs', 'sql_checkout_traces.log')
                        with open(log_path, 'a') as f:
                            f.write(f"GC_FINALIZER {tid or 'unknown'}\n")
                            f.write(s)
                            f.write("--- end finalizer ---\n\n")
                    except Exception:
                        # best-effort only
                        pass

                trace_id = con_record.info.get('trace_id') if hasattr(con_record, 'info') else None
                weakref.finalize(con_record, _finalizer, stack, trace_id)
            except Exception:
                pass

        try:
            # create_async_engine exposes a sync_engine we can instrument.
            # Listen on both the pool and the engine to increase coverage
            # (different pool implementations may route events differently).
            try:
                event.listen(app_db.engine.sync_engine.pool, 'checkout', _trace_checkout)
            except Exception:
                # best-effort only; continue to also attach to the engine
                pass

            # Also attach to pool classes directly to catch checkouts for
            # pool implementations created after import (NullPool etc.).
            try:
                from sqlalchemy.pool import Pool as _Pool, NullPool as _NullPool
                try:
                    event.listen(_Pool, 'checkout', _trace_checkout)
                except Exception:
                    pass
                try:
                    event.listen(_NullPool, 'checkout', _trace_checkout)
                except Exception:
                    pass
            except Exception:
                pass

            def _trace_connect(dbapi_con, con_record):
                stack = ''.join(traceback.format_stack(limit=40))
                print('\n--- SQL CONNECTION CONNECT TRACE ---')
                print(stack)
                print('--- end connect trace ---\n')

            def _trace_checkin(dbapi_con, con_record):
                try:
                    tid = con_record.info.get('trace_id') if hasattr(con_record, 'info') else None
                except Exception:
                    tid = None
                try:
                    os.makedirs('debug_logs', exist_ok=True)
                    log_path = os.path.join('debug_logs', 'sql_checkout_traces.log')
                    with open(log_path, 'a') as f:
                        f.write(f"CHECKIN {tid or 'unknown'}\n--- end checkin ---\n\n")
                except Exception:
                    print('\n--- SQL CONNECTION CHECKIN TRACE ---')
                    print(f'checkin {tid or "unknown"}')
                    print('--- end checkin ---\n')

            # Attach to engine-level checkout/connect events as well.

            # Attach to engine-level checkout/connect events as well.
            try:
                event.listen(app_db.engine.sync_engine, 'checkout', _trace_checkout)
            except Exception:
                pass
            try:
                event.listen(app_db.engine.sync_engine, 'connect', _trace_connect)
            except Exception:
                pass
            try:
                event.listen(app_db.engine.sync_engine, 'checkin', _trace_checkin)
            except Exception:
                pass
        except Exception as e:
            print('failed to install SQL checkout listener:', e)
    except Exception:
        # If SQLAlchemy isn't importable or other errors occur, ignore.
        pass

# Replace httpx.AsyncClient with an auto-authenticating subclass for tests.
# Tests in this repo often create AsyncClient directly; since lists now
# require authentication we transparently obtain a test token and attach it
# so existing tests continue to work without editing each file.
_OrigAsyncClient = httpx.AsyncClient


class AutoAuthAsyncClient(_OrigAsyncClient):
    async def __aenter__(self):
        client = await super().__aenter__()
        # ensure a stable test user exists and fetch a token
        from app.db import async_session
        from app.models import User
        from app.auth import pwd_context

        async with async_session() as sess:
            q = await sess.exec(select(User).where(User.username == "__autotest__"))
            u = q.first()
            if not u:
                ph = pwd_context.hash("p")
                u = User(username="__autotest__", password_hash=ph, is_admin=True)
                sess.add(u)
                try:
                    await sess.commit()
                except Exception:
                    await sess.rollback()
        # small diagnostic: if TRACE_SQL_CONN enabled, write a CALLSITE marker
        # to debug_logs so we can correlate exec ids to this high-level call.
        try:
            if os.getenv('TRACE_SQL_CONN') == '1':
                try:
                    cid = None
                    try:
                        from app.db import current_exec_id
                        cid = current_exec_id.get()
                    except Exception:
                        cid = None
                    stack = ''.join(traceback.format_stack(limit=8))
                    os.makedirs('debug_logs', exist_ok=True)
                    with open(os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                        f.write(f"CALLSITE autotoken {cid or 'none'}\n")
                        f.write(stack)
                        f.write('--- end ---\n\n')
                except Exception:
                    pass
        except Exception:
            pass
        resp = await client.post('/auth/token', json={'username': '__autotest__', 'password': 'p'})
        if resp.status_code == 200:
            token = resp.json().get('access_token')
            if token:
                client.headers.update({'Authorization': f'Bearer {token}'})
        return client


# Do not replace httpx.AsyncClient globally; keep AutoAuthAsyncClient
# available for manual use but prefer explicit authentication via the
# `client` fixture to avoid interfering with tests that exercise HTML
# session login/logout flows.
from httpx import AsyncClient, ASGITransport

# ensure project root is on PYTHONPATH for test runs
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app
from app.db import init_db, async_session
from app.models import ListState
from sqlmodel import select


# --- Test helpers for stubbing heavy date parsing ---
# Usage:
# - Tests that need to avoid invoking dateparser can opt-in to a fast,
#   deterministic extractor by including the `use_fake_extract_dates` fixture
#   in their test signature. Example:
#
#     async def test_x(client, use_fake_extract_dates):
#         # `app.utils.extract_dates` and `app.main.extract_dates` are patched
#         # to a deterministic extractor for the duration of the test.
#         resp = await client.get('/calendar/events')
#
# - Alternatively use `fake_extract_dates` with `monkeypatch` to set the
#   extractor manually:
#
#     def test_y(monkeypatch, fake_extract_dates):
#         import app.utils as utils
#         monkeypatch.setattr(utils, 'extract_dates', fake_extract_dates)
#
def _default_fake_extract_dates(text: str):
    """Deterministic fake extract_dates for tests.

    - If text contains an ISO date YYYY-MM-DD, return that date as UTC.
    - Otherwise return an empty list.
    """
    out = []
    if not text:
        return out
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        from datetime import datetime, timezone
        out.append(datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc))
    return out


@pytest.fixture
def fake_extract_dates():
    """Return a callable suitable for monkeypatching `app.utils.extract_dates`.

    Tests can use this fixture together with `monkeypatch` to replace the real
    dateparser-based extractor with a fast deterministic one:

        def test_x(monkeypatch, fake_extract_dates):
            import app.utils as utils
            monkeypatch.setattr(utils, 'extract_dates', fake_extract_dates)
            ...

    """
    return _default_fake_extract_dates


@pytest.fixture
def use_fake_extract_dates(monkeypatch, fake_extract_dates):
    """Convenience fixture: monkeypatch `app.utils.extract_dates` and
    `app.main.extract_dates` to the deterministic fake for the duration of the test.

    Tests that need to avoid invoking dateparser can use this fixture by
    including `use_fake_extract_dates` in the test signature.
    """
    import app.utils as utils
    import app.main as main
    monkeypatch.setattr(utils, 'extract_dates', fake_extract_dates)
    # also patch app.main in case the function was imported there
    monkeypatch.setattr(main, 'extract_dates', fake_extract_dates, raising=False)


@pytest_asyncio.fixture
async def ensure_db():
    await init_db()
    # Do not create a ListState named 'default' here; server default should be
    # set explicitly by tests using the server API when needed.


@pytest_asyncio.fixture
async def client(ensure_db):
    transport = ASGITransport(app=app)
    # use the AutoAuthAsyncClient for the `client` fixture so tests that
    # rely on it remain authenticated, but leave the global AsyncClient
    # unchanged so tests can create unauthenticated clients when needed.
    async with AutoAuthAsyncClient(transport=transport, base_url="http://test") as ac:
        # Ensure a test user exists and authenticate to get a bearer token
        from app.db import async_session
        from app.models import User
        from app.auth import pwd_context

        async with async_session() as sess:
            q = await sess.exec(select(User).where(User.username == "testuser"))
            u = q.first()
            if not u:
                ph = pwd_context.hash("testpass")
                u = User(username="testuser", password_hash=ph, is_admin=True)
                sess.add(u)
                try:
                    await sess.commit()
                except Exception:
                    await sess.rollback()
        # obtain token
        resp = await ac.post("/auth/token", json={"username": "testuser", "password": "testpass"})
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            if token:
                ac.headers.update({"Authorization": f"Bearer {token}"})
        yield ac



def pytest_collection_modifyitems(session, config, items):
    """Skip recurrence/occurrence-heavy tests when recurrence detection is disabled.

    Some tests assert that the server expands inline recurrence phrases into
    calendar occurrences. When the runtime flag ``ENABLE_RECURRING_DETECTION``
    is set to a falsey value (0/False) we want those tests skipped so test runs
    reflect the current server behavior.
    """
    try:
        from app import config as app_config
    except Exception:
        # If we can't import config, don't interfere with collection.
        return

    enabled = bool(getattr(app_config, 'ENABLE_RECURRING_DETECTION', False))
    if enabled:
        return

    # Patterns for test nodeids (filenames / test modules) that exercise
    # calendar occurrence expansion and recurrence behaviour.
    skip_patterns = (
        'calendar_occurrences_recurring',
        'calendar_occurrences',
        'rrule_occurrences',
        'integration_rrule_occurrences',
        'parse_text_to_rrule',
    )

    import pytest as _pytest
    reason = 'recurrence detection disabled via app.config.ENABLE_RECURRING_DETECTION'
    for item in items:
        node = item.nodeid.lower()
        if any(p in node for p in skip_patterns):
            item.add_marker(_pytest.mark.skip(reason=reason))


def pytest_sessionfinish(session, exitstatus):
    """Ensure the async engine is disposed at the end of the pytest session.

    Disposing the engine explicitly closes pooled connections and prevents
    SQLAlchemy's pool finalizer from emitting non-checked-in warnings during
    interpreter shutdown.
    """
    try:
        import asyncio
        from app import db as app_db

        loop = asyncio.get_event_loop()
        if loop.is_running():
            # schedule a coroutine to dispose the engine
            loop.run_until_complete(app_db.engine.dispose())
        else:
            loop.run_until_complete(app_db.engine.dispose())
    except Exception:
        # best-effort: if disposal fails, don't crash pytest teardown
        pass
