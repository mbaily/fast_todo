from fastapi import FastAPI, HTTPException, Depends
from sqlmodel import select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import and_, or_
from .db import async_session, init_db
from .models import ListState, Todo, CompletionType, TodoCompletion, User
from .auth import get_current_user, create_access_token, require_login
from pydantic import BaseModel
from .utils import now_utc, normalize_hashtag
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import json
import asyncio
from asyncio import Queue
import os
from contextvars import ContextVar
from sqlmodel import select
from .models import Hashtag, TodoHashtag, ListHashtag, ServerState, CompletionType, SyncOperation, Tombstone, Category
from .models import RecentListVisit
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from .utils import format_server_local, format_in_timezone
from .utils import extract_hashtags
from .utils import extract_dates
from .utils import extract_dates_meta
from .utils import remove_hashtags_from_text
from .utils import parse_text_to_rrule, recurrence_dict_to_rrule_string, recurrence_dict_to_rrule_params, parse_text_to_rrule_string, parse_date_and_recurrence
from .models import Session
import logging
from . import config

import sys

logger = logging.getLogger(__name__)
# Ensure INFO-level messages from this module appear on the server console when
# no handlers are configured (safe fallback for development/testing).
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Optional debugpy attach for live debugging. Enabled by setting ENABLE_DEBUGPY=1
# DEBUGPY_PORT defaults to 5678. If DEBUGPY_WAIT=1 the server will pause until a
# debugger attaches (useful during development).
try:
    if os.getenv('ENABLE_DEBUGPY', '0') in ('1', 'true', 'yes'):
        try:
            import debugpy
            debug_port = int(os.getenv('DEBUGPY_PORT', '5678'))
            debug_wait = os.getenv('DEBUGPY_WAIT', '0') in ('1', 'true', 'yes')
            # Listen on all interfaces so remote debuggers can attach if needed.
            debugpy.listen(('0.0.0.0', debug_port))
            logger.info('debugpy listening on 0.0.0.0:%d (wait=%s)', debug_port, debug_wait)
            if debug_wait:
                # Blocks until a debugger attaches.
                debugpy.wait_for_client()
        except Exception:
            logger.exception('failed to start debugpy; continuing without debugger')
except Exception:
    # keep startup robust in case os.getenv or logging behaves unexpectedly
    pass

# In-memory log store for lightweight debugging access via HTTP.
# Uses a deque with a configurable max length so memory usage stays bounded.
from collections import deque
try:
    LOG_STORE_MAX = int(os.getenv('INMEM_LOG_MAX', '10000'))
except Exception:
    LOG_STORE_MAX = 10000

# store items as dicts: {ts, level, logger, message}
_inmemory_log = deque(maxlen=LOG_STORE_MAX)

# Global list of asyncio Queues used by SSE log stream (always defined)
_sse_queues: list[Queue] = []

# contextvar to annotate whether current execution is handling an HTTP request
# value will be a short string like 'http_request:/path' or None for background tasks
_sse_origin: ContextVar[str | None] = ContextVar('_sse_origin', default=None)


class InMemoryHandler(logging.Handler):
    def emit(self, record):
        try:
            # Format using the handler's formatter if present, else basic message
            msg = self.format(record) if self.formatter else record.getMessage()
            # timestamp in UTC ISO
            ts = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
            _inmemory_log.append({
                'ts': ts,
                'level': record.levelname,
                'logger': record.name,
                'message': msg,
            })
        except Exception:
            # ensure logging never raises
            pass


# Attach in-memory handler to root logger so all module logs are captured
try:
    _inmem_handler = InMemoryHandler()
    _inmem_handler.setLevel(logging.DEBUG)
    _inmem_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s'))
    logging.getLogger().addHandler(_inmem_handler)
except Exception:
    logger.exception('failed to attach in-memory log handler')

    # Simple Server-Sent Events (SSE) broadcaster for live log streaming.
    # Simple Server-Sent Events (SSE) broadcaster for live log streaming.
    from asyncio import Queue

    def _broadcast_log(record: dict):
        # push record into all active queues (non-blocking)
        for q in list(_sse_queues):
            try:
                # do not await here; put_nowait is fine for in-memory small queues
                q.put_nowait(record)
            except Exception:
                try:
                    _sse_queues.remove(q)
                except Exception:
                    pass

    # augment the InMemoryHandler to also broadcast
    orig_emit = InMemoryHandler.emit
    def _emit_and_broadcast(self, record):
        try:
            orig_emit(self, record)
            # prepare the dict similarly
            msg = self.format(record) if self.formatter else record.getMessage()
            ts = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
            rec = {'ts': ts, 'level': record.levelname, 'logger': record.name, 'message': msg}
            _broadcast_log(rec)
        except Exception:
            pass

    InMemoryHandler.emit = _emit_and_broadcast

# templating for no-JS HTML client
TEMPLATES = Jinja2Templates(directory="html_no_js/templates")
# In development prefer templates to auto-reload so edits show up without
# requiring a full server restart. Also clear any cached templates on
# render for extra safety.
TEMPLATES.env.auto_reload = True
TEMPLATES.env.filters['server_local_dt'] = format_server_local
TEMPLATES.env.filters['in_tz'] = format_in_timezone
from markupsafe import Markup, escape
import re
import time


def linkify(text: str | None) -> Markup:
    """Convert bare http(s) URLs in text into clickable links and return
    safe HTML Markup. Keeps other text escaped.
    """
    if not text:
        return Markup("")


    # escape first to avoid HTML injection
    esc = escape(text)
    url_re = re.compile(r"(https?://[^\s<]+)")

    def _repl(m: re.Match) -> str:
        url = m.group(1)
        return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{escape(url)}</a>'

    res = url_re.sub(lambda m: _repl(m), str(esc))
    return Markup(res)


TEMPLATES.env.filters['linkify'] = linkify


# Helper to broadcast lightweight debug events to SSE queues when available.
def _sse_debug(event: str, payload: dict):
    try:
        # Toggle to completely disable SSE debug broadcasting for tests or
        # performance-sensitive runs. Default disabled; set env var
        # SSE_DEBUG_ENABLED=1 to enable without changing code (restart
        # required), or edit this variable in-code for a quick local toggle.
        try:
            # default to '0' -> disabled
            SSE_DEBUG_ENABLED = os.getenv('SSE_DEBUG_ENABLED', '0').lower() in ('1', 'true', 'yes')
        except Exception:
            SSE_DEBUG_ENABLED = False
        if not SSE_DEBUG_ENABLED:
            return
        # include optional source annotation when available from contextvar
        origin = None
        try:
            origin = _sse_origin.get()
        except Exception:
            origin = None
        debug_payload = {'event': event, 'payload': payload}
        if origin:
            # don't mutate original payload; annotate separately
            debug_payload['source'] = origin
        rec = {'ts': datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(), 'level': 'DEBUG', 'logger': 'sse.debug', 'message': json.dumps(debug_payload)}
        for q in list(_sse_queues):
            try:
                q.put_nowait(rec)
            except Exception:
                try:
                    _sse_queues.remove(q)
                except Exception:
                    pass
    except Exception:
        # never let debug broadcasting break application logic
        pass


def is_ios_safari(request: Request) -> bool:
    """Conservative check for iOS Safari: User-Agent contains 'iPhone' or 'iPad' or 'iPod' and 'Safari' but not 'CriOS' or 'FxiOS' (Chrome/Firefox on iOS)."""
    ua = (request.headers.get('user-agent') or '').lower()
    if not ua:
        return False
    if ('iphone' in ua or 'ipad' in ua or 'ipod' in ua) and 'safari' in ua and 'crios' not in ua and 'fxios' not in ua:
        return True
    return False


async def get_session_timezone(request: Request) -> str | None:
    """Prefer timezone stored on the server-side Session row; fall back to tz cookie."""
    # prefer session-stored tz when available
    st = request.cookies.get('session_token')
    if st:
        try:
            async with async_session() as sess:
                q = await sess.exec(select(Session).where(Session.session_token == st))
                row = q.first()
                if row:
                    # ignore expired sessions
                    try:
                        if getattr(row, 'expires_at', None) and row.expires_at < datetime.now(timezone.utc):
                            # expired: do not use this session's timezone
                            return None
                    except Exception:
                        # if any error comparing datetimes, ignore and continue
                        logger.exception("error while comparing session expires_at")
                    if getattr(row, 'timezone', None):
                        return row.timezone
        except Exception:
            logger.exception("error while reading session timezone from DB")
    # fallback to client-provided tz cookie
    return request.cookies.get('tz')

# Cookie secure flag: default to False for test/dev (HTTP). In production set
# COOKIE_SECURE=1 or true in the environment so cookies are marked Secure.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup checks
    # Warn or fail if SECRET_KEY is left as the test fallback in production-like environments.
    try:
        from .auth import SECRET_KEY as _SECRET_KEY
    except Exception:
        _SECRET_KEY = None
    if _SECRET_KEY == "CHANGE_ME_IN_ENV_FOR_TESTS":
        # If running in a production-like environment (COOKIE_SECURE set or ENV=production), fail-fast.
        env = os.getenv("ENV", "").lower()
        if COOKIE_SECURE or env in ("production", "prod"):
            raise RuntimeError("insecure SECRET_KEY detected; set SECRET_KEY in the environment for production")
        else:
            logger.warning("SECRET_KEY is the test fallback; this is insecure for production. Set SECRET_KEY in the environment.")

    # initialize DB and ensure default list exists
    await init_db()
    # Log which database URL the server is using so startup console shows active DB
    try:
        from . import db as _dbmod
        db_url = getattr(_dbmod, 'DATABASE_URL', None)
        if db_url:
            logger.info('starting server using DATABASE_URL=%s', db_url)
        else:
            logger.info('starting server: DATABASE_URL not set')
    except Exception:
        logger.exception('could not determine DATABASE_URL at startup')
    # Initialize a seeded DateDataParser instance per worker to avoid repeated
    # automatic language detection. This is created here (in the lifespan
    # startup) so each worker process gets its own instance and we can control
    # options safely for production.
    try:
        from dateparser.date import DateDataParser
        # prefer given order and do not try previous locales to avoid mutable
        # state changes across concurrent requests
        import app.utils as _utils
        _utils._DATE_DATA_PARSER = DateDataParser(languages=['en'], try_previous_locales=False, use_given_order=True)
        logger.info('seeded DateDataParser initialized for languages=["en"]')
    except Exception:
        # dateparser may not be installed in some environments; log and continue
        logger.info('DateDataParser not initialized (dateparser may be missing)')
    async with async_session() as sess:
        # Ensure ServerState exists; do not create or treat any ListState named
        # "default" specially. Server default must be set explicitly via the
        # `/server/default_list/{id}` API or by application logic elsewhere.
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        if not ss:
            ss = ServerState()
            sess.add(ss)
            await sess.commit()
    # start background undefer worker
    stop_event = asyncio.Event()

    async def _undefer_worker(interval: int):
        from .utils import now_utc
        while not stop_event.is_set():
            try:
                await asyncio.sleep(interval)
                async with async_session() as wsess:
                    # diagnostic CALLSITE: record exec id and stack when worker runs
                    try:
                        if os.getenv('TRACE_SQL_CONN') == '1':
                            try:
                                from app.db import current_exec_id
                                cid = None
                                try:
                                    cid = current_exec_id.get()
                                except Exception:
                                    cid = None
                                import traceback as _tb
                                stack = ''.join(_tb.format_stack(limit=6))
                                import os as _os
                                _os.makedirs('debug_logs', exist_ok=True)
                                with open(_os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                                    f.write(f"CALLSITE undefer_worker {cid or 'none'}\n")
                                    f.write(stack)
                                    f.write('--- end ---\n\n')
                            except Exception:
                                pass
                    except Exception:
                        pass
                    now = now_utc()
                    q = select(Todo).where(Todo.deferred_until != None).where(Todo.deferred_until <= now)
                    res = await wsess.exec(q)
                    due = res.all()
                    for t in due:
                        t.deferred_until = None
                        t.modified_at = now_utc()
                        wsess.add(t)
                    if due:
                        await wsess.commit()
            except asyncio.CancelledError:
                break
            except Exception:
                # log unexpected errors but keep worker running
                logger.exception("undefer worker encountered an error")

    interval = int(os.getenv("UNDEFER_INTERVAL_SECONDS", "60"))
    task = asyncio.create_task(_undefer_worker(interval))
    # Tombstone pruning configuration: TTL (days) and prune interval (seconds)
    TOMBSTONE_TTL_DAYS = int(os.getenv("TOMBSTONE_TTL_DAYS", "90"))
    PRUNE_INTERVAL_SECONDS = int(os.getenv("TOMBSTONE_PRUNE_INTERVAL_SECONDS", str(24 * 3600)))

    async def _prune_tombstones_worker(interval: int, ttl_days: int):
        from datetime import timedelta
        while not stop_event.is_set():
            try:
                await asyncio.sleep(interval)
                cutoff = now_utc() - timedelta(days=ttl_days)
                async with async_session() as wsess:
                    # diagnostic CALLSITE: record exec id and stack when prune runs
                    try:
                        if os.getenv('TRACE_SQL_CONN') == '1':
                            try:
                                from app.db import current_exec_id
                                cid = None
                                try:
                                    cid = current_exec_id.get()
                                except Exception:
                                    cid = None
                                import traceback as _tb
                                stack = ''.join(_tb.format_stack(limit=6))
                                import os as _os
                                _os.makedirs('debug_logs', exist_ok=True)
                                with open(_os.path.join('debug_logs', 'sql_checkout_traces.log'), 'a') as f:
                                    f.write(f"CALLSITE prune_worker {cid or 'none'}\n")
                                    f.write(stack)
                                    f.write('--- end ---\n\n')
                            except Exception:
                                pass
                    except Exception:
                        pass
                    stmt = sqlalchemy_delete(Tombstone).where(Tombstone.created_at != None).where(Tombstone.created_at < cutoff)
                    res = await wsess.exec(stmt)
                    try:
                        deleted = res.rowcount if hasattr(res, 'rowcount') and res.rowcount is not None else 0
                    except Exception:
                        deleted = 0
                    if deleted:
                        await wsess.commit()
                        logger.info('pruned %d tombstones older than %d days', deleted, ttl_days)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception('tombstone prune worker encountered an error')

    prune_task = asyncio.create_task(_prune_tombstones_worker(PRUNE_INTERVAL_SECONDS, TOMBSTONE_TTL_DAYS))
    try:
        yield
    finally:
        # signal worker to stop and wait for it
        stop_event.set()
        task.cancel()
        prune_task.cancel()
        try:
            await task
        except Exception:
            pass
        try:
            await prune_task
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)

# serve static assets (manifest, service-worker, icons, pwa helper JS, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Simple ASGI middleware: set `_sse_origin` contextvar for the scope of each
# HTTP request handler so debug events can be annotated with their request
# origin (path). Background tasks or other contexts will leave the origin as None.
@app.middleware('http')
async def _sse_origin_middleware(request: Request, call_next):
    token = None
    try:
        # set the contextvar to a concise string we can surface in SSE
        token = _sse_origin.set(f'http_request:{request.url.path}')
    except Exception:
        token = None
    try:
        resp = await call_next(request)
        return resp
    finally:
        try:
            if token is not None:
                _sse_origin.reset(token)
        except Exception:
            pass


def _is_local_request(request: Request) -> bool:
    """Return True if request originates from localhost addresses."""
    try:
        host = request.client.host if getattr(request, 'client', None) else None
        return host in (None, '127.0.0.1', '::1', 'localhost')
    except Exception:
        return False


def _log_endpoint_allowed(request: Request) -> bool:
    # Allow when explicitly enabled via env var or when request is local
    if os.getenv('ENABLE_LOG_ENDPOINT', '0') in ('1', 'true', 'yes'):
        return True
    return _is_local_request(request)


@app.get('/server/logs')
async def get_server_logs(request: Request, limit: int = 200, level: str | None = None):
    """Return recent in-memory log messages.

    By default returns up to `limit` messages (most recent first). If `level`
    is provided, filter by log level (e.g., DEBUG, INFO, WARNING, ERROR).
    Access is restricted to local requests unless ENABLE_LOG_ENDPOINT=1.
    """
    if not _log_endpoint_allowed(request):
        raise HTTPException(status_code=403, detail='forbidden')
    # clamp limit
    try:
        limit = min(max(int(limit), 1), LOG_STORE_MAX)
    except Exception:
        limit = 200
    items = list(_inmemory_log)
    if level:
        lvl = level.upper()
        items = [i for i in items if i.get('level') == lvl]
    # return most recent first
    return {'count': len(items), 'logs': list(reversed(items))[:limit]}


class LogPost(BaseModel):
    level: str = 'INFO'
    message: str


@app.post('/server/logs')
async def post_server_log(request: Request, payload: LogPost):
    """Append a synthetic log record to the in-memory store for debugging.

    This is intended for quick diagnostic notes. Access restricted to local
    requests unless ENABLE_LOG_ENDPOINT=1.
    """
    if not _log_endpoint_allowed(request):
        raise HTTPException(status_code=403, detail='forbidden')
    # create a log record via the root logger so formatting is consistent
    lvl = payload.level.upper() if payload.level else 'INFO'
    try:
        getattr(logging.getLogger(), lvl.lower())(payload.message)
    except Exception:
        logging.getLogger().info(payload.message)
    return {'ok': True}


@app.delete('/server/logs')
async def clear_server_logs(request: Request):
    """Clear the in-memory logs (local-only or enabled by env var)."""
    if not _log_endpoint_allowed(request):
        raise HTTPException(status_code=403, detail='forbidden')
    _inmemory_log.clear()
    return {'ok': True}


@app.middleware("http")
async def no_cache_dynamic(request: Request, call_next):
    """Set conservative no-cache headers for dynamic pages and API responses.

    Applies to HTML and JSON responses and to well-known dynamic prefixes so
    browsers and intermediaries don't serve stale content after deploys.
    """
    resp = await call_next(request)
    try:
        path = request.url.path or ''
        content_type = resp.headers.get('content-type', '')
        is_dynamic_path = any(path.startswith(p) for p in ('/html_no_js', '/todos', '/lists', '/server', '/html_pwa'))
        is_html = 'text/html' in content_type
        is_json = 'application/json' in content_type
        if is_dynamic_path or is_html or is_json:
            # preserve existing Cache-Control if it's already explicitly set to no-store
            cc = resp.headers.get('Cache-Control', '')
            if 'no-store' not in cc.lower():
                resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            resp.headers['Pragma'] = 'no-cache'
            resp.headers['Expires'] = '0'
    except Exception:
        # don't let header setting break request handling
        logger.exception('error while applying no-cache middleware')
    return resp


@app.middleware('http')
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    resp = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000.0
    try:
        # include query string for context but keep logs concise
        logger.info('timing %s %s %s %.1fms', request.method, request.url.path, request.url.query, duration_ms)
    except Exception:
        logger.info('timing %s %s %.1fms', request.method, request.url.path, duration_ms)
    return resp


@app.get("/service-worker.js")
async def service_worker_js():
    """Serve the service worker at the site root so it can control the whole
    origin/scope. Mark as no-cache so updates are noticed by clients.
    """
    return FileResponse("static/service-worker.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
async def manifest_json():
    """Serve the web app manifest at the site root."""
    return FileResponse("static/manifest.json", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/")
async def root_redirect():
    return RedirectResponse(url='/html_no_js/', status_code=302)


@app.get("/html_pwa/", response_class=HTMLResponse)
async def html_pwa_index_slash(request: Request):
    """Serve the PWA HTML index (trailing-slash path). Require login like the no-JS UI."""
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        current_user = None
    if not current_user:
        return RedirectResponse(url='/html_pwa/login', status_code=303)
    # serve the static PWA index
    return FileResponse("html_pwa/index.html", media_type="text/html")


@app.get("/html_pwa/index.html", response_class=HTMLResponse)
async def html_pwa_index_file(request: Request):
    """Serve the PWA HTML index (explicit filename).

    Some clients and the service-worker expect this exact path to be
    fetchable; provide both the trailing-slash and explicit file routes.
    Require login like the no-JS UI.
    """
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        current_user = None
    if not current_user:
        return RedirectResponse(url='/html_pwa/login', status_code=303)
    return FileResponse("html_pwa/index.html", media_type="text/html")


@app.post("/lists")
async def create_list(request: Request, name: str = Form(None), current_user: User = Depends(require_login)):
    # Accept name from form (normal HTML/PWA) or fallback to query params so
    # test clients that post with `params={'name': ...}` continue to work.
    if not name:
        name = request.query_params.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # strip leading whitespace and remove inlined hashtags from saved name
    name = remove_hashtags_from_text(name.lstrip())
    async with async_session() as sess:
        # Always create a new list row for the authenticated user. We allow
        # duplicate names per user (multiple lists with the same name).
        owner_id = current_user.id
        lst = ListState(name=name, owner_id=owner_id)
        sess.add(lst)
        try:
            await sess.commit()
        except IntegrityError:
            await sess.rollback()
            raise HTTPException(status_code=400, detail="could not create list")
        await sess.refresh(lst)
        # create default completion type for the list if it doesn't already exist
        qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == lst.id).where(CompletionType.name == "default"))
        if not qc.first():
            c = CompletionType(name="default", list_id=lst.id)
            sess.add(c)
            try:
                await sess.commit()
            except IntegrityError:
                # another concurrent request may have inserted the default; ignore
                await sess.rollback()
        # If the server default is not set and this was the first list in the DB,
        # make this newly-created list the default.
        qs2 = await sess.exec(select(ServerState))
        ss2 = qs2.first()
        if ss2 and not ss2.default_list_id:
            ss2.default_list_id = lst.id
            sess.add(ss2)
            await sess.commit()
        # Extract hashtags from the original submitted list name and sync list-level hashtags
        try:
            original = request.query_params.get('name') if request.query_params.get('name') and not request.form else None
            tags = extract_hashtags(request.query_params.get('name') or name)
        except Exception:
            tags = []
        # preserve order and dedupe
        seen: list[str] = []
        for t in tags:
            if t and t not in seen:
                seen.append(t)
        if seen:
            await _sync_list_hashtags(sess, lst.id, seen)
        return lst


@app.get("/lists")
async def list_lists(current_user: User = Depends(require_login)):
    async with async_session() as sess:
        owner_id = current_user.id if current_user else None
        res = await sess.exec(select(ListState).where(ListState.owner_id == owner_id))
        return res.all()


def _parse_iso_to_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # allow naive or offset-aware ISO strings
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # assume UTC for naive strings
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {s}")


@app.get('/calendar/events')
async def calendar_events(request: Request, start: Optional[str] = None, end: Optional[str] = None, current_user: User = Depends(require_login)):
    """Return lists and todos that contain parsed dates.

    Scans list names and todo text/note for date-like strings using dateparser.
    Optional `start` and `end` ISO datetimes (UTC or naive assumed UTC) can
    limit which dates are returned.
    """
    owner_id = current_user.id if current_user else None
    logger.info('calendar_events called owner_id=%s start=%s end=%s', owner_id, start, end)
    start_dt = _parse_iso_to_utc(start)
    end_dt = _parse_iso_to_utc(end)

    events: list[Dict[str, Any]] = []
    async with async_session() as sess:
        # fetch lists for this owner
        qlists = await sess.exec(select(ListState).where(ListState.owner_id == owner_id))
        lists = qlists.all()
        # fetch todos that belong to these lists
        if lists:
            list_ids = [l.id for l in lists if l.id is not None]
        else:
            list_ids = []
        logger.info('calendar_occurrences fetched %d lists for owner_id=%s', len(lists) if lists is not None else 0, owner_id)

        todos = []
        if list_ids:
            qtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)))
            todos = qtodos.all()
        logger.info('calendar_occurrences fetched %d todos for owner_id=%s', len(todos) if todos is not None else 0, owner_id)

        # helper to filter by optional window
        def in_window(dt: datetime) -> bool:
            if start_dt and dt < start_dt: return False
            if end_dt and dt > end_dt: return False
            return True

        # scan lists
        for l in lists:
            texts = [l.name or '']
            # also consider hashtags (joined) in case dates are in tags
            try:
                tags = getattr(l, 'hashtags', None)
                if tags:
                    texts.append(' '.join([getattr(t, 'tag', '') for t in tags]))
            except Exception:
                pass
            combined = ' \n '.join(texts)
            dates = extract_dates(combined)
            # keep only dates within window if provided
            dates = [d for d in dates if in_window(d)]
            if dates:
                events.append({
                    'item_type': 'list',
                    'id': l.id,
                    'title': l.name,
                    'dates': [d.isoformat() for d in dates]
                })

        # scan todos
        for t in todos:
            # Refresh the todo from the current session to pick up any recent
            # commits (tests may update created_at shortly before calling this
            # handler). This avoids using a stale object from a different session
            # snapshot.
            try:
                refreshed = await sess.get(Todo, getattr(t, 'id', None))
                if refreshed:
                    # force reload from DB to pick up any commits from other
                    # sessions (tests may update created_at in a separate
                    # session just before calling this handler)
                    try:
                        await sess.refresh(refreshed)
                    except Exception:
                        # refresh may fail for detached instances; ignore
                        pass
                    t = refreshed
            except Exception:
                pass
            texts = [t.text or '']
            if getattr(t, 'note', None):
                texts.append(t.note)
            combined = ' \n '.join(texts)
            dates = extract_dates(combined)
            # include explicit deferred_until if present
            if getattr(t, 'deferred_until', None):
                try:
                    du = t.deferred_until
                    if du.tzinfo is None:
                        du = du.replace(tzinfo=timezone.utc)
                    du = du.astimezone(timezone.utc)
                    dates.append(du)
                except Exception:
                    pass
            dates = [d for d in dates if in_window(d)]
            if dates:
                events.append({
                    'item_type': 'todo',
                    'id': t.id,
                    'list_id': t.list_id,
                    'title': t.text,
                    'dates': [d.isoformat() for d in dates]
                })

    return {'events': events}


@app.get('/calendar/occurrences')
async def calendar_occurrences(request: Request,
                               start: Optional[str] = None,
                               end: Optional[str] = None,
                               tz: Optional[str] = None,
                               expand: bool = True,
                               max_per_item: int = 100,
                               max_total: int = 10000,
                               include_ignored: bool = False,
                               current_user: User = Depends(require_login)):
    """Return a flattened, sorted list of occurrences (including expanded recurrences).

    Query params:
    - start, end: ISO datetimes (UTC) to bound occurrences. If not provided,
      default window is [now, now + 90 days].
    - tz: optional timezone hint (not used for computation; returned datetimes are UTC).
    - expand: whether to expand recurrence rules (default true).
    - max_per_item: limit occurrences expanded per todo/list.
    - max_total: safety cap on total occurrences returned.
    """
    owner_id = current_user.id if current_user else None
    # parse or default window
    try:
        start_dt = _parse_iso_to_utc(start) if start else None
    except HTTPException:
        raise
    try:
        end_dt = _parse_iso_to_utc(end) if end else None
    except HTTPException:
        raise
    from datetime import timedelta as _td
    if not start_dt and not end_dt:
        now = now_utc()
        start_dt = now
        end_dt = now + _td(days=90)
    elif not start_dt:
        # if only end provided, set start to now
        start_dt = now_utc()
    elif not end_dt:
        # if only start provided, set end to start + 90 days
        end_dt = start_dt + _td(days=90)

    # helper to ensure datetimes are timezone-aware UTC before comparing
    def _ensure_aware(d: datetime) -> datetime:
        if d is None:
            return now_utc()
        try:
            if d.tzinfo is None:
                return d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            return now_utc()

    # normalize start/end for internal comparisons
    start_dt = _ensure_aware(start_dt)
    end_dt = _ensure_aware(end_dt)

    occurrences: list[dict] = []
    truncated = False
    # support disabling recurring detection via config (useful for testing clients)
    # When recurring detection is disabled we should still return explicit
    # plain-date occurrences (those extracted by extract_dates_meta). Only
    # rrule/recurrence parsing and inline recurrence detection should be
    # skipped.
    try:
        recurring_enabled = bool(config.ENABLE_RECURRING_DETECTION)
    except Exception:
        recurring_enabled = True
    logger.info('calendar_occurrences called owner_id=%s start=%s end=%s expand=%s include_ignored=%s', owner_id, start_dt.isoformat() if start_dt else None, end_dt.isoformat() if end_dt else None, expand, include_ignored)
    # Emit SSE debug event for handler entry
    _sse_debug('calendar_occurrences.entry', {'owner_id': owner_id, 'start': start_dt.isoformat() if start_dt else None, 'end': end_dt.isoformat() if end_dt else None, 'expand': expand, 'include_ignored': include_ignored})
    # Development-only conditional breakpoint. Set ENABLE_CALENDAR_BREAKPOINT=1
    # in the environment to trigger a debugger break at the start of this
    # handler. Attempts debugpy.breakpoint() first, falls back to pdb.set_trace().
    try:
        if os.getenv('ENABLE_CALENDAR_BREAKPOINT', '0') in ('1', 'true', 'yes'):
            try:
                import debugpy
                # If a debugpy client is attached this will pause execution.
                debugpy.breakpoint()
                logger.info('debugpy.breakpoint() invoked for calendar_occurrences')
            except Exception:
                try:
                    import pdb
                    pdb.set_trace()
                except Exception:
                    logger.exception('failed to invoke debug breakpoint for calendar_occurrences')
    except Exception:
        # keep handler robust if environment inspection or imports fail
        logger.exception('error evaluating ENABLE_CALENDAR_BREAKPOINT')
    async with async_session() as sess:
        # fetch lists for this owner
        qlists = await sess.exec(select(ListState).where(ListState.owner_id == owner_id))
        lists = qlists.all()
        _sse_debug('calendar_occurrences.lists_fetched', {'count': len(lists) if lists else 0})
        if lists:
            list_ids = [l.id for l in lists if l.id is not None]
        else:
            list_ids = []

        todos = []
        if list_ids:
            qtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)))
            todos = qtodos.all()
        _sse_debug('calendar_occurrences.todos_fetched', {'count': len(todos)})
        try:
            logger.info('calendar_occurrences.fetched_todos %s', [(getattr(tt,'id',None), (getattr(tt,'text',None) or '')[:40], (getattr(tt,'created_at',None).isoformat() if getattr(tt,'created_at',None) and getattr(tt,'created_at',None).tzinfo else str(getattr(tt,'created_at',None)))) for tt in todos])
        except Exception:
            pass

        def add_occ(item_type: str, item_id: int, list_id: int | None, title: str, occ_dt, dtstart, is_rec, rrule_str, rec_meta, source: str | None = None):
            nonlocal occurrences, truncated
            if len(occurrences) >= max_total:
                # global truncation reached
                try:
                    _sse_debug('calendar_occurrences.truncated', {'when': 'max_total', 'item_type': item_type, 'item_id': item_id, 'current_total': len(occurrences)})
                except Exception:
                    pass
                truncated = True
                return
            # compute occurrence hash for client/server idempotency
            from .utils import occurrence_hash
            occ_hash = occurrence_hash(item_type, item_id, occ_dt, rrule_str or '', title)
            occurrences.append({
                'occurrence_dt': occ_dt.isoformat(),
                'item_type': item_type,
                'id': item_id,
                'list_id': list_id,
                'title': title,
                'dtstart': dtstart.isoformat() if dtstart is not None else None,
                'is_recurring': bool(is_rec),
                'rrule': rrule_str or '',
                'recurrence_meta': rec_meta,
                'occ_hash': occ_hash,
            })
            # Emit an SSE debug event so callers can see which occurrences were added
            try:
                pay = {'item_type': item_type, 'item_id': item_id, 'occurrence_dt': occ_dt.isoformat(), 'title': title or '', 'rrule': rrule_str or '', 'is_recurring': bool(is_rec)}
                if source:
                    pay['source'] = source
                _sse_debug('calendar_occurrences.added', pay)
                # Also emit an INFO log so appended occurrences are visible in server stdout and in /server/logs
                try:
                    # include title to make it easier to correlate occurrences
                    logger.info('calendar_occurrences.added owner_id=%s item_type=%s item_id=%s title=%s occurrence=%s rrule=%s recurring=%s source=%s', owner_id, item_type, item_id, (title or '')[:60], occ_dt.isoformat(), rrule_str or '', bool(is_rec), source)
                except Exception:
                    pass
                # Debug helper: log ParamEvent Jan occurrences for test analysis
                try:
                    if title and 'ParamEvent' in title and 'Jan' in title:
                        logger.info('DEBUG_PARAM_EVENT_JAN found item_type=%s id=%s title=%s occurrence=%s source=%s', item_type, item_id, title, occ_dt.isoformat(), source)
                except Exception:
                    pass
            except Exception:
                pass

        # scan lists
        from dateutil.rrule import rrulestr
        for l in lists:
            texts = [l.name or '']
            try:
                tags = getattr(l, 'hashtags', None)
                if tags:
                    texts.append(' '.join([getattr(t, 'tag', '') for t in tags]))
            except Exception:
                pass
            combined = ' \n '.join(texts)
            # prefer persisted recurrence expansion if available
            rec_rrule = getattr(l, 'recurrence_rrule', None)
            rec_dtstart = getattr(l, 'recurrence_dtstart', None)
            if expand and rec_rrule and recurring_enabled:
                try:
                    _sse_debug('calendar_occurrences.list_expand_start', {'list_id': l.id, 'rrule': rec_rrule})
                    # mark branch choice for this list
                    try:
                        _sse_debug('calendar_occurrences.branch_choice', {'list_id': l.id, 'chosen_branch': 'list-rrule'})
                    except Exception:
                        pass
                    if rec_dtstart and rec_dtstart.tzinfo is None:
                        rec_dtstart = rec_dtstart.replace(tzinfo=timezone.utc)
                    r = rrulestr(rec_rrule, dtstart=rec_dtstart)
                    occs = list(r.between(start_dt, end_dt, inc=True))[:max_per_item]
                    # signal when per-item limit reached
                    try:
                        if len(occs) >= max_per_item:
                            _sse_debug('calendar_occurrences.per_item_limit', {'when': 'list-rrule', 'list_id': l.id, 'limit': max_per_item})
                    except Exception:
                        pass
                    for od in occs:
                        add_occ('list', l.id, None, l.name, od, rec_dtstart, True, rec_rrule, getattr(l, 'recurrence_meta', None), source='list-rrule')
                    continue
                except Exception as e:
                    try:
                        _sse_debug('calendar_occurrences.rrule_expand_failed', {'list_id': l.id, 'error': str(e)})
                    except Exception:
                        pass
                    # fall back to extract_dates below
                    pass

            # fallback: extract explicit dates from text. Use meta extractor so
            # yearless matches can be expanded against the window.
            meta = extract_dates_meta(combined)
            # expand year-explicit matches directly
            for m in meta:
                if m.get('year_explicit'):
                    d = m.get('dt')
                    if d >= start_dt and d <= end_dt:
                        add_occ('list', l.id, None, l.name, d, None, False, '', None, source='list-explicit')
            # handle yearless matches: generate candidates for each year in window
            yearless = [m for m in meta if not m.get('year_explicit')]
            if yearless:
                # cap expansion to at most 1 year after the list's creation
                try:
                    item_created = getattr(l, 'created_at', None) or start_dt
                    # normalize to UTC-aware
                    if item_created.tzinfo is None:
                        item_created = item_created.replace(tzinfo=timezone.utc)
                    else:
                        item_created = item_created.astimezone(timezone.utc)
                    from datetime import datetime as _dt
                    cap_dt = _dt(item_created.year + 1, item_created.month, item_created.day, tzinfo=timezone.utc)
                except Exception:
                    item_created = start_dt
                    cap_dt = start_dt
                allowed_start = max(start_dt, item_created)
                allowed_end = min(end_dt, cap_dt)
                if allowed_end < allowed_start:
                    ys = []
                else:
                    ys = range(allowed_start.year, allowed_end.year + 1)
                for m in yearless:
                    mon = int(m.get('month'))
                    day = int(m.get('day'))
                    for y in ys:
                        try:
                            from datetime import datetime as _dt
                            cand = _dt(y, mon, day, tzinfo=timezone.utc)
                        except Exception:
                            # invalid date (e.g., Feb 29 on non-leap year)
                            continue
                        # only add candidate if it falls inside the allowed window
                        if cand >= allowed_start and cand <= allowed_end:
                            add_occ('list', l.id, None, l.name, cand, None, False, '', None, source='list-yearless')

        # scan todos
        for t in todos:
            # Refresh the todo from the current session to pick up any recent
            # commits (tests may update created_at shortly before calling this
            # handler). This avoids using a stale object from a different session
            # snapshot.
            try:
                refreshed = await sess.get(Todo, getattr(t, 'id', None))
                if refreshed:
                    t = refreshed
            except Exception:
                pass
            texts = [t.text or '']
            if getattr(t, 'note', None):
                texts.append(t.note)
            combined = ' \n '.join(texts)
            # prefer persisted recurrence expansion if available
            rec_rrule = getattr(t, 'recurrence_rrule', None)
            rec_dtstart = getattr(t, 'recurrence_dtstart', None)
            if expand and rec_rrule and recurring_enabled:
                try:
                    if rec_dtstart and rec_dtstart.tzinfo is None:
                        rec_dtstart = rec_dtstart.replace(tzinfo=timezone.utc)
                    r = rrulestr(rec_rrule, dtstart=rec_dtstart)
                    occs = list(r.between(start_dt, end_dt, inc=True))[:max_per_item]
                    for od in occs:
                        add_occ('todo', t.id, t.list_id, t.text, od, rec_dtstart, True, rec_rrule, getattr(t, 'recurrence_meta', None), source='todo-rrule')
                    continue
                except Exception:
                    pass
            # If no persisted recurrence, attempt to parse an inline recurrence phrase
            # If recurring detection is disabled, skip inline recurrence parsing
            if expand and not rec_rrule and recurring_enabled:
                try:
                    # parse_text_to_rrule returns (rrule_obj, dtstart)
                    from .utils import parse_text_to_rrule, parse_text_to_rrule_string
                    r_obj, dtstart = parse_text_to_rrule(combined)
                    if r_obj is not None and dtstart is not None:
                        try:
                            _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-inline-rrule'})
                        except Exception:
                            pass
                        if dtstart.tzinfo is None:
                            dtstart = dtstart.replace(tzinfo=timezone.utc)
                        # build rrule string for reporting
                        _dt, rrule_str_local = parse_text_to_rrule_string(combined)
                        occs = list(r_obj.between(start_dt, end_dt, inc=True))[:max_per_item]
                        try:
                            if len(occs) >= max_per_item:
                                _sse_debug('calendar_occurrences.per_item_limit', {'when': 'todo-inline-rrule', 'todo_id': t.id, 'limit': max_per_item})
                        except Exception:
                            pass
                        for od in occs:
                            add_occ('todo', t.id, t.list_id, t.text, od, dtstart, True, rrule_str_local, None, source='todo-inline-rrule')
                        continue
                except Exception as e:
                    logger.exception('inline recurrence expansion failed')
                    try:
                        _sse_debug('calendar_occurrences.inline_rrule_parse_failed', {'todo_id': t.id, 'error': str(e)})
                    except Exception:
                        pass

            # fallback: extract explicit dates from text
            try:
                ca = getattr(t, 'created_at', None)
                if ca and ca.tzinfo is None:
                    ca = ca.replace(tzinfo=timezone.utc)
                elif ca:
                    ca = ca.astimezone(timezone.utc)
            except Exception:
                ca = None
            logger.info('calendar_occurrences.todo.inspect id=%s title=%s created_at=%s', getattr(t, 'id', None), (getattr(t, 'text', '') or '')[:60], (ca.isoformat() if isinstance(ca, datetime) else str(ca)))
            meta = extract_dates_meta(combined)
            # collect explicit dates for this todo
            dates: list[datetime] = []
            try:
                # prepare JSON-friendly summary of meta
                meta_summary = []
                for m in meta:
                    dd = m.get('dt')
                    meta_summary.append({'year_explicit': bool(m.get('year_explicit')), 'match_text': m.get('match_text'), 'month': m.get('month'), 'day': m.get('day'), 'dt': (dd.isoformat() if isinstance(dd, datetime) else str(dd))})
                _sse_debug('calendar_occurrences.todo.meta', {'todo_id': t.id, 'meta': meta_summary})
            except Exception:
                pass
            # include explicit deferred_until if present
            if getattr(t, 'deferred_until', None):
                try:
                    du = t.deferred_until
                    if du.tzinfo is None:
                        du = du.replace(tzinfo=timezone.utc)
                    du = du.astimezone(timezone.utc)
                    dates.append(du)
                except Exception:
                    pass
            # expand year-explicit matches directly
            explicit = [m for m in meta if m.get('year_explicit')]
            for m in explicit:
                d = m.get('dt')
                if d >= start_dt and d <= end_dt:
                    try:
                        _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-explicit'})
                    except Exception:
                        pass
                    add_occ('todo', t.id, t.list_id, t.text, d, None, False, '', None, source='todo-explicit')
            # include deferred_until as explicit
            if getattr(t, 'deferred_until', None):
                try:
                    du = t.deferred_until
                    if du.tzinfo is None:
                        du = du.replace(tzinfo=timezone.utc)
                    du = du.astimezone(timezone.utc)
                    if du >= start_dt and du <= end_dt:
                        try:
                            _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-deferred'})
                        except Exception:
                            pass
                        add_occ('todo', t.id, t.list_id, t.text, du, None, False, '', None, source='todo-deferred')
                except Exception:
                    pass
            # handle yearless matches: prefer the todo's creation time so that a
            # plain date like "1/1" maps to the next logical occurrence after
            # the todo was created. This prevents emitting the same plain-date
            # for many years when clients query multi-year windows. If no
            # created_at is available, fall back to now.
            yearless = [m for m in meta if not m.get('year_explicit')]
            if yearless:
                # reference point for selecting the "next" occurrence
                ref_dt = getattr(t, 'created_at', None) or now_utc()
                try:
                    if ref_dt.tzinfo is None:
                        ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                    else:
                        ref_dt = ref_dt.astimezone(timezone.utc)
                except Exception:
                    # defensive fallback
                    ref_dt = now_utc()

                # If multiple yearless tokens are present, expand each across the
                # full requested window and add every candidate inside the window.
                if len(yearless) > 1:
                    for m in yearless:
                        mon = int(m.get('month'))
                        day = int(m.get('day'))
                        _sse_debug('calendar_occurrences.todo.yearless_match', {'todo_id': t.id, 'match_text': m.get('match_text'), 'month': mon, 'day': day, 'ref_dt': ref_dt.isoformat() if isinstance(ref_dt, datetime) else str(ref_dt)})
                        # cap expansion to 1 year after the todo's creation
                        try:
                            item_created = getattr(t, 'created_at', None) or ref_dt
                            # normalize to UTC-aware
                            if item_created.tzinfo is None:
                                item_created = item_created.replace(tzinfo=timezone.utc)
                            else:
                                item_created = item_created.astimezone(timezone.utc)
                            from datetime import datetime as _dt
                            cap_dt = _dt(item_created.year + 1, item_created.month, item_created.day, tzinfo=timezone.utc)
                        except Exception:
                            item_created = ref_dt
                            cap_dt = end_dt
                        allowed_start = max(start_dt, item_created)
                        allowed_end = min(end_dt, cap_dt)
                        if allowed_end < allowed_start:
                            continue
                        for y in range(allowed_start.year, allowed_end.year + 1):
                            try:
                                cand = datetime(y, mon, day, tzinfo=timezone.utc)
                            except Exception:
                                continue
                            if cand >= allowed_start and cand <= allowed_end:
                                try:
                                    _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-yearless'})
                                except Exception:
                                    pass
                                add_occ('todo', t.id, t.list_id, t.text, cand, None, False, '', None, source='todo-yearless')
                else:
                    # Single token: preserve original semantics (earliest >= created_at)
                    for m in yearless:
                        mon = int(m.get('month'))
                        day = int(m.get('day'))
                        _sse_debug('calendar_occurrences.todo.yearless_match', {'todo_id': t.id, 'match_text': m.get('match_text'), 'month': mon, 'day': day, 'ref_dt': ref_dt.isoformat() if isinstance(ref_dt, datetime) else str(ref_dt)})
                        earliest_cand = None
                        # search only up to created_at + 1 year
                        try:
                            item_created = getattr(t, 'created_at', None) or ref_dt
                            # normalize to UTC-aware
                            if item_created.tzinfo is None:
                                item_created = item_created.replace(tzinfo=timezone.utc)
                            else:
                                item_created = item_created.astimezone(timezone.utc)
                            from datetime import datetime as _dt
                            cap_dt = _dt(item_created.year + 1, item_created.month, item_created.day, tzinfo=timezone.utc)
                        except Exception:
                            item_created = ref_dt
                            cap_dt = end_dt
                        max_year = min(end_dt.year, cap_dt.year)
                        for y in range(ref_dt.year, max_year + 1):
                            try:
                                cand = datetime(y, mon, day, tzinfo=timezone.utc)
                            except Exception:
                                continue
                            # Treat a candidate on the same calendar date as ref_dt
                            # as valid (handles created_at set to midnight / timezone
                            # normalization edge-cases). If cand is the same date as
                            # ref_dt, accept it; otherwise require cand >= ref_dt.
                            try:
                                # Require candidate to be strictly >= ref_dt. The
                                # previous behavior accepted a candidate when the
                                # calendar date matched ref_dt even if the todo's
                                # created_at time was later in the day, which could
                                # cause same-day creations after the target time to
                                # incorrectly match. Enforce strict comparison to
                                # ensure same-day but later-time created_at yields
                                # the next year's candidate when inside the 1-year cap.
                                if cand >= ref_dt:
                                    earliest_cand = cand
                                    break
                            except Exception:
                                if cand >= ref_dt:
                                    earliest_cand = cand
                                    break

                        if earliest_cand:
                            _sse_debug('calendar_occurrences.todo.earliest_candidate', {'todo_id': t.id, 'match_text': m.get('match_text'), 'earliest': earliest_cand.isoformat()})
                            if earliest_cand >= start_dt and earliest_cand <= end_dt:
                                    try:
                                        _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-yearless-earliest'})
                                    except Exception:
                                        pass
                                    add_occ('todo', t.id, t.list_id, t.text, earliest_cand, None, False, '', None, source='todo-yearless-earliest')
                                    _sse_debug('calendar_occurrences.todo.added', {'todo_id': t.id, 'occurrence': earliest_cand.isoformat()})
                            continue

                        # fallback: if no candidate >= created_at, include any candidate within window
                        # but still respect the 1-year cap
                        allowed_start = max(start_dt, item_created)
                        allowed_end = min(end_dt, cap_dt)
                        if allowed_end < allowed_start:
                            continue
                        for y in range(allowed_start.year, allowed_end.year + 1):
                            try:
                                cand = datetime(y, mon, day, tzinfo=timezone.utc)
                            except Exception:
                                continue
                            if cand >= allowed_start and cand <= allowed_end:
                                try:
                                    _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-yearless-fallback'})
                                except Exception:
                                    pass
                                add_occ('todo', t.id, t.list_id, t.text, cand, None, False, '', None, source='todo-yearless-fallback')
                                break

    # sort occurrences by datetime ascending
    occurrences.sort(key=lambda x: x.get('occurrence_dt'))
    # Emit a compact SSE summary so tools can observe which occurrences were computed
    try:
        _sse_debug('calendar_occurrences.summary', {'count': len(occurrences), 'items': [{'id': o.get('id'), 'title': o.get('title'), 'occurrence_dt': o.get('occurrence_dt')} for o in occurrences]})
    except Exception:
        pass
    logger.info('calendar_occurrences computed %d occurrences before user filters (truncated=%s)', len(occurrences), truncated)

    # filter out occurrences ignored by the current user and mark completed
    try:
        from .models import CompletedOccurrence, IgnoredScope
        # fetch user's completed occ_hashes and active ignore scope_hashes
        qc = await sess.exec(select(CompletedOccurrence).where(CompletedOccurrence.user_id == owner_id))
        done_rows = qc.all()
        done_set = set(r.occ_hash for r in done_rows)
        qi = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == owner_id).where(IgnoredScope.active == True))
        ign_rows = qi.all()
        # partition ignore scopes by type for matching
        occ_ignore_hashes = set(r.scope_hash for r in ign_rows if getattr(r, 'scope_type', '') == 'occurrence' and r.scope_hash)
        list_ignore_ids = set(str(r.scope_key) for r in ign_rows if getattr(r, 'scope_type', '') == 'list')
        todo_from_scopes = []
        for r in ign_rows:
            if getattr(r, 'scope_type', '') == 'todo_from':
                todo_from_scopes.append(r)
        filtered = []
        # helper to parse ISO8601 possibly with Z
        def _parse_iso_z(s):
            try:
                if isinstance(s, datetime):
                    d = s
                else:
                    ss = (s or '').replace('Z', '+00:00')
                    d = datetime.fromisoformat(ss)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return d.astimezone(timezone.utc)
            except Exception:
                return None
        for o in occurrences:
            ignored_scopes: list[str] = []
            # occurrence-level ignore (direct hash match)
            if o.get('occ_hash') in occ_ignore_hashes:
                ignored_scopes.append('occurrence')
            # list-level ignore applies to list item occurrences
            try:
                if o.get('item_type') == 'list' and str(o.get('id')) in list_ignore_ids:
                    ignored_scopes.append('list')
            except Exception:
                pass
            # todo_from ignore applies to any item id (todo or list) from given date forward
            if todo_from_scopes:
                occ_dt = _parse_iso_z(o.get('occurrence_dt'))
                for r in todo_from_scopes:
                    try:
                        if str(o.get('id')) != str(getattr(r, 'scope_key', '')):
                            continue
                        r_from = getattr(r, 'from_dt', None)
                        r_from_dt = _parse_iso_z(r_from)
                        # if no from_dt, treat as ignore-all for that id
                        if r_from_dt is None or (occ_dt and occ_dt >= r_from_dt):
                            ignored_scopes.append('todo_from')
                            break
                    except Exception:
                        continue
            is_ignored = bool(ignored_scopes)
            # mark completed occurrences
            o['completed'] = (o.get('occ_hash') in done_set)
            if include_ignored:
                o['ignored'] = is_ignored
                if is_ignored:
                    o['ignored_scopes'] = ignored_scopes
            if not include_ignored and is_ignored:
                try:
                    _sse_debug('calendar_occurrences.filtered_out', {'occ_hash': o.get('occ_hash'), 'reason': 'ignored', 'item_id': o.get('id'), 'item_type': o.get('item_type')})
                except Exception:
                    pass
                continue
            filtered.append(o)
        occurrences = filtered
        logger.info('calendar_occurrences returning %d occurrences after filters (ignored_scopes=%d, completed=%d, include_ignored=%s)', len(occurrences), len(ign_rows), len(done_set), include_ignored)
        try:
            logger.info('calendar_occurrences.returning_items %s', [(o.get('item_type'), o.get('id'), (o.get('title') or '')[:40], o.get('occurrence_dt')) for o in occurrences])
        except Exception:
            pass
    except Exception:
        # if any DB error, don't block returning occurrences
        logger.exception('failed to fetch completed/ignored sets for user')

    return {'occurrences': occurrences, 'truncated': truncated}



@app.post('/occurrence/complete')
async def mark_occurrence_completed(request: Request, hash: str = Form(...), current_user: User = Depends(require_login)):
    """Mark a single occurrence hash as completed for the current user.

    For browser clients using cookie/session authentication require a valid
    CSRF token. Bearer-token API clients (Authorization header) are allowed
    to call this endpoint without CSRF.
    """
    # Determine whether request used bearer token (Authorization header)
    auth_hdr = request.headers.get('authorization')
    # If no Authorization header, this is likely a cookie-authenticated browser
    # request — require CSRF token. Accept token from form field _csrf or
    # cookie 'csrf_token'.
    if not auth_hdr:
        form = await request.form()
        token = form.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import CompletedOccurrence
    async with async_session() as sess:
        # idempotent upsert: insert row if not exists
        exists_q = await sess.exec(select(CompletedOccurrence).where(CompletedOccurrence.user_id == current_user.id).where(CompletedOccurrence.occ_hash == hash))
        if exists_q.first():
            return {'ok': True, 'created': False}
        row = CompletedOccurrence(user_id=current_user.id, occ_hash=hash)
        sess.add(row)
        await sess.commit()
        # Ensure positions are unique and sequential. If previous data had
        # duplicate positions (can happen with older imports or a bug),
        # normalize positions so order becomes deterministic and contiguous.
        try:
            cres = await sess.exec(select(Category).order_by(Category.position.asc(), Category.id.asc()))
            cats_all = cres.all()
            need_fix = False
            for idx, c in enumerate(cats_all):
                if c.position != idx:
                    need_fix = True
                    break
            if need_fix:
                logger.info('move_category: normalizing %d category positions', len(cats_all))
                for idx, c in enumerate(cats_all):
                    await sess.exec(sqlalchemy_update(Category).where(Category.id == c.id).values(position=idx))
                await sess.commit()
                logger.info('move_category: normalization complete')
        except Exception:
            logger.exception('move_category: failed to normalize category positions')
    return {'ok': True, 'created': True}


@app.post('/occurrence/uncomplete')
async def unmark_occurrence_completed(request: Request, hash: str = Form(...), current_user: User = Depends(require_login)):
    """Unmark a single occurrence hash as completed for the current user.

    Mirrors /occurrence/complete but removes any CompletedOccurrence rows for
    (user_id, occ_hash). Cookie-authenticated browsers must provide CSRF; bearer
    token API clients can omit CSRF.
    """
    # Require CSRF for cookie-authenticated browser requests. Allow bearer
    # token clients to call without CSRF.
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        form = await request.form()
        token = form.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import CompletedOccurrence
    async with async_session() as sess:
        # delete all rows matching this user+hash (should be at most one)
        q = await sess.exec(select(CompletedOccurrence).where(CompletedOccurrence.user_id == current_user.id).where(CompletedOccurrence.occ_hash == hash))
        rows = q.all()
        deleted = 0
        for r in rows:
            await sess.delete(r)
            deleted += 1
        if deleted:
            await sess.commit()
        return {'ok': True, 'deleted': deleted}


@app.post('/ignore/scope')
async def create_ignore_scope(request: Request, scope_type: str = Form(...), scope_key: str = Form(...), from_dt: str | None = Form(None), current_user: User = Depends(require_login)):
    """Create an ignore scope for the user.

    scope_type: 'list' or 'todo_from'
    scope_key: list id or todo id (string)
    from_dt: ISO datetime for todo_from (optional)
    Returns created scope_hash and record.
    """
    # Require CSRF for cookie-authenticated browser requests. Allow bearer
    # token clients to call without CSRF.
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        form = await request.form()
        token = form.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import IgnoredScope
    # compute scope_hash conservatively
    from .utils import ignore_list_hash, ignore_todo_from_hash
    if scope_type == 'list':
        scope_hash = ignore_list_hash(scope_key, owner_id=current_user.id)
    elif scope_type == 'todo_from' or scope_type == 'occurrence':
        # allow 'occurrence' scope_type used by the UI which supplies an occ_hash
        # for single-occurrence ignore; store it using the todo_from helper if
        # appropriate or as a direct scope_hash when occ_hash provided.
        if scope_type == 'occurrence':
            # scope_key is the occ_hash already; use it directly as scope_hash
            scope_hash = str(scope_key)
        else:
            scope_hash = ignore_todo_from_hash(scope_key, from_dt)
    else:
        raise HTTPException(status_code=400, detail='invalid scope_type')
    async with async_session() as sess:
        rec = IgnoredScope(user_id=current_user.id, scope_type=scope_type, scope_key=str(scope_key), from_dt=from_dt, scope_hash=scope_hash, active=True)
        sess.add(rec)
        await sess.commit()
        await sess.refresh(rec)
    return {'ok': True, 'scope_hash': scope_hash, 'id': rec.id}


@app.post('/ignore/unscope')
async def deactivate_ignore_scope(request: Request,
                                  scope_type: str = Form(...),
                                  scope_key: str = Form(...),
                                  from_dt: str | None = Form(None),
                                  current_user: User = Depends(require_login)):
    """Deactivate an existing ignore scope for the user (unignore).

    Accepts the same shape as /ignore/scope. For 'occurrence', scope_key is the
    occ_hash. For 'list', scope_key is the list id. For 'todo_from', scope_key is
    the todo/list id and from_dt optionally refines the match; if from_dt is
    omitted, any todo_from scope for that id will be deactivated.
    """
    # Require CSRF for cookie-authenticated browser requests. Allow bearer
    # token clients to call without CSRF.
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        form = await request.form()
        token = form.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import IgnoredScope
    from .utils import ignore_list_hash, ignore_todo_from_hash
    async with async_session() as sess:
        if scope_type == 'list':
            scope_hash = ignore_list_hash(scope_key, owner_id=current_user.id)
            q = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
        elif scope_type == 'occurrence':
            scope_hash = str(scope_key)
            q = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
        elif scope_type == 'todo_from':
            # If from_dt is provided, target the exact hash; otherwise, deactivate any
            # todo_from scopes for this scope_key (id) regardless of from_dt.
            if from_dt:
                scope_hash = ignore_todo_from_hash(scope_key, from_dt)
                q = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
            else:
                q = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_type == 'todo_from').where(IgnoredScope.scope_key == str(scope_key)).where(IgnoredScope.active == True))
        else:
            raise HTTPException(status_code=400, detail='invalid scope_type')

        rows = q.all()
        if not rows:
            return {'ok': True, 'updated': 0}
        for r in rows:
            r.active = False
            sess.add(r)
        await sess.commit()
        return {'ok': True, 'updated': len(rows)}


@app.post('/parse_text_to_rrule')
async def api_parse_text_to_rrule(request: Request, text: str = Form(None), current_user: User = Depends(require_login)):
    """Parse provided text for an anchor date and recurrence, returning DTSTART and RRULE info.

    Accepts `text` as form data or query param. Requires authentication.
    """
    # fallback to query param if form not provided
    if not text:
        text = request.query_params.get('text')
    dtstart = None
    rrule_str = ''
    rrule_params = None
    try:
        r, dt = parse_text_to_rrule(text or '')
        if dt:
            # ISO format UTC
            dtstart = dt.isoformat()
        if r is not None:
            # convert rrule object back to params and string for client
            # r._rrule is internal; instead export using our helpers
            # source recurrence dict is available via parsing again
            _, rec = parse_text_to_rrule_string(text or '')
            rrule_str = rec
            # Also provide a param dict for convenience
            # Re-parse recurrence phrase to get dict
            from .utils import parse_date_and_recurrence
            _, recdict = parse_date_and_recurrence(text or '')
            rrule_params = recurrence_dict_to_rrule_params(recdict) if recdict else None
    except Exception:
        # return empty structured response on error
        logger.exception('api_parse_text_to_rrule failed')
    return ParseRRuleResponse(dtstart=dtstart, rrule=rrule_str or '', rrule_params=rrule_params)


@app.get("/server/default_list")
async def get_default_list():
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        if not ss or not ss.default_list_id:
            raise HTTPException(status_code=404, detail="default list not set")
        q = await sess.exec(select(ListState).where(ListState.id == ss.default_list_id))
        return q.first()


class TokenRequest(BaseModel):
    username: str
    password: str


class ParseRRuleResponse(BaseModel):
    dtstart: str | None
    rrule: str
    rrule_params: dict | None


@app.post('/auth/token')
async def login_for_access_token(req: TokenRequest):
    # authenticate
    from .auth import authenticate_user
    user = await authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail='Incorrect username or password')
    access_token = create_access_token(data={'sub': user.username})
    return {'access_token': access_token, 'token_type': 'bearer'}


@app.get('/server/logs/stream')
async def stream_server_logs(request: Request):
    """SSE endpoint that streams in-memory log events as JSON lines.

    Clients should set `Accept: text/event-stream`. The stream will yield
    events named 'log' with JSON-encoded payloads.
    Access is restricted to localhost by default unless ENABLE_LOG_ENDPOINT=1.
    """
    if not _log_endpoint_allowed(request):
        raise HTTPException(status_code=403, detail='forbidden')

    async def event_generator():
        q: Queue = Queue()
        _sse_queues.append(q)
        try:
            # on connect, send a small warm-up batch of recent logs
            recent = list(_inmemory_log)
            for r in recent[-50:]:
                yield f"event: log\ndata: {json.dumps(r)}\n\n"
            while True:
                # if client disconnects, stop
                if await request.is_disconnected():
                    break
                try:
                    rec = await q.get()
                    yield f"event: log\ndata: {json.dumps(rec)}\n\n"
                except asyncio.CancelledError:
                    break
                except Exception:
                    continue
        finally:
            try:
                _sse_queues.remove(q)
            except Exception:
                pass

    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.post("/server/default_list/{list_id}")
async def set_default_list(list_id: int):
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
    # allow any list to be set as default; no special-name protection
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        ss.default_list_id = list_id
        sess.add(ss)
        await sess.commit()
        return {"default_list_id": list_id}


@app.delete("/lists/{list_id}")
async def delete_list(list_id: int, current_user: Optional[User] = Depends(get_current_user)):
    # Use a single session for the entire operation to avoid operating on a
    # closed session and to ensure commits are applied in order.
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        # enforce ownership: owners may delete; anonymous or other users are forbidden
        if current_user is None:
            # anonymous callers forbidden if list has an owner
            if lst.owner_id is not None:
                raise HTTPException(status_code=403, detail="forbidden")
        else:
            if lst.owner_id != current_user.id:
                raise HTTPException(status_code=403, detail="forbidden")

        # get server state
        qs = await sess.exec(select(ServerState))
        ss = qs.first()

        # capture todos that belong to this list (we do not move todos on list
        # deletion; preserve their list_id even if the list row is removed)
        qtodos = await sess.exec(select(Todo.id).where(Todo.list_id == list_id))
        todo_ids = [t for t in qtodos.all()]

        # remove any list-level artifacts (completion types, list hashtags)
        await sess.exec(sqlalchemy_delete(CompletionType).where(CompletionType.list_id == list_id))
        await sess.exec(sqlalchemy_delete(ListHashtag).where(ListHashtag.list_id == list_id))
        # delete the list row using a SQL-level delete to avoid ORM cascading
        await sess.exec(sqlalchemy_delete(ListState).where(ListState.id == list_id))
        # commit deletion first
        await sess.commit()
        # NOTE: we intentionally do NOT change Todo.list_id values here. Tests
        # and application expectations rely on preserving the original list_id
        # even after the ListState row is deleted.
        # If we deleted the server default, pick a new one preferring
        # modified_at, falling back to created_at. If no lists remain, clear it.
        if ss and ss.default_list_id == list_id:
            qpick = await sess.exec(select(ListState).order_by(ListState.modified_at.desc(), ListState.created_at.desc()))
            pick = qpick.first()
            if pick:
                old = ss.default_list_id
                ss.default_list_id = pick.id
                logger.info("server default list changed from %s to %s after deletion", old, pick.id)
            else:
                ss.default_list_id = None
                logger.info("server default list cleared (no lists remain) after deletion of %s", list_id)
            sess.add(ss)
            await sess.commit()

        # Cascade-delete: record tombstones and remove todos that belonged to this list and any link
        # rows (TodoCompletion, TodoHashtag). This enforces the invariant that
        # no Todo will reference a non-existent ListState after deletion.
        if todo_ids:
            # record tombstones for each todo deleted so offline clients can remove them
            for tid in todo_ids:
                ts = Tombstone(item_type='todo', item_id=tid)
                sess.add(ts)
            await sess.commit()
            # delete TodoCompletion entries for these todos
            await sess.exec(sqlalchemy_delete(TodoCompletion).where(TodoCompletion.todo_id.in_(todo_ids)))
            # delete TodoHashtag entries
            await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id.in_(todo_ids)))
            # finally delete the todos themselves
            await sess.exec(sqlalchemy_delete(Todo).where(Todo.id.in_(todo_ids)))
            await sess.commit()

    return {"deleted": list_id}


@app.post('/html_no_js/lists/{list_id}/delete')
async def html_delete_list(request: Request, list_id: int):
    # require CSRF and login for HTML list deletion
    from .auth import get_current_user as _gcu
    cu = await _gcu(token=None, request=request)
    if not cu:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, cu.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # call internal delete_list function (will enforce ownership)
    await delete_list(list_id=list_id, current_user=cu)
    # redirect back to lists index
    return RedirectResponse(url='/html_no_js/', status_code=303)


@app.post("/lists/{list_id}/hashtags")
async def add_list_hashtag(list_id: int, tag: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        # only owner may modify list-level hashtags (private-by-default)
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        # normalize tag and find or create hashtag
        try:
            tag = normalize_hashtag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
        h = qh.first()
        if not h:
            h = Hashtag(tag=tag)
            sess.add(h)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
                qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
                h = qh.first()
            else:
                await sess.refresh(h)
        # idempotent: only create link if it doesn't exist
        ql = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == list_id).where(ListHashtag.hashtag_id == h.id))
        if not ql.first():
            link = ListHashtag(list_id=list_id, hashtag_id=h.id)
            sess.add(link)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
        return {"list_id": list_id, "tag": tag}


@app.get("/lists/{list_id}/hashtags")
async def get_list_hashtags(
    list_id: int,
    include_todo_tags: bool = False,
    combine: bool = False,
    current_user: User = Depends(require_login),
):
    """Return hashtags for a list.

    Query params:
      - include_todo_tags (bool): if true, also collect hashtags attached to todos in the list.
      - combine (bool): if true, return a single deduplicated `hashtags` array combining list and todo tags.

    Ownership rules: only the list owner may call this API (same as other list APIs).
    """
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # list-level hashtags
        qh = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == list_id)
        res = await sess.exec(qh)
        rows = res.all()
        list_tags: list[str] = []
        for row in rows:
            val = row[0] if isinstance(row, (tuple, list)) else row
            if isinstance(val, str) and val:
                list_tags.append(val)

        # optionally include todo-level tags
        todo_tags: list[str] = []
        if include_todo_tags:
            qtt = (
                select(Hashtag.tag)
                .distinct()
                .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
                .join(Todo, Todo.id == TodoHashtag.todo_id)
                .where(Todo.list_id == list_id)
            )
            tres = await sess.exec(qtt)
            for row in tres.all():
                val = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(val, str) and val:
                    todo_tags.append(val)

        # return shape: preserve backwards compatibility when include_todo_tags is false
        if not include_todo_tags and not combine:
            return {"list_id": list_id, "hashtags": list_tags}

        if combine:
            # combined deduped list: list_tags first, then todo_tags not already present
            seen = set()
            combined: list[str] = []
            for t in list_tags:
                if t not in seen:
                    seen.add(t)
                    combined.append(t)
            for t in todo_tags:
                if t not in seen:
                    seen.add(t)
                    combined.append(t)
            return {"list_id": list_id, "hashtags": combined}

        # otherwise return separate keys
        return {"list_id": list_id, "list_hashtags": list_tags, "todo_hashtags": todo_tags}


@app.get("/lists/{list_id}/completion_types")
async def get_completion_types(list_id: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id))
        return qc.all()


@app.post("/lists/{list_id}/completion_types")
async def create_completion_type_endpoint(list_id: int, name: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        # do not allow creating a duplicate name for the same list
        qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).where(CompletionType.name == name))
        if qc.first():
            raise HTTPException(status_code=400, detail="completion type already exists")
        c = CompletionType(name=name, list_id=list_id)
        sess.add(c)
        try:
            await sess.commit()
        except IntegrityError:
            await sess.rollback()
            qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).where(CompletionType.name == name))
            existing = qc.first()
            if existing:
                return existing
            raise HTTPException(status_code=400, detail="could not create completion type")
        await sess.refresh(c)
        return c


@app.delete("/lists/{list_id}/completion_types/{name}")
async def delete_completion_type_endpoint(list_id: int, name: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        # ensure list exists and ownership
        ql = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = ql.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        q = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).where(CompletionType.name == name))
        c = q.first()
        if not c:
            raise HTTPException(status_code=404, detail="completion type not found")
        if c.name == "default":
            raise HTTPException(status_code=400, detail="cannot delete default completion type")
        # delete associated TodoCompletion rows
        qtc = select(TodoCompletion).where(TodoCompletion.completion_type_id == c.id)
        res = await sess.exec(qtc)
        for tc in res.all():
            await sess.delete(tc)
        await sess.delete(c)
        await sess.commit()
        return {"deleted": name}


@app.delete("/lists/{list_id}/hashtags")
async def remove_list_hashtag(list_id: int, tag: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        try:
            tag = normalize_hashtag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
        h = qh.first()
        if not h:
            raise HTTPException(status_code=404, detail="hashtag not found")
        # ensure list exists and ownership
        ql = select(ListState).where(ListState.id == list_id)
        lr = await sess.exec(ql)
        lst = lr.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        # delete link
        ql = select(ListHashtag).where(ListHashtag.list_id == list_id).where(ListHashtag.hashtag_id == h.id)
        lr = await sess.exec(ql)
        link = lr.first()
        if not link:
            raise HTTPException(status_code=404, detail="link not found")
        await sess.delete(link)
        await sess.commit()
        return {"removed": tag}


async def _remove_list_hashtag_core(sess, list_id: int, tag: str, current_user: User):
    try:
        tag = normalize_hashtag(tag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
    h = qh.first()
    if not h:
        raise HTTPException(status_code=404, detail="hashtag not found")
    ql = await sess.exec(select(ListState).where(ListState.id == list_id))
    lst = ql.first()
    if not lst:
        raise HTTPException(status_code=404, detail="list not found")
    if lst.owner_id is not None and lst.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    ql2 = select(ListHashtag).where(ListHashtag.list_id == list_id).where(ListHashtag.hashtag_id == h.id)
    lr = await sess.exec(ql2)
    link = lr.first()
    if not link:
        raise HTTPException(status_code=404, detail="link not found")
    await sess.delete(link)
    await sess.commit()
    return {"removed": tag}


@app.post('/html_no_js/lists/{list_id}/hashtags/remove')
async def html_remove_list_hashtag(request: Request, list_id: int, current_user: User = Depends(require_login)):
    # CSRF check + ownership for HTML flow
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    tag = form.get('tag')
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    async with async_session() as sess:
        await _remove_list_hashtag_core(sess, list_id, tag, current_user)
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/lists/{list_id}/hashtags')
async def html_add_list_hashtag(request: Request, list_id: int, current_user: User = Depends(require_login)):
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    tag = form.get('tag')
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    # replicate API logic to add list hashtag
    try:
        tag = normalize_hashtag(tag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
        h = qh.first()
        if not h:
            h = Hashtag(tag=tag)
            sess.add(h)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
                qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
                h = qh.first()
            else:
                await sess.refresh(h)
        ql = await sess.exec(select(ListHashtag).where(ListHashtag.list_id == list_id).where(ListHashtag.hashtag_id == h.id))
        if not ql.first():
            link = ListHashtag(list_id=list_id, hashtag_id=h.id)
            sess.add(link)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


from typing import Optional as _Optional


@app.post("/todos/{todo_id}/hashtags")
async def add_todo_hashtag(todo_id: int, tag: str, current_user: _Optional[User] = Depends(get_current_user)):
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # enforce ownership via parent list. If the list exists and is owned,
        # require the current_user to match the owner. If the list is public
        # (owner_id is None) anonymous operations are allowed.
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if lst and lst.owner_id is not None:
            if not current_user or lst.owner_id != current_user.id:
                # if the todo exists but caller is not authorized, return 403
                raise HTTPException(status_code=403, detail="forbidden")
        try:
            tag = normalize_hashtag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
        h = qh.first()
        if not h:
            h = Hashtag(tag=tag)
            sess.add(h)
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
                qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
                h = qh.first()
            else:
                await sess.refresh(h)
        link = TodoHashtag(todo_id=todo_id, hashtag_id=h.id)
        sess.add(link)
        try:
            await sess.commit()
        except IntegrityError:
            await sess.rollback()
        # Touch parent list modified_at and persist
        try:
            await _touch_list_modified(sess, todo.list_id if todo else None)
            await sess.commit()
        except Exception:
            await sess.rollback()
        return {"todo_id": todo_id, "tag": tag}


async def _remove_todo_hashtag_core(sess, todo_id: int, tag: str, current_user: _Optional[User]):
    try:
        tag = normalize_hashtag(tag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    qh = await sess.exec(select(Hashtag).where(Hashtag.tag == tag))
    h = qh.first()
    if not h:
        raise HTTPException(status_code=404, detail="hashtag not found")
    # enforce ownership via parent list
    qtodo = await sess.exec(select(Todo).where(Todo.id == todo_id))
    todo = qtodo.first()
    if not todo:
        raise HTTPException(status_code=404, detail="todo not found")
    ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
    lst = ql.first()
    if lst and lst.owner_id is not None:
        if not current_user or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
    ql2 = select(TodoHashtag).where(TodoHashtag.todo_id == todo_id).where(TodoHashtag.hashtag_id == h.id)
    lr = await sess.exec(ql2)
    link = lr.first()
    if not link:
        raise HTTPException(status_code=404, detail="link not found")
    await sess.delete(link)
    await sess.commit()
    # Touch parent list modified_at and persist
    try:
        await _touch_list_modified(sess, getattr(todo, 'list_id', None))
        await sess.commit()
    except Exception:
        await sess.rollback()
    return {"removed": tag}


@app.delete("/todos/{todo_id}/hashtags")
async def remove_todo_hashtag(todo_id: int, tag: str, current_user: _Optional[User] = Depends(get_current_user)):
    async with async_session() as sess:
        return await _remove_todo_hashtag_core(sess, todo_id, tag, current_user)


@app.post("/lists/{list_id}/state")
async def set_list_state(list_id: int, expanded: Optional[bool] = None, hide_done: Optional[bool] = None, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        if expanded is not None:
            lst.expanded = expanded
        if hide_done is not None:
            lst.hide_done = hide_done
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
        return lst


@app.post("/lists/{list_id}/icons")
async def set_list_icons(list_id: int, hide_icons: Optional[bool] = None, current_user: User = Depends(require_login)):
    """Set per-list UI icon visibility. If hide_icons is true, client should hide
    completion checkbox, pin and delete actions for the given list.
    """
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        if hide_icons is not None:
            lst.hide_icons = hide_icons
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
        return lst


@app.post('/lists/{list_id}/visit')
async def record_list_visit(list_id: int, current_user: User = Depends(require_login)):
    """Record that the current_user visited the given list. Updates visited_at or inserts a new row.

    This endpoint is intentionally small and idempotent; clients should call it
    when a list is viewed to let the server store a per-user recent-list timestamp.
    """
    async with async_session() as sess:
        # ensure list exists
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        # only allow recording visits for lists the user may legitimately access
        if lst.owner_id is not None and lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        now = now_utc()
        # Top-N behavior: default top_n preserved order size
        try:
            top_n = int(os.getenv('RECENT_LISTS_TOP_N', '10'))
        except Exception:
            top_n = 10

        # Read existing row if present
        qv = await sess.exec(select(RecentListVisit).where(RecentListVisit.user_id == current_user.id).where(RecentListVisit.list_id == list_id))
        rv = qv.first()

        if rv and rv.position is not None and rv.position < top_n:
            # If already in top-N, only update visited_at, preserving positions
            rv.visited_at = now
            sess.add(rv)
            await sess.commit()
        else:
            # Need to insert/update this row as the new top (position=0)
            # Shift existing positions down by +1 for positions in [0, top_n-2]
            try:
                # We want to increment positions 0..(top_n-2) so the previous
                # position top_n-1 becomes the candidate for eviction. Use a
                # consistent threshold `evict_pos = top_n - 1` for both queries.
                evict_pos = max(0, top_n - 1)
                shift_sql = text(
                    "UPDATE recentlistvisit SET position = position + 1 "
                    "WHERE user_id = :uid AND position IS NOT NULL AND position < :maxpos"
                )
                await sess.exec(shift_sql.bindparams(uid=current_user.id, maxpos=evict_pos))
                # Any position that is now >= evict_pos should be evicted (set NULL)
                clear_sql = text(
                    "UPDATE recentlistvisit SET position = NULL WHERE user_id = :uid AND position >= :maxpos"
                )
                await sess.exec(clear_sql.bindparams(uid=current_user.id, maxpos=evict_pos))
            except Exception:
                # Best-effort: ignore shift failures and continue
                logger.exception('failed to shift recentlist positions')

            if rv:
                rv.position = 0
                rv.visited_at = now
                sess.add(rv)
            else:
                rv = RecentListVisit(user_id=current_user.id, list_id=list_id, visited_at=now, position=0)
                sess.add(rv)
            await sess.commit()

        # Prune older visits for this user to keep storage bounded.
        # Configurable via RECENT_LISTS_PER_USER env var (default: 100).
        try:
            cap = int(os.getenv('RECENT_LISTS_PER_USER', '100'))
        except Exception:
            cap = 100
        if cap > 0:
            # Delete pairs (user_id, list_id) whose row is ranked > cap by visited_at
            prune_sql = text(
                "DELETE FROM recentlistvisit WHERE (user_id, list_id) IN ("
                "SELECT user_id, list_id FROM ("
                "SELECT user_id, list_id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY visited_at DESC) AS rn "
                "FROM recentlistvisit WHERE user_id = :uid) t WHERE t.rn > :cap)"
            )
            try:
                await sess.exec(prune_sql.bindparams(uid=current_user.id, cap=cap))
                await sess.commit()
            except Exception:
                # Best-effort pruning; do not fail the request if pruning SQL isn't supported
                pass

        return {"list_id": list_id, "visited_at": now}


@app.get('/lists/recent')
async def get_recent_lists(limit: int = 25, current_user: User = Depends(require_login)):
    """Return the recent lists visited by the current user ordered by preserved top-N then recent views."""
    try:
        top_n = int(os.getenv('RECENT_LISTS_TOP_N', '10'))
    except Exception:
        top_n = 10
    async with async_session() as sess:
        # First fetch top-N positioned rows ordered by position ASC
        top_q = select(RecentListVisit).where(RecentListVisit.user_id == current_user.id).where(RecentListVisit.position != None).order_by(RecentListVisit.position.asc()).limit(top_n)
        top_res = await sess.exec(top_q)
        top_rows = top_res.all()
        top_ids = [r.list_id for r in top_rows]

        results: list[dict] = []
        # load ListState for top rows preserving order
        if top_ids:
            qlists = select(ListState).where(ListState.id.in_(top_ids))
            lres = await sess.exec(qlists)
            lmap = {l.id: l for l in lres.all()}
            for r in top_rows:
                lst = lmap.get(r.list_id)
                if lst:
                    # attach visited_at for template/clients
                    try:
                        setattr(lst, 'visited_at', r.visited_at)
                    except Exception:
                        pass
                    results.append(lst)

        # If we still need more, fetch others ordered by visited_at desc excluding top_ids
        remaining = max(0, limit - len(results))
        if remaining > 0:
            q = select(ListState).join(RecentListVisit, RecentListVisit.list_id == ListState.id).where(RecentListVisit.user_id == current_user.id)
            if top_ids:
                q = q.where(RecentListVisit.list_id.notin_(top_ids))
            q = q.order_by(RecentListVisit.visited_at.desc()).limit(remaining)
            res = await sess.exec(q)
            other_lists = res.all()
            for lst in other_lists:
                results.append(lst)

        return results


@app.post("/todos")
async def create_todo(text: str, note: Optional[str] = None, list_id: int = None, current_user: User = Depends(require_login)):
    """
    Create a todo in an explicit, existing list. `list_id` is required and
    must reference an existing ListState. Requires authentication; the
    authenticated user may create todos in lists they own or in public lists
    (owner_id is None). Anonymous creation is not permitted.
    """
    if list_id is None:
        raise HTTPException(status_code=400, detail="list_id is required")

    async with async_session() as sess:
        # ensure the list exists
        ql = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = ql.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")

        # enforce ownership: authenticated users may use their lists or public lists
        user_id = current_user.id
        if lst.owner_id not in (None, user_id):
            raise HTTPException(status_code=403, detail="forbidden")

        # Remove inline hashtags from the saved text; hashtags will be linked separately
        try:
            clean_text = remove_hashtags_from_text(text.lstrip())
        except Exception:
            clean_text = text
        # compute recurrence metadata for the todo text/note and persist
        from .utils import parse_text_to_rrule_string, parse_date_and_recurrence, recurrence_dict_to_rrule_string
        dtstart_val, rrule_str = parse_text_to_rrule_string(text or '')
        _, recdict = parse_date_and_recurrence(text or '')
        import json
        meta_json = json.dumps(recdict) if recdict else None
        todo = Todo(text=clean_text, note=note, list_id=list_id, recurrence_rrule=rrule_str or None, recurrence_meta=meta_json, recurrence_dtstart=dtstart_val)
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        # Touch parent list modified_at and persist
        try:
            await _touch_list_modified(sess, list_id)
            await sess.commit()
        except Exception:
            await sess.rollback()
        # Precompute response while session is active to avoid lazy loads later
        todo_resp = _serialize_todo(todo, [])
    # Capture id before leaving session to guarantee no lazy access later
    todo_id_val = int(todo.id)
    # Log ParamEvent todo ids to help trace failing parametrized tests
    try:
        if todo_resp and todo_resp.get('text', '').startswith('ParamEvent'):
            logger.info(f"POST /todos created ParamEvent todo id={todo_id_val} title={todo_resp.get('text')}")
    except Exception:
        # non-critical logging; swallow any issues
        logger.debug('failed to log ParamEvent todo creation')
    # extract hashtags from original submitted text and note and sync links
    tags = extract_hashtags(text) + extract_hashtags(note)
    # ensure unique
    seen = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    async with async_session() as sess:
        await _sync_todo_hashtags(sess, todo_id_val, seen)
    return todo_resp


@app.get("/todos/{todo_id}")
async def get_todo(todo_id: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # check ownership via list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        # require login: only owners or public lists allowed
        user_id = current_user.id
        if lst and lst.owner_id not in (None, user_id):
            raise HTTPException(status_code=403, detail="forbidden")
        qc = select(TodoCompletion).where(TodoCompletion.todo_id == todo_id)
        cres = await sess.exec(qc)
        completions = [{"completion_type_id": c.completion_type_id, "done": c.done} for c in cres.all()]
        await sess.refresh(todo)
        return _serialize_todo(todo, completions)


@app.patch("/todos/{todo_id}")
async def update_todo(todo_id: int, text: Optional[str] = None, note: Optional[str] = None, list_id: Optional[int] = None, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # capture original parent list for potential move
        old_list_id = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None
        if text is not None:
            # Strip inline hashtags from saved text; tags will be managed separately
            try:
                todo.text = remove_hashtags_from_text(text.lstrip())
            except Exception:
                todo.text = text
        if note is not None:
            todo.note = note
        # If text or note changed, recompute recurrence metadata and persist.
        if text is not None or note is not None:
            try:
                from .utils import parse_text_to_rrule_string, parse_date_and_recurrence
                dtstart_val, rrule_str = parse_text_to_rrule_string(todo.text + '\n' + (todo.note or ''))
                _, recdict = parse_date_and_recurrence(todo.text + '\n' + (todo.note or ''))
                import json
                todo.recurrence_rrule = rrule_str or None
                todo.recurrence_meta = json.dumps(recdict) if recdict else None
                todo.recurrence_dtstart = dtstart_val
            except Exception:
                # Do not block updates on recurrence parsing failures; leave existing values
                logger.exception('failed to recompute recurrence metadata during update_todo')
        if list_id is not None:
            # ensure the target list exists
            ql = await sess.exec(select(ListState).where(ListState.id == list_id))
            lst = ql.first()
            if not lst:
                raise HTTPException(status_code=404, detail="list not found")
            # enforce ownership rules: only owners or public lists allowed
            user_id = current_user.id
            if lst.owner_id not in (None, user_id):
                raise HTTPException(status_code=403, detail="forbidden")
            todo.list_id = list_id
        todo.modified_at = now_utc()
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        # Capture list id after potential move
        parent_list_id = int(todo.list_id) if todo.list_id is not None else None
        # fetch completions for serialization
        qc = select(TodoCompletion).where(TodoCompletion.todo_id == todo_id)
        cres = await sess.exec(qc)
        completions = [{"completion_type_id": c.completion_type_id, "done": c.done} for c in cres.all()]
        # Precompute response dict before further commits to prevent lazy loads later
        todo_resp = _serialize_todo(todo, completions)
        # After updating the todo row, merge any newly provided hashtags with existing ones.
        # Only change tags if the request included new hashtags in text and/or note.
        provided_new_tags = []
        if text is not None:
            provided_new_tags += extract_hashtags(text)
        if note is not None:
            provided_new_tags += extract_hashtags(note)
        # dedupe provided
        pn_seen: list[str] = []
        for t in provided_new_tags:
            if t not in pn_seen:
                pn_seen.append(t)
        if pn_seen:
            # read existing tags for this todo
            rtags = await sess.exec(select(Hashtag.tag).join(TodoHashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id == todo.id))
            existing_tags = []
            for row in rtags.all():
                val = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(val, str) and val and val not in existing_tags:
                    existing_tags.append(val)
            # order-preserving union: existing first, then newly provided not already present
            merged = list(existing_tags)
            for t in pn_seen:
                if t not in merged:
                    merged.append(t)
            await _sync_todo_hashtags(sess, todo.id, merged)
        # Touch parent list modified_at and persist (and the old list if the todo moved)
        try:
            await _touch_list_modified(sess, parent_list_id)
            if old_list_id is not None and old_list_id != parent_list_id:
                await _touch_list_modified(sess, old_list_id)
            await sess.commit()
        except Exception:
            await sess.rollback()
        return todo_resp


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: int, current_user: Optional[User] = Depends(get_current_user)):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            # resource doesn't exist; return 404 regardless of auth
            raise HTTPException(status_code=404, detail="todo not found")
        # check ownership via parent list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        # enforce ownership: owners or public lists allowed; anonymous callers forbidden for owned lists
        if lst:
            if current_user is None:
                if lst.owner_id is not None:
                    raise HTTPException(status_code=403, detail="forbidden")
            else:
                if lst.owner_id not in (None, current_user.id):
                    raise HTTPException(status_code=403, detail="forbidden")
    # delete dependent link/completion rows at the DB level first to avoid
    # SQLAlchemy trying to null-out PK columns on dependent rows during flush
    await sess.exec(sqlalchemy_delete(TodoCompletion).where(TodoCompletion.todo_id == todo_id))
    await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id == todo_id))
    # now delete the todo
    # record tombstone so offline clients learn about the deletion
    ts = Tombstone(item_type='todo', item_id=todo_id)
    sess.add(ts)
    await sess.delete(todo)
    # Touch parent list modified_at and persist
    try:
        await _touch_list_modified(sess, getattr(todo, 'list_id', None))
    except Exception:
        pass
    await sess.commit()
    return {"ok": True}


@app.post('/todos/{todo_id}/pin')
async def pin_todo(todo_id: int, pinned: bool = Form(...), current_user: User = Depends(require_login)):
    """Set or clear the pinned flag on a todo. Accepts form-encoded `pinned` (true/false).

    This endpoint is intended for HTML clients (POST) and programmatic clients
    can use the JSON `/todos/{id}` PATCH endpoint in future.
    """
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # ownership via list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if lst and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        todo.pinned = bool(pinned)
        todo.modified_at = now_utc()
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        # also touch the parent list modified_at
        try:
            await _touch_list_modified(sess, getattr(todo, 'list_id', None))
            await sess.commit()
        except Exception:
            await sess.rollback()
    return {'id': todo.id, 'pinned': todo.pinned}


@app.post('/html_no_js/todos/{todo_id}/pin')
async def html_pin_todo(request: Request, todo_id: int, pinned: str = Form(...), current_user: User = Depends(require_login)):
    # require CSRF for html_no_js flow
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # interpret pinned string
    pinned_bool = str(pinned).lower() in ('1', 'true', 'yes')
    # reuse pin_todo logic
    await pin_todo(todo_id=todo_id, pinned=pinned_bool, current_user=current_user)
    # after pinning, redirect back to the parent list so the user stays on the list view
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if todo and getattr(todo, 'list_id', None):
            return RedirectResponse(url=f'/html_no_js/lists/{todo.list_id}#todo-{todo_id}', status_code=303)
    # fallback: redirect to the todo page if we couldn't determine the list
    return RedirectResponse(url=f'/html_no_js/todos/{todo_id}', status_code=303)


@app.post("/todos/{todo_id}/defer")
async def defer_todo(todo_id: int, hours: int):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        todo.deferred_until = now_utc() + timedelta(hours=hours)
        todo.modified_at = now_utc()
        sess.add(todo)
        await sess.commit()
        await sess.refresh(todo)
        # touch parent list modified_at
        try:
            await _touch_list_modified(sess, getattr(todo, 'list_id', None))
            await sess.commit()
        except Exception:
            await sess.rollback()
        return {"id": todo.id, "deferred_until": todo.deferred_until.isoformat()}


@app.post("/todos/{todo_id}/complete")
async def complete_todo(todo_id: int, completion_type: str = "default", done: bool = True):
    async with async_session() as sess:
        q = select(Todo).where(Todo.id == todo_id)
        res = await sess.exec(q)
        todo = res.first()
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # find or create completion type for the list
        qc = select(CompletionType).where(CompletionType.list_id == todo.list_id).where(CompletionType.name == completion_type)
        cres = await sess.exec(qc)
        ctype = cres.first()
        if not ctype:
            ctype = CompletionType(name=completion_type, list_id=todo.list_id)
            sess.add(ctype)
            await sess.commit()
            await sess.refresh(ctype)
        # upsert completion
        qtc = select(TodoCompletion).where(TodoCompletion.todo_id == todo_id).where(TodoCompletion.completion_type_id == ctype.id)
        rtc = await sess.exec(qtc)
        comp = rtc.first()
        if not comp:
            comp = TodoCompletion(todo_id=todo_id, completion_type_id=ctype.id, done=done)
        else:
            comp.done = done
        sess.add(comp)
        await sess.commit()
        # touch parent list modified_at
        try:
            await _touch_list_modified(sess, getattr(todo, 'list_id', None))
            await sess.commit()
        except Exception:
            await sess.rollback()
        return {"todo_id": todo_id, "completion_type": completion_type, "done": done}


@app.post("/admin/undefer")
async def undefer_due():
    async with async_session() as sess:
        now = now_utc()
        q = select(Todo).where(Todo.deferred_until != None).where(Todo.deferred_until <= now)
        res = await sess.exec(q)
        due = res.all()
        affected_lists: set[int] = set()
        for t in due:
            t.deferred_until = None
            t.modified_at = now_utc()
            sess.add(t)
            if getattr(t, 'list_id', None) is not None:
                try:
                    affected_lists.add(int(t.list_id))
                except Exception:
                    pass
        await sess.commit()
        # touch lists that had todos changed
        try:
            for lid in affected_lists:
                await _touch_list_modified(sess, lid)
            await sess.commit()
        except Exception:
            await sess.rollback()
        return {"undeferred": len(due)}


    @app.post('/admin/prune_tombstones')
    async def prune_tombstones(ttl_days: Optional[int] = None, current_user: User = Depends(require_login)):
        """Prune tombstones older than ttl_days (default: TOMBSTONE_TTL_DAYS env or 90)."""
        # simple admin check
        if not getattr(current_user, 'is_admin', False):
            raise HTTPException(status_code=403, detail='admin required')
        ttl = ttl_days or int(os.getenv("TOMBSTONE_TTL_DAYS", "90"))
        from datetime import timedelta
        cutoff = now_utc() - timedelta(days=ttl)
        async with async_session() as sess:
            stmt = sqlalchemy_delete(Tombstone).where(Tombstone.created_at != None).where(Tombstone.created_at < cutoff)
            res = await sess.exec(stmt)
            try:
                deleted = res.rowcount if hasattr(res, 'rowcount') and res.rowcount is not None else 0
            except Exception:
                deleted = 0
            if deleted:
                await sess.commit()
        return {'pruned': deleted, 'cutoff': cutoff.isoformat()}


def _serialize_todo(todo: Todo, completions: list[dict] | None = None) -> dict:
    def _fmt(dt):
        if not dt:
            return None
        # If the DB returned a naive datetime, assume UTC and attach tzinfo
        if dt.tzinfo is None:
            from datetime import timezone as _tz

            dt = dt.replace(tzinfo=_tz.utc)
        return dt.isoformat()

    return {
        "id": todo.id,
        "text": todo.text,
    "pinned": getattr(todo, 'pinned', False),
        "note": todo.note,
        "created_at": _fmt(todo.created_at),
        "modified_at": _fmt(todo.modified_at),
        "deferred_until": _fmt(todo.deferred_until),
        "list_id": todo.list_id,
        "completions": completions or [],
    }


def _serialize_list(lst: ListState) -> dict:
    def _fmt(dt):
        if not dt:
            return None
        if dt.tzinfo is None:
            from datetime import timezone as _tz

            dt = dt.replace(tzinfo=_tz.utc)
        return dt.isoformat()

    return {
        "id": lst.id,
        "name": lst.name,
        "owner_id": lst.owner_id,
        "created_at": _fmt(lst.created_at),
        "modified_at": _fmt(lst.modified_at),
        "expanded": getattr(lst, 'expanded', None),
        "hide_done": getattr(lst, 'hide_done', None),
    }


async def _touch_list_modified(sess, list_id: Optional[int]):
    """Set the parent list's modified_at to now. Caller is responsible for commit.

    Safe to call with None list_id.
    """
    if list_id is None:
        return
    try:
        lst = await sess.get(ListState, list_id)
        if lst:
            lst.modified_at = now_utc()
            sess.add(lst)
    except Exception:
        # non-fatal; do not block todo ops on list timestamp failures
        logger.debug('failed to touch list modified_at for list_id=%s', list_id)


async def _sync_todo_hashtags(sess, todo_id: int, tags: list[str]):
    """Ensure Hashtag rows exist for each tag and ensure TodoHashtag links exist
    for the given todo. This is idempotent and safe under concurrency.
    """
    # Normalize/dedupe defensively to avoid invalid entries (e.g., '#')
    raw = [t for t in (tags or []) if t]
    norm: list[str] = []
    for t in raw:
        try:
            nt = normalize_hashtag(t)
        except Exception:
            continue
        if nt and nt not in norm:
            norm.append(nt)
    tags = norm

    if not tags:
        # If no tags are desired, remove all links for this todo and exit.
        await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id == todo_id))
        await sess.commit()
        return

    # Step 1: ensure Hashtag rows exist and collect a map of tag -> id.
    existing: dict[str, int] = {}
    res = await sess.exec(select(Hashtag.tag, Hashtag.id).where(Hashtag.tag.in_(tags)))
    for tag_val, id_val in res.all():
        existing[tag_val] = id_val

    missing = [t for t in tags if t not in existing]
    if missing:
        # Try to insert all missing hashtags in one go; fall back to reselect on conflict.
        new_objs = [Hashtag(tag=t) for t in missing]
        sess.add_all(new_objs)
        try:
            # flush assigns primary keys without committing the transaction
            await sess.flush()
            for h in new_objs:
                # capture scalar ids now and discard ORM refs
                existing[h.tag] = int(h.id)
        except IntegrityError:
            # Another transaction raced us; drop pending inserts and reselect ids
            await sess.rollback()
            res2 = await sess.exec(select(Hashtag.tag, Hashtag.id).where(Hashtag.tag.in_(tags)))
            for tag_val, id_val in res2.all():
                existing[tag_val] = id_val

    desired_ids = [existing[t] for t in tags if t in existing]
    if not desired_ids:
        # Nothing to link; ensure all links are removed.
        await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id == todo_id))
        await sess.commit()
        return

    # Step 2: determine current links, then compute deletes/inserts before mutating.
    res_links0 = await sess.exec(select(TodoHashtag.hashtag_id).where(TodoHashtag.todo_id == todo_id))
    have_rows0 = res_links0.all()
    existing_ids: set[int] = set(
        [r[0] if isinstance(r, (tuple, list)) else int(getattr(r, 'hashtag_id', r)) for r in have_rows0]
    )
    desired_set = set(desired_ids)
    to_delete = list(existing_ids - desired_set)
    to_insert = [hid for hid in desired_ids if hid not in existing_ids]

    if to_delete:
        await sess.exec(
            sqlalchemy_delete(TodoHashtag)
            .where(TodoHashtag.todo_id == todo_id)
            .where(TodoHashtag.hashtag_id.in_(to_delete))
        )

    if to_insert:
        sess.add_all([TodoHashtag(todo_id=todo_id, hashtag_id=hid) for hid in to_insert])
        try:
            await sess.flush()
        except IntegrityError:
            # Rollback and retry with granular operations (recompute sets post-rollback)
            await sess.rollback()
            res_links1 = await sess.exec(select(TodoHashtag.hashtag_id).where(TodoHashtag.todo_id == todo_id))
            have_rows1 = res_links1.all()
            existing_ids2: set[int] = set(
                [r[0] if isinstance(r, (tuple, list)) else int(getattr(r, 'hashtag_id', r)) for r in have_rows1]
            )
            to_delete2 = list(existing_ids2 - desired_set)
            to_insert2 = [hid for hid in desired_ids if hid not in existing_ids2]
            if to_delete2:
                await sess.exec(
                    sqlalchemy_delete(TodoHashtag)
                    .where(TodoHashtag.todo_id == todo_id)
                    .where(TodoHashtag.hashtag_id.in_(to_delete2))
                )
            for hid in to_insert2:
                sess.add(TodoHashtag(todo_id=todo_id, hashtag_id=hid))
                try:
                    await sess.flush()
                except IntegrityError:
                    await sess.rollback()

    # Single commit at the end for all changes (creates, deletes, links)
    await sess.commit()
    return


async def _sync_list_hashtags(sess, list_id: int, tags: list[str]):
    """Ensure list-level hashtags reflect the provided tags (idempotent).
    Creates missing Hashtag rows, then updates ListHashtag links via set-diff.
    """
    # Normalize/dedupe to ensure only valid hashtags are synced
    raw = [t for t in (tags or []) if t]
    norm: list[str] = []
    for t in raw:
        try:
            nt = normalize_hashtag(t)
        except Exception:
            continue
        if nt and nt not in norm:
            norm.append(nt)
    tags = norm
    if not tags:
        await sess.exec(sqlalchemy_delete(ListHashtag).where(ListHashtag.list_id == list_id))
        await sess.commit()
        return
    # ensure Hashtag rows exist
    existing: dict[str, int] = {}
    res = await sess.exec(select(Hashtag.tag, Hashtag.id).where(Hashtag.tag.in_(tags)))
    for tag_val, id_val in res.all():
        existing[tag_val] = id_val
    missing = [t for t in tags if t not in existing]
    if missing:
        objs = [Hashtag(tag=t) for t in missing]
        sess.add_all(objs)
        try:
            await sess.flush()
            for h in objs:
                existing[h.tag] = int(h.id)
        except IntegrityError:
            await sess.rollback()
            res2 = await sess.exec(select(Hashtag.tag, Hashtag.id).where(Hashtag.tag.in_(tags)))
            for tag_val, id_val in res2.all():
                existing[tag_val] = id_val
    desired_ids = [existing[t] for t in tags if t in existing]
    # current links
    rl = await sess.exec(select(ListHashtag.hashtag_id).where(ListHashtag.list_id == list_id))
    have_rows = rl.all()
    existing_ids: set[int] = set([r[0] if isinstance(r, (tuple, list)) else int(getattr(r, 'hashtag_id', r)) for r in have_rows])
    desired_set = set(desired_ids)
    to_delete = list(existing_ids - desired_set)
    to_insert = [hid for hid in desired_ids if hid not in existing_ids]
    if to_delete:
        await sess.exec(
            sqlalchemy_delete(ListHashtag)
            .where(ListHashtag.list_id == list_id)
            .where(ListHashtag.hashtag_id.in_(to_delete))
        )
    if to_insert:
        sess.add_all([ListHashtag(list_id=list_id, hashtag_id=hid) for hid in to_insert])
        try:
            await sess.flush()
        except IntegrityError:
            await sess.rollback()
            # recompute and add individually
            rl2 = await sess.exec(select(ListHashtag.hashtag_id).where(ListHashtag.list_id == list_id))
            have_rows2 = rl2.all()
            existing_ids2: set[int] = set([r[0] if isinstance(r, (tuple, list)) else int(getattr(r, 'hashtag_id', r)) for r in have_rows2])
            to_delete2 = list(existing_ids2 - desired_set)
            to_insert2 = [hid for hid in desired_ids if hid not in existing_ids2]
            if to_delete2:
                await sess.exec(
                    sqlalchemy_delete(ListHashtag)
                    .where(ListHashtag.list_id == list_id)
                    .where(ListHashtag.hashtag_id.in_(to_delete2))
                )
            for hid in to_insert2:
                sess.add(ListHashtag(list_id=list_id, hashtag_id=hid))
                try:
                    await sess.flush()
                except IntegrityError:
                    await sess.rollback()
    await sess.commit()
    return


### HTML no-JS client routes


@app.get("/html_no_js/", response_class=HTMLResponse)
async def html_index(request: Request):
    # Resolve current user from cookies/tokens but do not let auth errors
    # return a JSON 401 for the HTML UI; treat invalid credentials as
    # anonymous and redirect to login so user sees the HTML flow.
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        current_user = None
    # Redirect anonymous users to the login page for the HTML UI
    if not current_user:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    # keyset pagination: 50 lists per page using (created_at DESC, id DESC)
    per_page = 50
    dir_param = request.query_params.get('dir', 'next')  # 'next' for older, 'prev' for newer
    cursor_created_at_str = request.query_params.get('cursor_created_at')
    cursor_id_str = request.query_params.get('cursor_id')
    cursor_dt = None
    cursor_id = None
    if cursor_created_at_str and cursor_id_str:
        try:
            # ISO 8601 with offset, e.g., 2025-08-23T12:34:56.123456+00:00
            from datetime import datetime
            cursor_dt = datetime.fromisoformat(cursor_created_at_str)
            cursor_id = int(cursor_id_str)
        except Exception:
            cursor_dt, cursor_id = None, None
    async with async_session() as sess:
        owner_id = current_user.id
        # base ordered query (newest first)
        q = select(ListState).where(ListState.owner_id == owner_id)
        # apply cursor condition if present
        if cursor_dt is not None and cursor_id is not None:
            if dir_param == 'prev':
                # fetch newer than cursor
                q = q.where(or_(ListState.created_at > cursor_dt,
                                and_(ListState.created_at == cursor_dt, ListState.id > cursor_id)))
            else:
                # fetch older than cursor (default)
                q = q.where(or_(ListState.created_at < cursor_dt,
                                and_(ListState.created_at == cursor_dt, ListState.id < cursor_id)))
        q = q.order_by(ListState.created_at.desc(), ListState.id.desc()).limit(per_page)
        res_page = await sess.exec(q)
        lists = res_page.all()

        # determine prev/next availability via lightweight existence checks
        has_prev = False
        has_next = False
        next_cursor_created_at = None
        next_cursor_id = None
        prev_cursor_created_at = None
        prev_cursor_id = None
        if lists:
            first = lists[0]
            last = lists[-1]
            # compute cursors from current window
            prev_cursor_created_at, prev_cursor_id = first.created_at, first.id
            next_cursor_created_at, next_cursor_id = last.created_at, last.id
            # is there anything newer than first?
            q_prev_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(
                or_(ListState.created_at > first.created_at,
                    and_(ListState.created_at == first.created_at, ListState.id > first.id))
            ).limit(1)
            r_prev = await sess.exec(q_prev_exists)
            has_prev = r_prev.first() is not None
            # is there anything older than last?
            q_next_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(
                or_(ListState.created_at < last.created_at,
                    and_(ListState.created_at == last.created_at, ListState.id < last.id))
            ).limit(1)
            r_next = await sess.exec(q_next_exists)
            has_next = r_next.first() is not None
    # convert ORM ListState objects to plain dicts to avoid lazy-loading
        list_rows = []
        list_ids = [l.id for l in lists]
        tag_map: dict[int, list[str]] = {}
        if list_ids:
            qlh = await sess.exec(select(ListHashtag.list_id, Hashtag.tag).where(ListHashtag.list_id.in_(list_ids)).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id))
            rows = qlh.all()
            for lid, tag in rows:
                tag_map.setdefault(lid, []).append(tag)
        for l in lists:
            list_rows.append({
                "id": l.id,
                "name": l.name,
                "completed": l.completed,
                "owner_id": l.owner_id,
                "created_at": l.created_at,
                "modified_at": getattr(l, 'modified_at', None),
                "category_id": l.category_id,
                "hashtags": tag_map.get(l.id, []),
            })
        # group lists by category for easier template rendering
        lists_by_category: dict[int, list[dict]] = {}
        for row in list_rows:
            cid = row.get('category_id') or 0
            lists_by_category.setdefault(cid, []).append(row)
        # fetch categories ordered by position
        categories = []
        try:
            qcat = select(Category).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cres.all()]
        except Exception:
            categories = []
        # Also fetch pinned todos from lists visible to this user (owned or public)
        pinned_todos = []
        try:
            # visible lists: owned by user or public (owner_id is NULL)
            qvis = select(ListState).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            rvis = await sess.exec(qvis)
            vis_lists = rvis.all()
            vis_ids = [l.id for l in vis_lists]
            if vis_ids:
                qp = select(Todo).where(Todo.pinned == True).where(Todo.list_id.in_(vis_ids)).order_by(Todo.modified_at.desc())
                pres = await sess.exec(qp)
                pin_rows = pres.all()
                # map list ids to names
                lm = {l.id: l.name for l in vis_lists}
                # include modification timestamp so templates can explicitly sort by it
                pinned_todos = [
                    {
                        'id': t.id,
                        'text': t.text,
                        'list_id': t.list_id,
                        'list_name': lm.get(t.list_id),
                        'modified_at': (t.modified_at.isoformat() if getattr(t, 'modified_at', None) else None),
                    }
                    for t in pin_rows
                ]
                # attach tags for pinned todos
                pin_ids = [p['id'] for p in pinned_todos]
                if pin_ids:
                    qtp = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(pin_ids))
                    pres2 = await sess.exec(qtp)
                    pm = {}
                    for tid, tag in pres2.all():
                        pm.setdefault(tid, []).append(tag)
                    for p in pinned_todos:
                        p['tags'] = pm.get(p['id'], [])
        except Exception:
            # if DB lacks the pinned column or some error occurs, show no pinned todos
            pinned_todos = []
        # prepare cursors for template (ISO strings)
        def _iso(dt):
            try:
                return dt.isoformat() if dt else None
            except Exception:
                return None
        cursors = {
            "has_prev": has_prev,
            "has_next": has_next,
            "prev_cursor_created_at": _iso(prev_cursor_created_at),
            "prev_cursor_id": prev_cursor_id,
            "next_cursor_created_at": _iso(next_cursor_created_at),
            "next_cursor_id": next_cursor_id,
        }
    # no special ordering by name; lists are returned newest-first by created_at
    csrf_token = None
    if current_user:
        from .auth import create_csrf_token
        csrf_token = create_csrf_token(current_user.username)
    client_tz = await get_session_timezone(request)
    # Allow a developer override via query param to force the iOS-only template
    # for testing (e.g., /html_no_js/?force_ios=1). Otherwise, fall back to
    # automatic UA detection.
    force_ios = request.query_params.get('force_ios') == '1' or request.query_params.get('ios') == '1'
    ua = (request.headers.get('user-agent') or '')
    # Log which template we will render and why (truncate UA to avoid huge logs)
    try:
        if force_ios:
            logger.info('html_index: rendering index_ios_safari (forced) ua=%s', ua[:200])
            return TEMPLATES.TemplateResponse(request, "index_ios_safari.html", {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "cursors": cursors, "categories": categories})
        if is_ios_safari(request):
            logger.info('html_index: rendering index_ios_safari (ua-detected) ua=%s', ua[:200])
            return TEMPLATES.TemplateResponse(request, "index_ios_safari.html", {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "cursors": cursors, "categories": categories})
        logger.info('html_index: rendering index.html (default) ua=%s', ua[:200])
        return TEMPLATES.TemplateResponse(request, "index.html", {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "cursors": cursors, "categories": categories})
    except Exception:
        # Ensure we always return something even if logging fails
        return TEMPLATES.TemplateResponse(request, "index.html", {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "cursors": cursors, "categories": categories})


@app.get('/html_no_js/categories', response_class=HTMLResponse)
async def html_categories(request: Request):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        current_user = None
    if not current_user:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    # Serve a JS-driven page; the client will fetch categories via API.
    csrf_token = None
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    return TEMPLATES.TemplateResponse(request, 'categories.html', { 'request': request, 'csrf_token': csrf_token })


@app.get('/api/categories')
async def api_get_categories(request: Request, current_user: User = Depends(require_login)):
    """Return JSON list of categories ordered by position."""
    async with async_session() as sess:
        try:
            cres = await sess.exec(select(Category).order_by(Category.position.asc(), Category.id.asc()))
            cats = cres.all()
            return {'categories': [{'id': c.id, 'name': c.name, 'position': c.position} for c in cats]}
        except Exception:
            return {'categories': []}


class MoveCatRequest(BaseModel):
    direction: str


async def _normalize_category_positions(sess) -> list[Category]:
    """Ensure Category.position values are contiguous (0..N-1) and unique.
    Returns categories ordered by position after normalization."""
    cres = await sess.exec(select(Category).order_by(Category.position.asc(), Category.id.asc()))
    cats = cres.all()
    changed = False
    for idx, c in enumerate(cats):
        try:
            if c.position != idx:
                await sess.exec(sqlalchemy_update(Category).where(Category.id == c.id).values(position=idx))
                changed = True
        except Exception:
            # fallback: still attempt to continue normalizing others
            logger.exception('normalize positions failed for cat_id=%s', getattr(c, 'id', None))
    if changed:
        try:
            await sess.commit()
        except Exception:
            logger.exception('commit failed during category position normalization')
    # re-read in normalized order
    cres2 = await sess.exec(select(Category).order_by(Category.position.asc(), Category.id.asc()))
    return cres2.all()


@app.post('/api/categories/{cat_id}/move')
async def api_move_category(request: Request, cat_id: int, payload: MoveCatRequest, current_user: User = Depends(require_login)):
    """Move category up or down. Accepts JSON {direction: 'up'|'down'}."""
    # Allow bearer-token API clients (Authorization header) without CSRF.
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        # require CSRF for cookie-auth browser clients
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = body.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    direction = payload.direction if payload and getattr(payload, 'direction', None) else None
    if direction not in ('up', 'down'):
        raise HTTPException(status_code=400, detail='invalid direction')

    async with async_session() as sess:
        # capture order before
        bres = await sess.exec(select(Category).order_by(Category.position.asc(), Category.id.asc()))
        before = [{'id': c.id, 'name': c.name, 'position': c.position} for c in bres.all()]
        q = await sess.exec(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur:
            raise HTTPException(status_code=404, detail='category not found')
        if direction == 'up':
            qprev = await sess.exec(select(Category).where(Category.position < cur.position).order_by(Category.position.desc()).limit(1))
            prev = qprev.first()
            if prev:
                cur_pos = cur.position
                prev_pos = prev.position
                logger.info('api_move_category: swapping up cat_id=%s cur_pos=%s prev_id=%s prev_pos=%s', cur.id, cur_pos, prev.id, prev_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == prev.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=prev_pos))
                logger.info('api_move_category: swap executed for cat_id=%s', cur.id)
        else:
            qnext = await sess.exec(select(Category).where(Category.position > cur.position).order_by(Category.position.asc()).limit(1))
            nxt = qnext.first()
            if nxt:
                cur_pos = cur.position
                next_pos = nxt.position
                logger.info('api_move_category: swapping down cat_id=%s cur_pos=%s next_id=%s next_pos=%s', cur.id, cur_pos, nxt.id, next_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == nxt.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=next_pos))
                logger.info('api_move_category: swap executed for cat_id=%s', cur.id)
        await sess.commit()
        # Normalize positions to avoid duplicates or gaps, then return list
        cats2 = await _normalize_category_positions(sess)
        after = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cats2]
        try:
            logger.info('api_move_category: before=%s after=%s',
                        [(x['id'], x['position']) for x in before],
                        [(x['id'], x['position']) for x in after])
        except Exception:
            pass
        return {'categories': after, 'before': before, 'after': after}


@app.post('/html_no_js/categories/create')
async def create_category(request: Request, name: str = Form(...)):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        # determine max position and append
        qmax = await sess.exec(select(Category).order_by(Category.position.desc()).limit(1))
        maxc = qmax.first()
        pos = (maxc.position + 1) if maxc else 0
        nc = Category(name=name.strip()[:200], position=pos)
        sess.add(nc)
        await sess.commit()
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.post('/html_no_js/categories/{cat_id}/rename')
async def rename_category(request: Request, cat_id: int, name: str = Form(...)):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        await sess.exec(sqlalchemy_update(Category).where(Category.id == cat_id).values(name=name.strip()[:200]))
        await sess.commit()
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.post('/html_no_js/categories/{cat_id}/delete')
async def delete_category(request: Request, cat_id: int):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        # remove category association from lists, then delete
        await sess.exec(sqlalchemy_update(ListState).where(ListState.category_id == cat_id).values(category_id=None))
        await sess.exec(sqlalchemy_delete(Category).where(Category.id == cat_id))
        await sess.commit()
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.post('/html_no_js/categories/{cat_id}/move')
async def move_category(request: Request, cat_id: int, direction: str = Form(...)):
    # direction: 'up' or 'down'
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        q = await sess.exec(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur:
            return RedirectResponse(url='/html_no_js/categories', status_code=303)
        if direction == 'up':
            # find previous (lower position) item
            qprev = await sess.exec(select(Category).where(Category.position < cur.position).order_by(Category.position.desc()).limit(1))
            prev = qprev.first()
            if prev:
                cur_pos = cur.position
                prev_pos = prev.position
                logger.info('move_category: swapping up cat_id=%s cur_pos=%s prev_id=%s prev_pos=%s', cur.id, cur_pos, prev.id, prev_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == prev.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=prev_pos))
                logger.info('move_category: swap executed for cat_id=%s', cur.id)
        elif direction == 'down':
            qnext = await sess.exec(select(Category).where(Category.position > cur.position).order_by(Category.position.asc()).limit(1))
            nxt = qnext.first()
            if nxt:
                cur_pos = cur.position
                next_pos = nxt.position
                logger.info('move_category: swapping down cat_id=%s cur_pos=%s next_id=%s next_pos=%s', cur.id, cur_pos, nxt.id, next_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == nxt.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=next_pos))
                logger.info('move_category: swap executed for cat_id=%s', cur.id)
        await sess.commit()
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.get('/html_no_js/search', response_class=HTMLResponse)
async def html_search(request: Request):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        current_user = None
    if not current_user:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    qparam = request.query_params.get('q', '').strip()
    include_list_todos = str(request.query_params.get('include_list_todos', '')).lower() in ('1','true','yes','on')
    exclude_completed = str(request.query_params.get('exclude_completed', '')).lower() in ('1','true','yes','on')
    results = {'lists': [], 'todos': []}
    if qparam:
        # Search across names/text/notes AND hashtags extracted from the query.
        like = f"%{qparam}%"
        try:
            search_tags = extract_hashtags(qparam)
        except Exception:
            search_tags = []
        async with async_session() as sess:
            owner_id = current_user.id
            # search lists visible to user by name
            qlists = select(ListState).where(ListState.owner_id == owner_id).where(ListState.name.ilike(like))
            rlists = await sess.exec(qlists)
            lists_by_id: dict[int, ListState] = {l.id: l for l in rlists.all()}
            # add lists visible to user that match by hashtag
            if search_tags:
                qlh = (
                    select(ListState)
                    .join(ListHashtag, ListHashtag.list_id == ListState.id)
                    .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                    .where(ListState.owner_id == owner_id)
                    .where(Hashtag.tag.in_(search_tags))
                )
                rlh = await sess.exec(qlh)
                for l in rlh.all():
                    lists_by_id.setdefault(l.id, l)
            results['lists'] = [
                {'id': l.id, 'name': l.name, 'completed': getattr(l, 'completed', False)}
                for l in lists_by_id.values()
                if not (exclude_completed and getattr(l, 'completed', False))
            ]
            # search todos in visible lists
            qvis = select(ListState).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            rvis = await sess.exec(qvis)
            vis_ids = [l.id for l in rvis.all()]
            todos_acc: dict[int, Todo] = {}
            if vis_ids:
                # text/note match
                qtodos = select(Todo).where(Todo.list_id.in_(vis_ids)).where((Todo.text.ilike(like)) | (Todo.note.ilike(like)))
                for t in (await sess.exec(qtodos)).all():
                    todos_acc.setdefault(t.id, t)
                # hashtag match
                if search_tags:
                    qth = (
                        select(Todo)
                        .join(TodoHashtag, TodoHashtag.todo_id == Todo.id)
                        .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                        .where(Todo.list_id.in_(vis_ids))
                        .where(Hashtag.tag.in_(search_tags))
                    )
                    for t in (await sess.exec(qth)).all():
                        todos_acc.setdefault(t.id, t)
                # optionally include all todos from lists that matched in the list search
                if include_list_todos and lists_by_id:
                    list_ids_match = list(lists_by_id.keys())
                    qall = select(Todo).where(Todo.list_id.in_(list_ids_match))
                    for t in (await sess.exec(qall)).all():
                        todos_acc.setdefault(t.id, t)
                # include list name for display
                lm = {l.id: l.name for l in (await sess.exec(select(ListState).where(ListState.id.in_(vis_ids)))).all()}
                # Compute default completion status per todo for strike-out and optional exclusion
                todo_list_ids = list({t.list_id for t in todos_acc.values()})
                default_ct_ids: dict[int, int] = {}
                if todo_list_ids:
                    qct = select(CompletionType).where(CompletionType.list_id.in_(todo_list_ids)).where(CompletionType.name == 'default')
                    for ct in (await sess.exec(qct)).all():
                        default_ct_ids[int(ct.list_id)] = int(ct.id)
                todo_ids = list(todos_acc.keys())
                completed_ids: set[int] = set()
                if todo_ids and default_ct_ids:
                    qdone = select(TodoCompletion.todo_id, TodoCompletion.done, TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(list(default_ct_ids.values())))
                    for tid, done_val, ctid in (await sess.exec(qdone)).all():
                        if done_val:
                            completed_ids.add(int(tid))
                results['todos'] = [
                    {'id': t.id, 'text': t.text, 'note': t.note, 'list_id': t.list_id, 'list_name': lm.get(t.list_id), 'completed': (int(t.id) in completed_ids)}
                    for t in todos_acc.values() if not (exclude_completed and (int(t.id) in completed_ids))
                ]
    client_tz = await get_session_timezone(request)
    csrf_token = None
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    return TEMPLATES.TemplateResponse(request, 'search.html', {'request': request, 'q': qparam, 'results': results, 'client_tz': client_tz, 'csrf_token': csrf_token, 'include_list_todos': include_list_todos, 'exclude_completed': exclude_completed})


@app.get('/html_no_js/calendar', response_class=HTMLResponse)
async def html_calendar(request: Request, year: Optional[int] = None, month: Optional[int] = None, selected_day: Optional[int] = None, current_user: User = Depends(require_login)):
    """Render a simple month calendar view. Defaults to current month if not provided."""
    from calendar import monthrange, Calendar
    from datetime import datetime, timezone, timedelta

    now = now_utc()
    y = year or now.year
    m = month or now.month
    # compute start and end of month in UTC
    start_dt = datetime(y, m, 1, tzinfo=timezone.utc)
    last_day = monthrange(y, m)[1]
    end_dt = datetime(y, m, last_day, 23, 59, 59, tzinfo=timezone.utc)

    # Optionally include the first 7 days of next month if client cookie set
    try:
        include_next7 = False
        cookie_val = request.cookies.get('include_next7')
        if cookie_val and cookie_val == '1':
            include_next7 = True
        if include_next7:
            # extend end_dt by up to 7 days into next month
            from datetime import timedelta
            end_dt = end_dt + timedelta(days=7)
    except Exception:
        # non-fatal: continue with original end_dt
        pass

    # reuse calendar_occurrences logic by calling parse helpers directly
    # collect occurrences between start_dt and end_dt by reusing calendar_occurrences
    from .main import calendar_occurrences as _co  # type: ignore
    co_res = await _co(request=None, start=start_dt.isoformat(), end=end_dt.isoformat(), current_user=current_user)
    occurrences = co_res.get('occurrences', []) if isinstance(co_res, dict) else []

    # group occurrences by day number
    occ_by_day: dict[int, list[dict]] = {}
    for o in occurrences:
        try:
            dt = datetime.fromisoformat(o['occurrence_dt'])
            day = dt.day
            occ_by_day.setdefault(day, []).append(o)
        except Exception:
            continue

    # build calendar grid (weeks starting Sunday)
    cal = Calendar(firstweekday=6)
    weeks = []
    for week in cal.monthdayscalendar(y, m):
        row = []
        for d in week:
            row.append({'day': d})
        weeks.append(row)

    # Instead of a month grid, produce a chronological list of occurrences
    occ_list = []
    for o in occurrences:
        try:
            dt = datetime.fromisoformat(o['occurrence_dt'])
            occ_list.append((dt, o))
        except Exception:
            continue
    occ_list.sort(key=lambda x: x[0])
    occurrences_sorted = [o for _, o in occ_list]

    # provide simple prev/next month links for convenience
    prev_month = m - 1
    prev_year = y
    if prev_month < 1:
        prev_month = 12; prev_year -= 1
    next_month = m + 1
    next_year = y
    if next_month > 12:
        next_month = 1; next_year += 1

    # Clear template cache to ensure recent edits to templates are used.
    try:
        TEMPLATES.env.cache.clear()
    except Exception:
        pass
    return TEMPLATES.TemplateResponse('calendar.html', {'request': request, 'year': y, 'month': m, 'occurrences_sorted': occurrences_sorted, 'prev_year': prev_year, 'prev_month': prev_month, 'next_year': next_year, 'next_month': next_month})


@app.post("/html_no_js/lists/create")
async def html_create_list(request: Request, name: str = Form(...), current_user: User = Depends(require_login)):
    # require CSRF for authenticated users (now always authenticated)
    form = await request.form()
    token = form.get("_csrf")
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    # create_list now expects the Request as the first argument (so it can
    # read query params when tests/clients send name via params). Pass
    # the current request through when invoking it internally.
    await create_list(request, name=name, current_user=current_user)
    return RedirectResponse(url="/html_no_js/", status_code=303)


@app.post('/html_no_js/lists/{list_id}/edit')
async def html_edit_list(request: Request, list_id: int, name: str = Form(...), current_user: User = Depends(require_login)):
    # require CSRF and ownership
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # strip leading whitespace and remove inline hashtags from stored name
    original_name = name or ''
    name = remove_hashtags_from_text(original_name.lstrip())
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        lst.name = name
        lst.modified_at = now_utc()
        sess.add(lst)
        await sess.commit()
        # Extract hashtags from the submitted name and merge with existing list-level hashtags
        try:
            tags = extract_hashtags(original_name)
        except Exception:
            tags = []
        # order-preserving dedupe of new tags
        seen: list[str] = []
        for t in tags:
            if t and t not in seen:
                seen.append(t)
        # If no new tags were provided in the edited name, leave existing list tags untouched.
        if seen:
            # Preserve existing tags and add new ones: compute union and sync
            ql = select(Hashtag.tag).join(
                ListHashtag, ListHashtag.hashtag_id == Hashtag.id
            ).where(ListHashtag.list_id == list_id)
            lres = await sess.exec(ql)
            rows = lres.all()
            current_tags: list[str] = []
            for row in rows:
                # row may be a scalar string or a 1-tuple depending on driver/version
                if isinstance(row, (tuple, list)):
                    val = row[0]
                else:
                    val = row
                if isinstance(val, str):
                    current_tags.append(val)
            # order-preserving union: existing first, then new not already present
            union_tags = list(dict.fromkeys((current_tags or []) + seen))
            await _sync_list_hashtags(sess, list_id, union_tags)
    # respond with redirect for normal browsers or 200 for fetch
    accept = request.headers.get('accept','')
    if 'application/json' in accept.lower():
        return {'id': list_id, 'name': name}
    return RedirectResponse(url='/html_no_js/', status_code=303)



@app.get('/html_no_js/login', response_class=HTMLResponse)
async def html_login_get(request: Request):
    client_tz = await get_session_timezone(request)
    return TEMPLATES.TemplateResponse(request, 'login.html', {"request": request, "client_tz": client_tz})


@app.post('/html_no_js/login')
async def html_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    from .auth import authenticate_user, create_access_token, get_user_by_username, verify_password
    user = await get_user_by_username(username)
    ok = False
    if user:
        ok = await verify_password(password, user.password_hash)
    if not user or not ok:
        # re-render login with simple message (keeps no-js constraint simple)
        client_tz = await get_session_timezone(request)
        return TEMPLATES.TemplateResponse(request, 'login.html', {"request": request, "error": "Invalid credentials", "client_tz": client_tz})
    token = create_access_token({"sub": user.username})
    # create a server-side session token and set it in an HttpOnly cookie
    from .auth import create_session_for_user, create_csrf_token
    # pass client timezone (if present in cookie) into server session
    client_tz = request.cookies.get('tz')
    session_token = await create_session_for_user(user, session_timezone=client_tz)
    # Return a small HTML response with cookies set. Tests only require the
    # cookies to be present; rendering the full template isn't necessary here
    # and using a simple Response guarantees Set-Cookie headers are emitted.
    # Redirect back to the index like a real browser and set cookies on the
    # RedirectResponse so clients that follow redirects pick them up.
    # Render the index page (final 200 response) and set cookies on that
    # response. Some test clients (and certain browsers) only persist cookies
    # when they are present on the final response when following redirects.
    csrf = create_csrf_token(user.username)
    # load lists for this user for the index template
    async with async_session() as sess:
        res = await sess.exec(select(ListState).where(ListState.owner_id == user.id).order_by(ListState.created_at.desc()))
        lists = res.all()
    # Return a redirect to the index and set cookies on that response. Some
    # test clients (and certain browsers) only persist cookies when they are
    # present on the final response when following redirects; setting cookies
    # on the RedirectResponse ensures httpx with follow_redirects=True will
    # observe them in most environments. Use the module-level COOKIE_SECURE
    # flag so tests (HTTP) do not mark cookies as Secure while production
    # can enable Secure cookies via env var.
    from fastapi.responses import RedirectResponse
    # Redirect to the index page like a normal browser flow and set cookies
    # on the RedirectResponse. Browsers will follow the redirect and use the
    # cookies for subsequent requests. COOKIE_SECURE controls the Secure flag
    # so test/dev HTTP environments won't mark cookies as Secure.
    resp = RedirectResponse(url="/html_no_js/", status_code=303)
    resp.set_cookie('session_token', session_token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('access_token', token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE)
    return resp


@app.get('/html_pwa/login', response_class=HTMLResponse)
async def html_pwa_login_get(request: Request):
    """Serve the PWA-specific static login page (keeps PWA redirects local)."""
    client_tz = await get_session_timezone(request)
    # serve a small static PWA login page so the POST stays under /html_pwa/
    return FileResponse('html_pwa/login.html', media_type='text/html')


@app.post('/html_pwa/login')
async def html_pwa_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Authenticate and set cookies for PWA clients, then redirect to the PWA index.

    This mirrors the behavior of the html_no_js login flow so PWA clients
    will get the same session and access cookies.
    """
    from .auth import authenticate_user, create_access_token, get_user_by_username, verify_password
    user = await get_user_by_username(username)
    ok = False
    if user:
        ok = await verify_password(password, user.password_hash)
    if not user or not ok:
        client_tz = await get_session_timezone(request)
        return TEMPLATES.TemplateResponse(request, 'login.html', {"request": request, "error": "Invalid credentials", "client_tz": client_tz})
    token = create_access_token({"sub": user.username})
    # create a server-side session token and set it in an HttpOnly cookie
    from .auth import create_session_for_user, create_csrf_token
    client_tz = request.cookies.get('tz')
    session_token = await create_session_for_user(user, session_timezone=client_tz)
    csrf = create_csrf_token(user.username)
    # Redirect to the PWA index and set cookies on the response
    resp = RedirectResponse(url="/html_pwa/", status_code=303)
    resp.set_cookie('session_token', session_token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('access_token', token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE)
    return resp



@app.post('/html_no_js/lists/{list_id}/icons')
async def html_set_list_icons(request: Request, list_id: int, hide_icons: str = Form(None), current_user: User = Depends(require_login)):
    # require CSRF and ownership
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        if hide_icons is not None:
            val = hide_icons.lower() in ('1', 'true', 'yes', 'on')
            lst.hide_icons = val
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/lists/{list_id}/category')
async def html_set_list_category(request: Request, list_id: int, category_id: Optional[int] = Form(None), current_user: User = Depends(require_login)):
    """Assign or clear a list's category.
    Pass category_id as a form field. Empty string or -1 clears the category.
    """
    # CSRF and ownership
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Normalize category_id
    raw = form.get('category_id')
    cid: Optional[int]
    if raw is None or str(raw).strip() == '' or str(raw).strip() == '-1':
        cid = None
    else:
        try:
            cid = int(str(raw))
        except Exception:
            cid = None
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # If setting to a real category, ensure it exists
        if cid is not None:
            cat = await sess.get(Category, cid)
            if not cat:
                raise HTTPException(status_code=400, detail='category not found')
        lst.category_id = cid
        lst.modified_at = now_utc()
        sess.add(lst)
        await sess.commit()
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


class SyncOp(BaseModel):
    op: str
    payload: dict


class SyncRequest(BaseModel):
    ops: List[SyncOp]


@app.get('/sync')
async def sync_get(since: Optional[str] = None, current_user: User = Depends(require_login)):
    """Return lists and todos modified since the optional ISO8601 timestamp.
    The result includes lists the user owns or public lists (owner_id is None).
    """
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except Exception:
            raise HTTPException(status_code=400, detail='invalid since timestamp')
    async with async_session() as sess:
        # lists visible to the user (owned or public)
        ql = select(ListState).where((ListState.owner_id == current_user.id) | (ListState.owner_id == None))
        if since_dt:
            ql = ql.where(ListState.modified_at != None).where(ListState.modified_at > since_dt)
        resl = await sess.exec(ql)
        lists = [ _serialize_list(l) for l in resl.all() ]

        # todos in those lists
        list_ids = [l['id'] for l in lists]
        qt = select(Todo)
        if list_ids:
            qt = qt.where(Todo.list_id.in_(list_ids))
        else:
            # if no lists changed, still allow checking todos modified since
            if since_dt:
                qt = qt.where(Todo.modified_at != None).where(Todo.modified_at > since_dt)
            else:
                qt = qt.where(False)
        rest = await sess.exec(qt)
    todos = [ _serialize_todo(t) for t in rest.all() ]
    # tombstones since the requested time so clients can remove deleted items
    tombstones = []
    if since_dt:
        qtomb = select(Tombstone).where(Tombstone.created_at != None).where(Tombstone.created_at > since_dt)
        tres = await sess.exec(qtomb)
        tombstones = [{'item_type': t.item_type, 'item_id': t.item_id, 'created_at': t.created_at.isoformat() if t.created_at else None} for t in tres.all()]
    # also return a server timestamp so clients can safely mark the sync boundary
    return {"lists": lists, "todos": todos, "tombstones": tombstones, "server_ts": now_utc().isoformat()}


@app.post('/sync')
async def sync_post(req: SyncRequest, current_user: User = Depends(require_login)):
    """Accept a batch of simple operations from a PWA client. This is a
    minimal server-side handler: it performs create/update/delete for
    todos and lists and returns per-op results. The client should ensure
    idempotency (this handler does not persist idempotency keys).
    """
    results: List[Dict[str, Any]] = []
    async with async_session() as sess:
        for op in req.ops:
            name = op.op
            payload = op.payload or {}
            # idempotency key provided by client to dedupe retries
            op_id = payload.get('op_id')
            if op_id:
                qop = await sess.exec(select(SyncOperation).where(SyncOperation.op_id == op_id).where(SyncOperation.user_id == current_user.id))
                existing = qop.first()
                if existing:
                    # return previously-recorded result for this op
                    try:
                        prev = json.loads(existing.result_json) if existing.result_json else {'op': name, 'status': 'ok', 'id': existing.server_id}
                    except Exception:
                        prev = {'op': name, 'status': 'ok', 'id': existing.server_id}
                    results.append(prev)
                    continue
            try:
                if name == 'create_list':
                    client_id = payload.get('client_id')
                    lst = ListState(name=payload.get('name'), owner_id=current_user.id)
                    sess.add(lst)
                    await sess.commit()
                    await sess.refresh(lst)
                    out = {'op': name, 'status': 'ok', 'id': lst.id}
                    if client_id is not None:
                        out['client_id'] = client_id
                    results.append(out)
                    # persist idempotency record if requested
                    if op_id:
                        so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=client_id, server_id=lst.id, result_json=json.dumps(out))
                        sess.add(so)
                        await sess.commit()

                elif name == 'delete_list':
                    lid = payload.get('id')
                    q = await sess.exec(select(ListState).where(ListState.id == lid))
                    lst = q.first()
                    if not lst:
                        results.append({'op': name, 'status': 'not_found', 'id': lid})
                        continue
                    if lst.owner_id != current_user.id:
                        results.append({'op': name, 'status': 'forbidden', 'id': lid})
                        continue
                    # capture todos that belong to this list
                    qtodos = await sess.exec(select(Todo.id).where(Todo.list_id == lid))
                    todo_ids = [t for t in qtodos.all()]
                    # remove list-level artifacts
                    await sess.exec(sqlalchemy_delete(CompletionType).where(CompletionType.list_id == lid))
                    await sess.exec(sqlalchemy_delete(ListHashtag).where(ListHashtag.list_id == lid))
                    # delete the list row
                    await sess.exec(sqlalchemy_delete(ListState).where(ListState.id == lid))
                    await sess.commit()
                    # adjust server default if needed
                    qs = await sess.exec(select(ServerState))
                    ss = qs.first()
                    if ss and ss.default_list_id == lid:
                        qpick = await sess.exec(select(ListState).order_by(ListState.modified_at.desc(), ListState.created_at.desc()))
                        pick = qpick.first()
                        if pick:
                            old = ss.default_list_id
                            ss.default_list_id = pick.id
                            logger.info("server default list changed from %s to %s after deletion", old, pick.id)
                        else:
                            ss.default_list_id = None
                            logger.info("server default list cleared (no lists remain) after deletion of %s", lid)
                        sess.add(ss)
                        await sess.commit()
                    # record tombstones and delete todos and link rows
                    if todo_ids:
                        for tid in todo_ids:
                            ts = Tombstone(item_type='todo', item_id=tid)
                            sess.add(ts)
                        await sess.commit()
                        await sess.exec(sqlalchemy_delete(TodoCompletion).where(TodoCompletion.todo_id.in_(todo_ids)))
                        await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id.in_(todo_ids)))
                        await sess.exec(sqlalchemy_delete(Todo).where(Todo.id.in_(todo_ids)))
                        await sess.commit()
                    out = {'op': name, 'status': 'ok', 'id': lid}
                    results.append(out)
                    if op_id:
                        try:
                            so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, server_id=lid, result_json=json.dumps(out))
                            sess.add(so)
                            await sess.commit()
                        except IntegrityError:
                            await sess.rollback()

                elif name == 'create_todo':
                    text = payload.get('text')
                    note = payload.get('note')
                    list_id = payload.get('list_id')
                    if list_id is None:
                        results.append({'op': name, 'status': 'bad_request', 'reason': 'list_id required'})
                    else:
                        ql = await sess.exec(select(ListState).where(ListState.id == list_id))
                        lst = ql.first()
                        if not lst:
                            results.append({'op': name, 'status': 'list_not_found', 'list_id': list_id})
                        elif lst.owner_id not in (None, current_user.id):
                            results.append({'op': name, 'status': 'forbidden', 'list_id': list_id})
                        else:
                            client_id = payload.get('client_id')
                            todo = Todo(text=text, note=note, list_id=list_id)
                            sess.add(todo)
                            await sess.commit()
                            await sess.refresh(todo)
                            # touch parent list modified_at
                            try:
                                await _touch_list_modified(sess, int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None)
                                await sess.commit()
                            except Exception:
                                await sess.rollback()
                            out = {'op': name, 'status': 'ok', 'id': todo.id}
                            if client_id is not None:
                                out['client_id'] = client_id
                            results.append(out)
                            if op_id:
                                so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, client_id=client_id, server_id=todo.id, result_json=json.dumps(out))
                                sess.add(so)
                                await sess.commit()

                elif name == 'update_todo':
                    tid = payload.get('id')
                    q = await sess.exec(select(Todo).where(Todo.id == tid))
                    todo = q.first()
                    if not todo:
                        results.append({'op': name, 'status': 'not_found', 'id': tid})
                    else:
                        # check ownership via list
                        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
                        lst = ql.first()
                        if lst and lst.owner_id not in (None, current_user.id):
                            results.append({'op': name, 'status': 'forbidden', 'id': tid})
                        else:
                            old_list_id = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None
                            # apply provided fields
                            if 'text' in payload:
                                    todo.text = payload.get('text')
                            if 'note' in payload:
                                    todo.note = payload.get('note')
                            if 'list_id' in payload:
                                new_list_id = payload.get('list_id')
                                # validate target list and ownership rules
                                ql2 = await sess.exec(select(ListState).where(ListState.id == new_list_id))
                                new_lst = ql2.first()
                                if not new_lst:
                                    results.append({'op': name, 'status': 'list_not_found', 'list_id': new_list_id})
                                    continue
                                if new_lst.owner_id not in (None, current_user.id):
                                    results.append({'op': name, 'status': 'forbidden', 'list_id': new_list_id})
                                    continue
                                todo.list_id = new_list_id
                            todo.modified_at = now_utc()
                            sess.add(todo)
                            await sess.commit()
                            await sess.refresh(todo)
                            # touch parent list modified_at (and old list if moved)
                            try:
                                new_list_id_int = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None
                                await _touch_list_modified(sess, new_list_id_int)
                                if old_list_id is not None and old_list_id != new_list_id_int:
                                    await _touch_list_modified(sess, old_list_id)
                                await sess.commit()
                            except Exception:
                                await sess.rollback()
                            out = {'op': name, 'status': 'ok', 'id': todo.id}
                            results.append(out)
                            if op_id:
                                try:
                                    so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, server_id=todo.id, result_json=json.dumps(out))
                                    sess.add(so)
                                    await sess.commit()
                                except IntegrityError:
                                    await sess.rollback()

                elif name == 'delete_todo':
                    tid = payload.get('id')
                    q = await sess.exec(select(Todo).where(Todo.id == tid))
                    todo = q.first()
                    if not todo:
                        results.append({'op': name, 'status': 'not_found', 'id': tid})
                    else:
                        # check ownership via list
                        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
                        lst = ql.first()
                        if lst and lst.owner_id not in (None, current_user.id):
                            results.append({'op': name, 'status': 'forbidden', 'id': tid})
                        else:
                            parent_list_id = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None
                            # delete dependent rows first, then the todo
                            await sess.exec(sqlalchemy_delete(TodoCompletion).where(TodoCompletion.todo_id == tid))
                            await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id == tid))
                            await sess.exec(sqlalchemy_delete(Todo).where(Todo.id == tid))
                            await sess.commit()
                            # touch parent list modified_at
                            try:
                                await _touch_list_modified(sess, parent_list_id)
                                await sess.commit()
                            except Exception:
                                await sess.rollback()
                            out = {'op': name, 'status': 'ok', 'id': tid}
                            results.append(out)
                            if op_id:
                                try:
                                    so = SyncOperation(user_id=current_user.id, op_id=op_id, op_name=name, server_id=tid, result_json=json.dumps(out))
                                    sess.add(so)
                                    await sess.commit()
                                except IntegrityError:
                                    await sess.rollback()

                else:
                    results.append({'op': name, 'status': 'unsupported'})
            except Exception:
                logger.exception('error processing sync op %s', op.op)
                results.append({'op': name, 'status': 'error'})
    return {'results': results}


@app.get('/__debug_setcookie')
def __debug_setcookie():
    from fastapi.responses import Response
    r = Response(content='r', media_type='text/plain')
    r.set_cookie('session_token', 'abc', httponly=True, samesite='lax')
    return r
 


@app.post('/html_no_js/logout')
async def html_logout(request: Request):
    # attempt to remove server-side session if present
    session_token = request.cookies.get('session_token')
    if session_token:
        from .auth import delete_session
        await delete_session(session_token)
    client_tz = await get_session_timezone(request)
    resp = TEMPLATES.TemplateResponse(request, 'logout.html', {"request": request, "client_tz": client_tz})
    # delete cookies with the same attributes used when setting them so
    # browsers will reliably remove them.
    resp.delete_cookie('session_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('access_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('csrf_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    return resp


@app.get("/html_no_js/lists/{list_id}", response_class=HTMLResponse)
async def html_view_list(request: Request, list_id: int, current_user: User = Depends(require_login)):
    # require login and ownership for HTML list view
    async with async_session() as sess:
        q = await sess.exec(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # fetch completion types for this list
        qct = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).order_by(CompletionType.name.asc()))
        ctypes = qct.all()

        # load todos and completion states in batch
        q2 = await sess.exec(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
        todos = q2.all()
        todo_ids = [t.id for t in todos]
        ctype_ids = [c.id for c in ctypes]
        status_map: dict[tuple[int, int], bool] = {}
        if todo_ids and ctype_ids:
            qtc = select(TodoCompletion.todo_id, TodoCompletion.completion_type_id, TodoCompletion.done).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(ctype_ids))
            r = await sess.exec(qtc)
            for tid, cid, done_val in r.all():
                status_map[(tid, cid)] = bool(done_val)

        # find default completion type id if present
        default_ct = next((c for c in ctypes if c.name == 'default'), None)
        default_id = default_ct.id if default_ct else None

        todo_rows = []
        for t in todos:
            # default completion state for the main checkbox
            completed_default = False
            if default_id is not None:
                completed_default = status_map.get((t.id, default_id), False)
            # extra completion types (exclude 'default') with per-type status
            extra = []
            for c in ctypes:
                if c.name == 'default':
                    continue
                extra.append({'id': c.id, 'name': c.name, 'done': status_map.get((t.id, c.id), False)})

            todo_rows.append({
                "id": t.id,
                "text": t.text,
                "note": t.note,
                "created_at": t.created_at,
                "modified_at": t.modified_at,
                "completed": completed_default,
                "pinned": getattr(t, 'pinned', False),
                "extra_completions": extra,
            })

        # fetch hashtags for all todos in this list
        todo_ids = [r['id'] for r in todo_rows]

        
        tags_map = {}
        if todo_ids:
            qth = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_ids))
            tres = await sess.exec(qth)
            for tid, tag in tres.all():
                tags_map.setdefault(tid, []).append(tag)
        for r in todo_rows:
            r['tags'] = tags_map.get(r['id'], [])

        # fetch list-level hashtags while session is open so templates don't lazy-load
        ql = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == list_id)
        lres = await sess.exec(ql)
        _rows = lres.all()
        list_tags: list[str] = []
        for row in _rows:
            # row may be a scalar (str) or a 1-tuple depending on driver/version
            if isinstance(row, (tuple, list)):
                val = row[0]
            else:
                val = row
            if isinstance(val, str) and val:
                list_tags.append(val)

        # build a plain dict for the list to avoid DetachedInstanceError in templates
        list_row = {
            "id": lst.id,
            "name": lst.name,
            "completed": lst.completed,
            "hashtags": list_tags,
            # persist UI preference so templates can render checkbox state
            "hide_icons": getattr(lst, 'hide_icons', False),
            "category_id": getattr(lst, 'category_id', None),
        }
        # also pass completion types for management UI
        completion_types = [{'id': c.id, 'name': c.name} for c in ctypes]
        # fetch this user's hashtags for completion suggestions (from lists and todos they own)
        owner_id_val = current_user.id
        # tags from lists owned by the user
        q_user_list_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id)
            .join(ListState, ListState.id == ListHashtag.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_list_tags = await sess.exec(q_user_list_tags)
        # tags from todos in lists owned by the user
        q_user_todo_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
            .join(Todo, Todo.id == TodoHashtag.todo_id)
            .join(ListState, ListState.id == Todo.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_todo_tags = await sess.exec(q_user_todo_tags)
        _all_rows = list(r_user_list_tags.all()) + list(r_user_todo_tags.all())
        all_hashtags: list[str] = []
        for row in _all_rows:
            val = row[0] if isinstance(row, (tuple, list)) else row
            if isinstance(val, str) and val and val not in all_hashtags:
                all_hashtags.append(val)
        # fetch categories for assignment UI (ordered by position)
        try:
            qcat = select(Category).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cres.all()]
        except Exception:
            categories = []
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    # Best-effort: record that the current user visited this list so the
    # "recent lists" page reflects views made from the HTML no-JS UI.
    # Use the existing record_list_visit helper; swallow any errors so
    # rendering the list page never fails because of visit recording problems.
    try:
        # call the route function directly with the resolved current_user
        await record_list_visit(list_id=list_id, current_user=current_user)
    except Exception:
        logger.exception('failed to record list visit for list %s', list_id)
    client_tz = await get_session_timezone(request)
    return TEMPLATES.TemplateResponse(request, "list.html", {"request": request, "list": list_row, "todos": todo_rows, "csrf_token": csrf_token, "client_tz": client_tz, "completion_types": completion_types, "all_hashtags": all_hashtags, "categories": categories})


@app.post('/html_no_js/lists/{list_id}/complete')
async def html_toggle_list_complete(request: Request, list_id: int, completed: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    completed_bool = str(completed).lower() in ('1', 'true', 'yes')
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        lst.completed = completed_bool
        lst.modified_at = now_utc()
        sess.add(lst)
        await sess.commit()
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)



@app.get('/html_no_js/recent', response_class=HTMLResponse)
async def html_recent_lists(request: Request, current_user: User = Depends(require_login)):
    """Render a simple page listing recently visited lists for the current user."""
    try:
        top_n = int(os.getenv('RECENT_LISTS_TOP_N', '10'))
    except Exception:
        top_n = 10
    async with async_session() as sess:
        # First fetch top-N positioned rows ordered by position ASC
        top_q = select(RecentListVisit).where(RecentListVisit.user_id == current_user.id).where(RecentListVisit.position != None).order_by(RecentListVisit.position.asc()).limit(top_n)
        top_res = await sess.exec(top_q)
        top_rows = top_res.all()
        top_ids = [r.list_id for r in top_rows]

        results: list[dict] = []
        list_ids: list[int] = []
        tags_map: dict[int, list[str]] = {}

        # load ListState for top rows preserving order
        if top_ids:
            qlists = select(ListState).where(ListState.id.in_(top_ids))
            lres = await sess.exec(qlists)
            lmap = {l.id: l for l in lres.all()}
            for r in top_rows:
                lst = lmap.get(r.list_id)
                if lst:
                    results.append({
                        'id': lst.id,
                        'name': lst.name,
                        'completed': getattr(lst, 'completed', False),
                        'created_at': getattr(lst, 'created_at', None),
                        'modified_at': getattr(lst, 'modified_at', None),
                        'visited_at': r.visited_at,
                        'position': r.position,
                        'hashtags': [],
                    })
                    list_ids.append(lst.id)

        # If we still need more, fetch others ordered by visited_at desc excluding top_ids
        remaining = max(0, 25 - len(results))
        if remaining > 0:
            q = select(ListState, RecentListVisit.visited_at).join(RecentListVisit, RecentListVisit.list_id == ListState.id).where(RecentListVisit.user_id == current_user.id)
            if top_ids:
                q = q.where(RecentListVisit.list_id.notin_(top_ids))
            q = q.order_by(RecentListVisit.visited_at.desc()).limit(remaining)
            res = await sess.exec(q)
            other_rows = res.all()
            for lst, visited_at in other_rows:
                results.append({
                    'id': lst.id,
                    'name': lst.name,
                    'completed': getattr(lst, 'completed', False),
                    'created_at': getattr(lst, 'created_at', None),
                    'modified_at': getattr(lst, 'modified_at', None),
                    'visited_at': visited_at,
                    'position': None,
                    'hashtags': [],
                })
                list_ids.append(lst.id)

        # fetch hashtags for all list_ids we've collected
        if list_ids:
            try:
                qtags = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(list_ids))
                tres = await sess.exec(qtags)
                for lid, tag in tres.all():
                    tags_map.setdefault(lid, []).append(tag)
            except Exception:
                logger.exception('failed to fetch list hashtags for recent lists')

        # attach hashtags to results
        for item in results:
            item['hashtags'] = tags_map.get(item['id'], [])
        recent = results
    return TEMPLATES.TemplateResponse(request, 'recent.html', {"request": request, "recent": recent, "client_tz": await get_session_timezone(request)})


@app.post('/html_no_js/lists/{list_id}/completion_types')
async def html_add_completion_type(request: Request, list_id: int, name: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF and ownership checks
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Disallow creating another "default"
    if name.strip().lower() == 'default':
        return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)
    await create_completion_type_endpoint(list_id=list_id, name=name.strip(), current_user=current_user)
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


@app.post('/html_no_js/lists/{list_id}/completion_types/remove')
async def html_remove_completion_type(request: Request, list_id: int, name: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF and ownership checks
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Prevent removing default via the UI; API already guards it too
    if name.strip().lower() == 'default':
        return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)
    await delete_completion_type_endpoint(list_id=list_id, name=name.strip(), current_user=current_user)
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


@app.post('/html_no_js/todos/{todo_id}/complete_type')
async def html_toggle_todo_completion_type(request: Request, todo_id: int, completion_type_id: int = Form(...), done: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Find the todo and its list for redirect and ownership
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # Validate completion type belongs to this list
        qct = await sess.exec(select(CompletionType).where(CompletionType.id == completion_type_id).where(CompletionType.list_id == lst.id))
        ctype = qct.first()
        if not ctype:
            raise HTTPException(status_code=404, detail='completion type not found')
        list_id_val = int(lst.id)
        ctype_name = ctype.name
    # Toggle via API-level logic
    val = True if str(done).lower() in ('1','true','yes') else False
    await complete_todo(todo_id=todo_id, completion_type=ctype_name, done=val)
    anchor = form.get('anchor') or f'todo-{todo_id}'
    return RedirectResponse(url=f'/html_no_js/lists/{list_id_val}#{anchor}', status_code=303)

@app.post('/lists/{list_id}/complete')
async def api_toggle_list_complete(list_id: int, completed: bool = Form(...), current_user: User = Depends(require_login)):
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        lst.completed = bool(completed)
        lst.modified_at = now_utc()
        sess.add(lst)
        await sess.commit()
        await sess.refresh(lst)
    return {'id': lst.id, 'completed': lst.completed}


@app.post("/html_no_js/todos/create")
async def html_create_todo(request: Request, text: str = Form(...), list_id: int = Form(...), current_user: User = Depends(require_login)):
    # require CSRF for authenticated users (now always authenticated)
    form = await request.form()
    token = form.get("_csrf")
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    await create_todo(text=text, list_id=list_id, current_user=current_user)
    return RedirectResponse(url=f"/html_no_js/lists/{list_id}", status_code=303)


@app.post("/html_no_js/todos/{todo_id}/complete")
async def html_toggle_complete(request: Request, todo_id: int, done: str = Form(...), current_user: User = Depends(require_login)):
    # convert string form value to bool
    val = True if done.lower() in ("1", "true", "yes") else False
    # find the todo's list so we can redirect back to it after marking
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
    # require login for completing todos; complete_todo does not check auth
    await complete_todo(todo_id=todo_id, done=val)
    # if the form included an anchor field, use it as a fragment
    form = await request.form()
    anchor = form.get('anchor')
    if todo and todo.list_id:
        url = f"/html_no_js/lists/{todo.list_id}"
        if anchor:
            url = f"{url}#{anchor}"
        return RedirectResponse(url=url, status_code=303)
    return RedirectResponse(url="/html_no_js/", status_code=303)


@app.get("/html_no_js/todos/{todo_id}/complete")
async def html_toggle_complete_get(request: Request, todo_id: int, done: str):
    # Accept 'done' as query param string and perform the same toggle as the POST handler.
    val = True if str(done).lower() in ("1", "true", "yes") else False
    async with async_session() as sess:
        q = await sess.exec(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
    await complete_todo(todo_id=todo_id, done=val)
    # optional anchor param to include as fragment when redirecting
    anchor = request.query_params.get('anchor')
    if todo and todo.list_id:
        url = f"/html_no_js/lists/{todo.list_id}"
        if anchor:
            url = f"{url}#{anchor}"
        return RedirectResponse(url=url, status_code=303)
    return RedirectResponse(url="/html_no_js/", status_code=303)


@app.post("/html_no_js/todos/{todo_id}/delete")
async def html_delete_todo(request: Request, todo_id: int):
    # require CSRF for authenticated users
    from .auth import get_current_user as _gcu
    # call dependency function directly: pass token=None to avoid the Depends() default object
    cu = await _gcu(token=None, request=request)
    # Read the submitted form early so we can use list_id/anchor for redirects.
    try:
        form = await request.form()
    except Exception:
        form = {}

    # require csrf if user is authenticated
    if cu:
        token = form.get("_csrf")
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, cu.username):
            raise HTTPException(status_code=403, detail="invalid csrf token")

    anchor = form.get('anchor')
    list_id = form.get('list_id')
    # If the form didn't include list_id, try to fetch it now so we can redirect
    if not list_id:
        try:
            async with async_session() as sess:
                todo_row = await sess.get(Todo, todo_id)
                if todo_row:
                    list_id = str(todo_row.list_id) if todo_row.list_id is not None else None
        except Exception:
            list_id = None

    # attempt deletion, but if the todo is already missing return to the list view
    try:
        await delete_todo(todo_id=todo_id, current_user=cu)
    except HTTPException as e:
        if e.status_code == 404:
            # prefer explicit list_id from the form when available
            if list_id:
                return RedirectResponse(url=f"/html_no_js/lists/{list_id}", status_code=303)
            ref = request.headers.get('Referer', '/html_no_js/')
            return RedirectResponse(url=ref, status_code=303)
        raise

    # After successful deletion build a sensible redirect:
    # Normalize Referer (may be absolute URL) and prefer redirecting to the
    # owning list when possible to avoid sending the client back to a now-
    # deleted todo page which would render a JSON 404.
    from urllib.parse import urlparse
    ref = request.headers.get("Referer", "/html_no_js/")
    parsed = urlparse(ref) if ref else None
    ref_path = parsed.path if parsed else '/html_no_js/'

    # If we know the list_id prefer redirecting to that list. If the user
    # came from a lists page and an anchor was supplied, include it.
    if list_id:
        list_url = f"/html_no_js/lists/{list_id}"
        if anchor and ref_path.startswith('/html_no_js/lists'):
            list_url = f"{list_url}#{anchor}"
        return RedirectResponse(url=list_url, status_code=303)

    # If no list_id is available, preserve the referer but if it points to a
    # lists page and an anchor was supplied, include the fragment.
    if anchor and ref_path.startswith('/html_no_js/lists'):
        ref_nohash = ref.split('#')[0]
        return RedirectResponse(url=f"{ref_nohash}#{anchor}", status_code=303)

    return RedirectResponse(url=ref, status_code=303)



@app.get('/html_no_js/todos/{todo_id}', response_class=HTMLResponse)
async def html_view_todo(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # check ownership via list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        # require login: only owners or public lists allowed
        if lst and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        qc = select(TodoCompletion).where(TodoCompletion.todo_id == todo_id)
        cres = await sess.exec(qc)
        completed = any(c.done for c in cres.all())
        # fetch hashtags for this todo while session is open
        qh = select(Hashtag.tag).join(TodoHashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id == todo_id)
        hres = await sess.exec(qh)
        todo_tags = [r for r in hres.all()]

        # fetch this user's hashtags for completion suggestions (from lists and todos they own)
        owner_id_val = current_user.id
        q_user_list_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id)
            .join(ListState, ListState.id == ListHashtag.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_list_tags = await sess.exec(q_user_list_tags)
        q_user_todo_tags = (
            select(Hashtag.tag)
            .distinct()
            .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
            .join(Todo, Todo.id == TodoHashtag.todo_id)
            .join(ListState, ListState.id == Todo.list_id)
            .where(ListState.owner_id == owner_id_val)
        )
        r_user_todo_tags = await sess.exec(q_user_todo_tags)
        _all_rows = list(r_user_list_tags.all()) + list(r_user_todo_tags.all())
        all_hashtags: list[str] = []
        for row in _all_rows:
            val = row[0] if isinstance(row, (tuple, list)) else row
            if isinstance(val, str) and val and val not in all_hashtags:
                all_hashtags.append(val)

        # Build plain dicts to avoid lazy-loading on detached ORM objects during template rendering
        todo_row = {
            "id": todo.id,
            "text": todo.text,
            "note": todo.note,
            "created_at": todo.created_at,
            "modified_at": todo.modified_at,
            "list_id": todo.list_id,
            "pinned": getattr(todo, 'pinned', False),
        }
        list_row = None
        if lst:
            list_row = {"id": lst.id, "name": lst.name, "completed": lst.completed}
    csrf_token = None
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    client_tz = await get_session_timezone(request)
    # pass plain dicts (with datetime objects preserved) to avoid lazy DB loads
    return TEMPLATES.TemplateResponse(request, 'todo.html', {"request": request, "todo": todo_row, "completed": completed, "list": list_row, "csrf_token": csrf_token, "client_tz": client_tz, "tags": todo_tags, "all_hashtags": all_hashtags})


@app.post('/html_no_js/todos/{todo_id}/edit')
async def html_edit_todo(request: Request, todo_id: int, text: str = Form(...), note: str = Form(None), current_user: User = Depends(require_login)):
    # require CSRF for authenticated users (always logged in here)
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # perform update and return either a redirect (normal browsers) or JSON (AJAX/fetch)
    result = await update_todo(todo_id=todo_id, text=text, note=note, current_user=current_user)
    accept = request.headers.get('accept', '')
    if 'application/json' in accept.lower():
        # return JSON result for AJAX autosave clients
        return result
    return RedirectResponse(url=f"/html_no_js/todos/{todo_id}", status_code=303)


@app.post('/html_no_js/todos/{todo_id}/hashtags/remove')
async def html_remove_todo_hashtag(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    # CSRF + ownership checks for HTML flow
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    tag = form.get('tag')
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    # reuse internal core logic with the authenticated current_user so we
    # don't invoke FastAPI dependency resolution by calling the route func.
    async with async_session() as sess:
        await _remove_todo_hashtag_core(sess, todo_id, tag, current_user)
    # redirect back to referer when possible
    ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/todos/{todo_id}/hashtags')
async def html_add_todo_hashtag(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    # CSRF + ownership checks for HTML flow
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    tag = form.get('tag')
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    async with async_session() as sess:
        # ensure todo exists and user owns the parent list
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # normalize/validate tag
        try:
            ntag = normalize_hashtag(tag)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # ensure Hashtag row exists
        qh = await sess.exec(select(Hashtag).where(Hashtag.tag == ntag))
        h = qh.first()
        if not h:
            h = Hashtag(tag=ntag)
            sess.add(h)
            try:
                await sess.flush()
            except IntegrityError:
                await sess.rollback()
                qh2 = await sess.exec(select(Hashtag).where(Hashtag.tag == ntag))
                h = qh2.first()
        # link if not already linked
        qlink = await sess.exec(select(TodoHashtag).where(TodoHashtag.todo_id == todo_id).where(TodoHashtag.hashtag_id == h.id))
        if not qlink.first():
            sess.add(TodoHashtag(todo_id=todo_id, hashtag_id=h.id))
            try:
                await sess.commit()
            except IntegrityError:
                await sess.rollback()
        # redirect back
    ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
    return RedirectResponse(url=ref, status_code=303)
