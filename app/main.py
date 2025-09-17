from .models import TrashMeta, ListTrashMeta
from fastapi import FastAPI, HTTPException, Depends
from sqlmodel import select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import and_, or_
from .db import async_session, init_db
from .models import ListState, Todo, CompletionType, TodoCompletion, User
from .auth import get_current_user, create_access_token, require_login, CSRF_TOKEN_EXPIRE_MINUTES, CSRF_TOKEN_EXPIRE_SECONDS
from pydantic import BaseModel
from .utils import now_utc, normalize_hashtag
from .utils import validate_metadata_for_storage, parse_metadata_json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
import json
import asyncio
import os
from contextvars import ContextVar
from .models import Hashtag, TodoHashtag, ListHashtag, ServerState, Tombstone, Category
from .models import ItemLink
from .models import UserCollation
from .models import RecentListVisit, RecentTodoVisit
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text, func
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from .utils import format_server_local, format_in_timezone
from .utils import extract_hashtags
from .utils import extract_dates
from .utils import extract_dates_meta
from .utils import remove_hashtags_from_text
from .utils import parse_text_to_rrule, recurrence_dict_to_rrule_params, parse_text_to_rrule_string
from .models import Session
import logging
from . import config
from .repl_api import run_code_for_user

import sys
from asyncio import Queue

logger = logging.getLogger(__name__)
# Ensure INFO-level messages from this module appear on the server console when
# no handlers are configured (safe fallback for development/testing).
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Dedicated CSRF logger to a separate file (toggle via env CSRF_LOG_ENABLED)
csrf_logger = logging.getLogger('csrf')
try:
    _csrf_log_enabled = str(os.getenv('CSRF_LOG_ENABLED', '0')).lower() in ('1', 'true', 'yes', 'on')
    csrf_logger.propagate = False  # don't bleed into root
    if _csrf_log_enabled:
        if not csrf_logger.handlers:
            _csrf_handler = logging.FileHandler('csrf.log')
            _csrf_formatter = logging.Formatter('%(asctime)s %(levelname)s:%(name)s: %(message)s')
            _csrf_handler.setFormatter(_csrf_formatter)
            csrf_logger.addHandler(_csrf_handler)
        csrf_logger.disabled = False
        csrf_logger.setLevel(logging.INFO)
        logger.info('CSRF logging: ENABLED (csrf.log)')
    else:
        # Ensure disabled and no handlers are added so calls are no-ops
        csrf_logger.handlers.clear()
        csrf_logger.disabled = True
        logger.info('CSRF logging: DISABLED (set CSRF_LOG_ENABLED=1 to enable)')
except Exception:
    # Fail closed if configuration fails
    try:
        csrf_logger.disabled = True
        csrf_logger.propagate = False
    except Exception:
        pass

# Helper to record assertion-style diagnostics to csrf.log
def csrf_assert(ok: bool, code: str, message: str | None = None, **context):
    try:
        status = 'PASS' if ok else 'FAIL'
        payload = {'assert': code, **context}
        if message:
            payload['msg'] = message
        csrf_logger.info('ASSERT %s %s %s', status, code, payload)
    except Exception:
        # Avoid raising from logging helper
        pass

# Helper: parse CSRF JWT into info dict for diagnostics (safe best-effort)
def _csrf_token_info(token: str | None):
    info = {
        'present': bool(token),
        'sub': None,
        'type': None,
        'exp': None,
        'exp_iso': None,
        'remaining': None,
        'hash': None,
    }
    try:
        import hashlib
        info['hash'] = hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:12] if token else None
        if not token:
            return info
        import base64
        import json
        import datetime
        parts = token.split('.')
        if len(parts) >= 2:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
            info['sub'] = payload.get('sub')
            info['type'] = payload.get('type')
            exp = payload.get('exp')
            if exp is not None:
                try:
                    exp_int = int(exp)
                    info['exp'] = exp_int
                    info['exp_iso'] = datetime.datetime.fromtimestamp(exp_int, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
                    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                    info['remaining'] = exp_int - now_ts
                except Exception:
                    pass
    except Exception:
        pass
    return info

# Track the last CSRF issued per user in-process for compatibility assertions
_last_csrf_by_user: dict[str, dict] = {}

def _record_issued_csrf(username: str | None, token: str, source: str):
    if not username:
        return
    info = _csrf_token_info(token)
    _last_csrf_by_user[username] = {'token_hash': info.get('hash'), 'exp': info.get('exp'), 'source': source}
    # Assertions about newly issued token
    csrf_assert(info.get('type') == 'csrf', 'csrf_issue_type', source=source, token_hash=info.get('hash'), typ=info.get('type'))
    rem = info.get('remaining')
    if rem is not None:
        csrf_assert(abs(rem - CSRF_TOKEN_EXPIRE_SECONDS) <= 5, 'csrf_issue_lifetime', source=source, expected=CSRF_TOKEN_EXPIRE_SECONDS, remaining=rem)
    else:
        csrf_assert(False, 'csrf_issue_decode', source=source)
    # Also assert exp aligns with now + configured seconds
    try:
        import datetime
        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        expected_exp = now_ts + int(CSRF_TOKEN_EXPIRE_SECONDS)
        actual_exp = info.get('exp')
        if actual_exp is not None:
            csrf_assert(abs(int(actual_exp) - expected_exp) <= 5, 'csrf_issue_expected_exp', source=source, expected_exp=expected_exp, actual_exp=int(actual_exp), now_ts=now_ts, configured=CSRF_TOKEN_EXPIRE_SECONDS)
        else:
            csrf_assert(False, 'csrf_issue_no_exp', source=source)
    except Exception:
        pass

# Extract all csrf_token values from a Cookie header for diagnosis
def _extract_all_csrf_from_cookie_header(cookie_header: str | None) -> list[dict]:
    if not cookie_header:
        return []
    tokens: list[dict] = []
    try:
        parts = cookie_header.split(';')
        for p in parts:
            kv = p.strip()
            if not kv:
                continue
            if kv.startswith('csrf_token='):
                val = kv[len('csrf_token='):]
                info = _csrf_token_info(val)
                tokens.append(info)
    except Exception:
        pass
    return tokens

# Helper to set a csrf_token cookie on a response for a username
def _issue_csrf_cookie(resp, username: str):
    try:
        from .auth import create_csrf_token
        csrf = create_csrf_token(username)
        # httponly=False so client-side JS can read when necessary
        resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE, path='/')
        # Record and assert properties of the newly issued token
        _record_issued_csrf(username, csrf, source='issue_cookie')
        try:
            csrf_assert(True, 'csrf_cookie_set', source='issue_cookie', path='/')
        except Exception:
            pass
    except Exception:
        logger.exception('failed to issue csrf cookie')


def _redirect_or_json(request: Request, url: str, extra: dict | None = None, status: int = 303):
    """Return JSON when client asked for application/json, otherwise a RedirectResponse.

    JSON payload is {'ok': True, 'redirect': url, **extra}.
    """
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        payload = {'ok': True, 'redirect': url}
        try:
            if extra:
                payload.update(extra)
        except Exception:
            pass
        return JSONResponse(payload)
    return RedirectResponse(url=url, status_code=status)


# (middleware registered later after app is created)

# Print a startup notice if SECRET_KEY is already set in the environment. This
# helps detect accidental secrets left in the environment when starting the
# server. Printed to stderr so it appears in most service logs.
try:
    if os.environ.get('SECRET_KEY'):
        print('NOTICE: environment variable SECRET_KEY is set. Ensure this is intended and not a leaked secret.', file=sys.stderr)
except Exception:
    # Keep startup robust; do not prevent server from starting if print fails
    pass

# Always print CSRF expiry seconds at startup so it's visible in service logs
try:
    from .auth import CSRF_TOKEN_EXPIRE_SECONDS
    print(f'CONFIG: CSRF_TOKEN_EXPIRE_SECONDS={CSRF_TOKEN_EXPIRE_SECONDS}', file=sys.stderr)
except Exception:
    # Do not break startup if import/print fails
    pass

# Hard-enable verbose debug logging for debugging intermittent server 403s.
# Set to False to disable. This is intentionally hardcoded per request.
ENABLE_VERBOSE_DEBUG = True

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
# Whether SSE debug emissions are permitted in current context. Set per HTTP request
# by middleware based on client origin (localhost only by default) and env overrides.
_sse_allowed: ContextVar[bool] = ContextVar('_sse_allowed', default=False)
# Per-request cache for fn:link label resolution to avoid DB lookups during template render
_fn_link_label_cache: ContextVar[dict | None] = ContextVar('_fn_link_label_cache', default=None)


class InMemoryHandler(logging.Handler):
    def emit(self, record):
        try:
            # Format using the handler's formatter if present, else basic message
            msg = self.format(record) if self.formatter else record.getMessage()
            # timestamp in UTC ISO (use timezone-aware now)
            ts = datetime.now(timezone.utc).isoformat()
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
            ts = datetime.now(timezone.utc).isoformat()
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
# Enable the 'do' extension which some templates use (eg. index_ios_safari.html)
try:
    TEMPLATES.env.add_extension('jinja2.ext.do')
except Exception:
    # fail closed: if extension can't be added, keep running without raising
    logger.exception('failed to add jinja2.ext.do extension to TEMPLATES.env')
TEMPLATES.env.filters['server_local_dt'] = format_server_local
TEMPLATES.env.filters['in_tz'] = format_in_timezone
# Expose config in Jinja templates (e.g., for DOKUWIKI_NOTE_LINK_PREFIX)
try:
    TEMPLATES.env.globals['config'] = config
except Exception:
    # keep template setup robust if globals assignment fails
    logger.exception('failed to inject config into Jinja env globals')
from markupsafe import Markup, escape
import re
import time
from urllib.parse import quote_plus


def linkify(text: str | None) -> Markup:
    """Convert bare http(s) URLs in text into clickable links and return
    safe HTML Markup. Keeps other text escaped.
    """
    if not text:
        return Markup("")

    # Regex for bare URLs in text segments (not inside existing anchors)
    url_re = re.compile(r"(https?://[^\s<]+)")
    # Regex to split into anchor vs non-anchor segments
    anchor_re = re.compile(r"(<a\b[^>]*>.*?</a>)", re.IGNORECASE | re.DOTALL)

    def _linkify_segment(segment: str) -> str:
        # Escape non-anchor text first, then replace bare URLs with anchors
        seg = escape(segment)
        def _repl(m: re.Match) -> str:
            url = m.group(1)
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{escape(url)}</a>'
        return url_re.sub(lambda m: _repl(m), str(seg))

    s = str(text)
    parts = anchor_re.split(s)
    out_parts: list[str] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if anchor_re.match(part):
            # Preserve existing anchors verbatim
            out_parts.append(part)
        else:
            out_parts.append(_linkify_segment(part))
    return Markup(''.join(out_parts))


TEMPLATES.env.filters['linkify'] = linkify


def render_fn_tags(text: str | None) -> Markup:
    """Render {{fn:...}} tags into safe HTML buttons with data attributes.

    Recognizes tags of the form:
      {{fn:identifier arg1=val1,arg2=val2 | Label ?confirm="Are you sure?"}}

    Produces HTML like:
      <button data-fn="identifier" data-args='{"arg1":"val1"}'>Label</button>

    Malformed tags are returned escaped.
    """
    if not text:
        return Markup("")

    # Regex to find {{fn: ... }} non-greedy
    tag_re = re.compile(r"\{\{\s*fn:([^\}]+?)\s*\}\}")

    def _render_match(m: re.Match) -> str:
        body = m.group(1).strip()
        try:
            # Split off a label part after a '|' if present
            label = None
            confirm = None
            if '|' in body:
                before, after = body.split('|', 1)
                body = before.strip()
                label_part = after.strip()
                # label may include ?confirm="..."
                if '?confirm=' in label_part:
                    lp, conf = label_part.split('?confirm=', 1)
                    label = lp.strip()
                    # strip optional surrounding quotes
                    conf = conf.strip()
                    if (conf.startswith('"') and conf.endswith('"')) or (conf.startswith("'") and conf.endswith("'")):
                        conf = conf[1:-1]
                    confirm = conf
                else:
                    label = label_part

            # Now parse identifier and arg list
            parts = body.split(None, 1)
            identifier = parts[0].strip()
            args_text = parts[1].strip() if len(parts) > 1 else ''

            args = {}
            if args_text:
                # Support comma-separated key=val pairs, values may be quoted
                # Simple parser: split on commas not inside quotes
                cur = ''
                in_q = None
                pairs = []
                for ch in args_text:
                    if ch in ('"', "'"):
                        if in_q is None:
                            in_q = ch
                        elif in_q == ch:
                            in_q = None
                        cur += ch
                    elif ch == ',' and in_q is None:
                        pairs.append(cur)
                        cur = ''
                    else:
                        cur += ch
                if cur.strip():
                    pairs.append(cur)

                for p in pairs:
                    if '=' in p:
                        k, v = p.split('=', 1)
                        k = k.strip()
                        v = v.strip()
                        # strip quotes
                        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                            v = v[1:-1]
                        # normalize tags into a list
                        if k == 'tags':
                            # allow tags="#a,#b" or tags=#a,#b (we split earlier on commas so handle single value)
                            if isinstance(v, str) and ',' in v:
                                args[k] = [t.strip() for t in v.split(',') if t.strip()]
                            else:
                                args[k] = [v]
                        else:
                            args[k] = v
                    else:
                        # positional tag-like argument, e.g., #tag -> collect into tags list
                        val = p.strip()
                        if val:
                            if 'tags' in args and isinstance(args['tags'], str):
                                # convert stray string to list
                                args['tags'] = [args['tags']]
                            args.setdefault('tags', []).append(val)

            # Final normalization: ensure tags is a list when present
            if 'tags' in args and not isinstance(args['tags'], list):
                if isinstance(args['tags'], str):
                    args['tags'] = [t.strip() for t in args['tags'].split(',') if t.strip()]
                else:
                    args['tags'] = [args['tags']]

            # Build data-args JSON safely
            data_args = json.dumps(args, ensure_ascii=False)

            btn_label = (label or identifier)

            # Escape label for HTML
            esc_label = escape(btn_label)
            esc_ident = escape(identifier)
            esc_args = escape(data_args)

            attrs = f'data-fn="{esc_ident}" data-args="{esc_args}"'
            if confirm:
                attrs += f' data-confirm="{escape(confirm)}"'

            # For navigation-style functions (search.multi) render an anchor so
            # middle-click / Ctrl+click / right-click -> open in new tab works
            if identifier == 'search.multi':
                # Build a simple query from tags if present (comma-separated)
                q = ''
                try:
                    if 'tags' in args and isinstance(args['tags'], list):
                        # remove any internal whitespace from tags (server convention)
                        cleaned = [t.replace(' ', '') for t in args['tags'] if isinstance(t, str)]
                        # join with spaces so the search page receives separate tokens
                        q = ' '.join(cleaned)
                except Exception:
                    q = ''
                href = '/html_no_js/search?q=' + quote_plus(q)
                # Important: emit a plain anchor without data-fn so middle/Ctrl-click works and no exec-fn intercept
                return f'<a class="fn-button" role="link" href="{escape(href)}">{esc_label}</a>'

            # External URL link with optional custom label
            if identifier == 'url':
                try:
                    # Determine href from explicit args or first positional token
                    href_val = None
                    if isinstance(args, dict):
                        href_val = args.get('href') or args.get('url')
                        if (not href_val) and isinstance(args.get('tags'), list) and args.get('tags'):
                            href_val = args['tags'][0]
                    href = str(href_val or '').strip()
                    # basic scheme safety: require http/https
                    if not href.lower().startswith('http://') and not href.lower().startswith('https://'):
                        # if missing scheme but looks like domain, prepend http:// as a convenience
                        if href and '://' not in href and ('.' in href or href.startswith('www.')):
                            href = 'http://' + href
                    if not href:
                        # malformed; fall back to button rendering
                        raise ValueError('missing href')
                    # Label: use custom label if provided; else show the URL
                    link_label = btn_label if label else href
                    # Attributes
                    target = (args.get('target') if isinstance(args, dict) else None) or '_blank'
                    rel = (args.get('rel') if isinstance(args, dict) else None) or 'noopener noreferrer'
                    # Optional nofollow/noreferrer flags
                    try:
                        def _is_true(v):
                            s = str(v).strip().lower() if v is not None else ''
                            return s in ('1','true','yes','on') or v is True
                        if isinstance(args, dict) and (_is_true(args.get('nofollow')) or ('nofollow' in args and args.get('nofollow') is None)):
                            if 'nofollow' not in rel:
                                rel = (rel + ' nofollow').strip()
                    except Exception:
                        pass
                    return (
                        f'<a class="fn-button fn-url" role="link" href="{escape(href)}" target="{escape(target)}" rel="{escape(rel)}">'
                        f'{escape(link_label)}</a>'
                    )
                except Exception:
                    # fall through to default
                    pass

            # Navigation link to a specific todo or list by id
            if identifier == 'link':
                try:
                    kind = None
                    target_id = None
                    # Prefer a combined target like "todo:123" or "list:45"
                    tval = args.get('target') if isinstance(args, dict) else None
                    if isinstance(tval, str) and ':' in tval:
                        k, v = tval.split(':', 1)
                        kind = (k or '').strip().lower()
                        try:
                            target_id = int((v or '').strip())
                        except Exception:
                            target_id = None
                    else:
                        # Accept separate keys: type + id, or todo/list keys directly
                        if 'type' in args and 'id' in args:
                            kind = str(args.get('type') or '').strip().lower()
                            try:
                                target_id = int(str(args.get('id') or '').strip())
                            except Exception:
                                target_id = None
                        elif 'todo' in args:
                            kind = 'todo'
                            try:
                                target_id = int(str(args.get('todo') or '').strip())
                            except Exception:
                                target_id = None
                        elif 'list' in args:
                            kind = 'list'
                            try:
                                target_id = int(str(args.get('list') or '').strip())
                            except Exception:
                                target_id = None

                    if kind in ('todo', 'list') and isinstance(target_id, int) and target_id > 0:
                        href = f"/html_no_js/{'todos' if kind=='todo' else 'lists'}/{target_id}"
                        # Resolve label: prefer explicit |Label; else use the actual item name/text when possible
                        has_custom_label = bool(label)
                        link_label = btn_label if has_custom_label else None
                        link_priority: int | None = None
                        link_tags: list[str] | None = None
                        # determine if priority should be suppressed via args
                        def _is_false(v: str | bool | None) -> bool:
                            if v is None:
                                return False
                            if isinstance(v, bool):
                                return (v is False)
                            s = str(v).strip().lower()
                            return s in ('0','false','no','off')
                        show_prio = True
                        try:
                            if _is_false(args.get('show_priority')) or _is_false(args.get('priority')) or ('no_priority' in args) or ('nopriority' in args):
                                show_prio = False
                        except Exception:
                            show_prio = True
                        # First check per-request cache for name, priority, and tags
                        cache = _fn_link_label_cache.get() or {}
                        cache_key = f"{kind}:{target_id}"
                        cached = cache.get(cache_key) if cache else None
                        if isinstance(cached, dict):
                            try:
                                if link_priority is None:
                                    link_priority = cached.get('priority')
                                # Only adopt cached label when no custom label given
                                if not has_custom_label and (not link_label):
                                    link_label = cached.get('name') or cached.get('label')
                                if link_tags is None:
                                    ct = cached.get('tags')
                                    if isinstance(ct, list):
                                        link_tags = [str(x) for x in ct if isinstance(x, str)] or []
                            except Exception:
                                pass
                        elif isinstance(cached, str) and cached and not has_custom_label and not link_label:
                            link_label = cached

                        # Decide if we need a DB lookup: if label is missing (no custom) or we need priority or tags
                        need_lookup = (not has_custom_label and not link_label) or (show_prio and (link_priority is None)) or (link_tags is None)
                        resolved_name: str | None = None
                        if need_lookup:
                            # Try SQLAlchemy sync session first; if that fails (e.g., async driver), try sqlite3 direct.
                            looked_up = False
                            try:
                                from .db import engine, TracedSyncSession
                                with TracedSyncSession(bind=getattr(engine, 'sync_engine', None)) as _s:
                                    if kind == 'todo':
                                        res = _s.execute(select(Todo.text, Todo.priority).where(Todo.id == target_id)).first()
                                        if res:
                                            txt = res[0] if isinstance(res, (tuple, list)) else None
                                            pr = res[1] if isinstance(res, (tuple, list)) and len(res) > 1 else None
                                            if isinstance(txt, str) and txt.strip():
                                                resolved_name = txt.strip()
                                                if not has_custom_label and not link_label:
                                                    link_label = resolved_name
                                            try:
                                                if link_priority is None:
                                                    link_priority = int(pr) if pr is not None else None
                                            except Exception:
                                                link_priority = None
                                            # fetch hashtags for todo
                                            try:
                                                qtags = select(Hashtag.tag).join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id).where(TodoHashtag.todo_id == target_id)
                                                rtags = _s.execute(qtags).all()
                                                tags_list: list[str] = []
                                                for row in rtags:
                                                    val = row[0] if isinstance(row, (tuple, list)) else row
                                                    if isinstance(val, str) and val:
                                                        tags_list.append(val)
                                                link_tags = tags_list
                                            except Exception:
                                                pass
                                            looked_up = True
                                    else:
                                        res = _s.execute(select(ListState.name, ListState.priority).where(ListState.id == target_id)).first()
                                        if res:
                                            name = res[0] if isinstance(res, (tuple, list)) else None
                                            pr = res[1] if isinstance(res, (tuple, list)) and len(res) > 1 else None
                                            if isinstance(name, str) and name.strip():
                                                resolved_name = name.strip()
                                                if not has_custom_label and not link_label:
                                                    link_label = resolved_name
                                            try:
                                                if link_priority is None:
                                                    link_priority = int(pr) if pr is not None else None
                                            except Exception:
                                                link_priority = None
                                            # fetch hashtags for list
                                            try:
                                                qtags = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == target_id)
                                                rtags = _s.execute(qtags).all()
                                                tags_list: list[str] = []
                                                for row in rtags:
                                                    val = row[0] if isinstance(row, (tuple, list)) else row
                                                    if isinstance(val, str) and val:
                                                        tags_list.append(val)
                                                link_tags = tags_list
                                            except Exception:
                                                pass
                                            looked_up = True
                            except Exception:
                                pass
                            # Fallback: direct sqlite3 if using local sqlite DB
                            if not looked_up:
                                try:
                                    from .db import DATABASE_URL as _DB_URL
                                    from .db import _sqlite_path_from_url as _sqlite_path_from_url
                                    path = _sqlite_path_from_url(_DB_URL)
                                    if path:
                                        import sqlite3
                                        import os as _os
                                        abs_path = _os.path.abspath(path)
                                        if _os.path.exists(abs_path):
                                            con = sqlite3.connect(abs_path)
                                            try:
                                                cur = con.cursor()
                                                if kind == 'todo':
                                                    cur.execute('SELECT text, priority FROM todo WHERE id = ?', (target_id,))
                                                else:
                                                    cur.execute('SELECT name, priority FROM liststate WHERE id = ?', (target_id,))
                                                row = cur.fetchone()
                                                if row:
                                                    if isinstance(row[0], str) and row[0].strip():
                                                        resolved_name = row[0].strip()
                                                        if not has_custom_label and not link_label:
                                                            link_label = resolved_name
                                                    try:
                                                        if link_priority is None:
                                                            link_priority = int(row[1]) if len(row) > 1 and row[1] is not None else None
                                                    except Exception:
                                                        link_priority = None
                                                # fetch hashtags via sqlite
                                                try:
                                                    if kind == 'todo':
                                                        cur.execute('SELECT h.tag FROM hashtag h JOIN todohashtag th ON th.hashtag_id = h.id WHERE th.todo_id = ?', (target_id,))
                                                    else:
                                                        cur.execute('SELECT h.tag FROM hashtag h JOIN listhashtag lh ON lh.hashtag_id = h.id WHERE lh.list_id = ?', (target_id,))
                                                    rows = cur.fetchall()
                                                    link_tags = [r[0] for r in rows if r and isinstance(r[0], str) and r[0]]
                                                except Exception:
                                                    pass
                                            finally:
                                                try:
                                                    con.close()
                                                except Exception:
                                                    pass
                                except Exception:
                                    pass
                        # If tags were not resolved yet, attempt a light sqlite fallback just for hashtags
                        if link_tags is None:
                            try:
                                from .db import DATABASE_URL as _DB_URL
                                from .db import _sqlite_path_from_url as _sqlite_path_from_url
                                path = _sqlite_path_from_url(_DB_URL)
                                if path:
                                    import sqlite3
                                    import os as _os
                                    abs_path = _os.path.abspath(path)
                                    if _os.path.exists(abs_path):
                                        con = sqlite3.connect(abs_path)
                                        try:
                                            cur = con.cursor()
                                            if kind == 'todo':
                                                cur.execute('SELECT h.tag FROM hashtag h JOIN todohashtag th ON th.hashtag_id = h.id WHERE th.todo_id = ?', (target_id,))
                                            else:
                                                cur.execute('SELECT h.tag FROM hashtag h JOIN listhashtag lh ON lh.hashtag_id = h.id WHERE lh.list_id = ?', (target_id,))
                                            rows = cur.fetchall()
                                            link_tags = [r[0] for r in rows if r and isinstance(r[0], str) and r[0]]
                                            if os.getenv('DEBUG_FN_LINKS', '0').lower() in ('1','true','yes'):
                                                try:
                                                    import os as _os
                                                    import time as _time
                                                    _os.makedirs('debug_logs', exist_ok=True)
                                                    with open(_os.path.join('debug_logs', 'fn_link_debug.log'), 'a', encoding='utf-8') as _f:
                                                        _ts = _time.strftime('%Y-%m-%d %H:%M:%S')
                                                        _f.write(f"[{_ts}] sqlite-tags kind={kind} id={target_id} count={len(link_tags or [])} rows={link_tags!r}\n")
                                                except Exception:
                                                    pass
                                        finally:
                                            try:
                                                con.close()
                                            except Exception:
                                                pass
                            except Exception:
                                pass
                        # Final debug snapshot of what will be rendered
                        if os.getenv('DEBUG_FN_LINKS', '0').lower() in ('1','true','yes'):
                            try:
                                import os as _os
                                import time as _time
                                _os.makedirs('debug_logs', exist_ok=True)
                                with open(_os.path.join('debug_logs', 'fn_link_debug.log'), 'a', encoding='utf-8') as _f:
                                    _ts = _time.strftime('%Y-%m-%d %H:%M:%S')
                                    _f.write(f"[{_ts}] final-tags kind={kind} id={target_id} tags={link_tags!r}\n")
                            except Exception:
                                pass
                        if link_label is None:
                            # Final fallback if lookup failed
                            link_label = f"Todo #{target_id}" if kind == 'todo' else f"List #{target_id}"
                        else:
                            # Save into per-request cache for subsequent references in same render/request
                            cache = _fn_link_label_cache.get() or {}
                            try:
                                cache_key = f"{kind}:{target_id}"
                                # Store true resolved name when available; avoid caching custom labels as titles
                                store_name = resolved_name if isinstance(resolved_name, str) and resolved_name else (None if has_custom_label else link_label)
                                entry = cache.get(cache_key) if isinstance(cache.get(cache_key), dict) else {}
                                if store_name:
                                    entry['name'] = store_name
                                    entry['label'] = store_name
                                if link_priority is not None:
                                    entry['priority'] = link_priority
                                if isinstance(link_tags, list):
                                    entry['tags'] = [str(x) for x in link_tags if isinstance(x, str)]
                                if entry:
                                    cache[cache_key] = entry
                                    _fn_link_label_cache.set(cache)
                            except Exception:
                                pass
                        # Important: do NOT include data-fn/data-args here so clicks navigate normally (no exec-fn)
                        # Optionally append priority circle if available and not suppressed
                        def _circled(n: int | None) -> str:
                            try:
                                if n is None:
                                    return ''
                                n = int(n)
                                if 1 <= n <= 10:
                                    return chr(0x2460 + (n - 1))
                                return str(n)
                            except Exception:
                                return ''
                        pr_html = ''
                        if show_prio and (link_priority is not None):
                            ch = _circled(link_priority)
                            if ch:
                                pr_html = f' <span class="meta priority-inline" title="Priority {int(link_priority)}"><span class="priority-circle">{escape(ch)}</span></span>'
                        # Place the priority markup inside the anchor so post-processing (linkify) preserves it
                        # Build hashtags as separate tag-chip anchors outside the main link
                        tags_html = ''
                        try:
                            if isinstance(link_tags, list) and link_tags:
                                chips: list[str] = []
                                for t in link_tags:
                                    if not isinstance(t, str) or not t:
                                        continue
                                    chips.append(f'<a class="tag-chip" href="/html_no_js/search?q={quote_plus(t)}" role="link">{escape(t)}</a>')
                                if chips:
                                    # No wrapper span to avoid linkify escaping non-anchor HTML; chips have their own spacing.
                                    tags_html = ' ' + ''.join(chips)
                        except Exception:
                            tags_html = ''
                        # Optional debug logging for troubleshooting link rendering
                        try:
                            if os.getenv('DEBUG_FN_LINKS', '0').lower() in ('1','true','yes'):
                                try:
                                    import os as _os
                                    import time as _time
                                    _os.makedirs('debug_logs', exist_ok=True)
                                    with open(_os.path.join('debug_logs', 'fn_link_debug.log'), 'a', encoding='utf-8') as _f:
                                        _ts = _time.strftime('%Y-%m-%d %H:%M:%S')
                                        _f.write(f"[{_ts}] fn:link kind={kind} id={target_id} label={link_label!r} pr={link_priority!r} tags={link_tags!r}\n")
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # Compose label using Markup to avoid double-escaping of our span fragments
                        label_html = escape(link_label) + Markup(pr_html)
                        # Attempt to detect whether the target todo is completed so
                        # we can mark inline anchors with `done` and let CSS
                        # apply a strikethrough. Best-effort: try a sync DB
                        # lookup first, fall back to sqlite direct query.
                        link_completed = False
                        try:
                            if kind == 'todo' and isinstance(target_id, int):
                                try:
                                    from .db import engine, TracedSyncSession
                                    with TracedSyncSession(bind=getattr(engine, 'sync_engine', None)) as _s:
                                        q = _s.execute(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id == target_id).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                                        row = q.first()
                                        if row:
                                            link_completed = True
                                except Exception:
                                    try:
                                        from .db import DATABASE_URL as _DB_URL
                                        from .db import _sqlite_path_from_url as _sqlite_path_from_url
                                        path = _sqlite_path_from_url(_DB_URL)
                                        if path:
                                            import sqlite3
                                            import os as _os
                                            abs_path = _os.path.abspath(path)
                                            if _os.path.exists(abs_path):
                                                con = sqlite3.connect(abs_path)
                                                try:
                                                    cur = con.cursor()
                                                    cur.execute("SELECT tc.todo_id FROM todocompletion tc JOIN completiontype ct ON tc.completion_type_id = ct.id WHERE tc.todo_id = ? AND ct.name = 'default' AND tc.done = 1", (target_id,))
                                                    prow = cur.fetchone()
                                                    if prow:
                                                        link_completed = True
                                                finally:
                                                    try:
                                                        con.close()
                                                    except Exception:
                                                        pass
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        cls = 'fn-button fn-link'
                        if link_completed:
                            cls += ' done'
                        return f'<a class="{cls}" role="link" href="{escape(href)}">{label_html}</a>' + tags_html
                    # If parsing failed, fall through to default button rendering
                except Exception:
                    pass

            return f'<button type="button" class="fn-button" {attrs}>{esc_label}</button>'
        except Exception:
            # On any parse error, return the original text escaped
            return escape(m.group(0))

    # First escape the whole text to avoid HTML injection, then un-escape/replace tags
    esc = escape(text)

    # We need to run tag replacement on the raw text, not the escaped one, to preserve parsing
    try:
        out = tag_re.sub(lambda m: _render_match(m), str(text))
        return Markup(out)
    except Exception:
        return Markup(escape(text))


TEMPLATES.env.filters['render_fn_tags'] = render_fn_tags
 


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
        # Determine if emission is allowed for this context.
        # - By default, only local HTTP requests are allowed (middleware sets _sse_allowed).
        # - Background tasks (no origin) are blocked unless SSE_DEBUG_ALLOW_BACKGROUND=1.
        # - Non-local HTTP requests can be allowed via SSE_DEBUG_ALLOW_NONLOCAL=1.
        try:
            allowed_ctx = _sse_allowed.get()
        except Exception:
            allowed_ctx = False
        try:
            allow_background = os.getenv('SSE_DEBUG_ALLOW_BACKGROUND', '0').lower() in ('1', 'true', 'yes')
        except Exception:
            allow_background = False
        # If origin is None, treat as background context
        is_background = origin is None
        if (is_background and not allow_background) or (not is_background and not allowed_ctx):
            return
        debug_payload = {'event': event, 'payload': payload}
        if origin:
            # don't mutate original payload; annotate separately
            debug_payload['source'] = origin
        rec = {'ts': datetime.now(timezone.utc).isoformat(), 'level': 'DEBUG', 'logger': 'sse.debug', 'message': json.dumps(debug_payload)}
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
    return ('iphone' in ua or 'ipad' in ua or 'ipod' in ua) and 'safari' in ua and 'crios' not in ua and 'fxios' not in ua

 


async def get_session_timezone(request: Request) -> str | None:
    """Prefer timezone stored on the server-side Session row; fall back to tz cookie."""
    # prefer session-stored tz when available
    st = request.cookies.get('session_token')
    if st:
        try:
            async with async_session() as sess:
                q = await sess.scalars(select(Session).where(Session.session_token == st))
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

# Verbose calendar diagnostics (disabled by default). Set CALENDAR_VERBOSE_DEBUG=1
# to enable chatty per-todo inspect logs during calendar extraction.
try:
    CALENDAR_VERBOSE_DEBUG = os.getenv('CALENDAR_VERBOSE_DEBUG', '0').lower() in ('1', 'true', 'yes')
except Exception:
    CALENDAR_VERBOSE_DEBUG = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup checks
    # Warn or fail if SECRET_KEY is left as the test fallback in production-like environments.
    try:
        from .auth import SECRET_KEY as _SECRET_KEY
    except Exception:
        _SECRET_KEY = None
    # If SECRET_KEY is missing or still the test fallback, fail fast. The
    # application should not start without a proper secret in the environment
    # to avoid subtle authentication/CSRF/security issues.
    if _SECRET_KEY is None or _SECRET_KEY == "CHANGE_ME_IN_ENV_FOR_TESTS":
        raise RuntimeError("SECRET_KEY not set or insecure fallback in use; set the SECRET_KEY environment variable before starting the server")

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
    # Announce fn:link debug if enabled so it’s visible in console
    try:
        _d = os.getenv('DEBUG_FN_LINKS', '0').lower()
        if _d in ('1','true','yes'):
            logger.info('DEBUG_FN_LINKS enabled: fn:link will log to debug_logs/fn_link_debug.log')
            # Also print directly in case logger routing filters this out
            print('[app] DEBUG_FN_LINKS enabled: fn:link will log to debug_logs/fn_link_debug.log', flush=True)
    except Exception:
        pass
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
    # Optionally start SSH REPL server (before yield so it's available during app lifetime)
    ssh_server = None
    try:
        _enable_ssh = os.getenv('SSH_REPL_ENABLE', '0').lower() in ('1','true','yes')
    except Exception:
        _enable_ssh = False
    if _enable_ssh:
        try:
            bind = os.getenv('SSH_REPL_BIND','0.0.0.0')
            port = int(os.getenv('SSH_REPL_PORT','2222'))
            hk = os.getenv('SSH_REPL_HOST_KEY_PATH','./ssh_repl_host_key')
            logger.info('SSH REPL: enabled by env; attempting to start (bind=%s port=%s host_key=%s)', bind, port, hk)
            # quick port availability check to avoid conflicting binds (race is possible but rare)
            import socket as _socket
            def _port_free(host: str, port_num: int) -> bool:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                try:
                    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                    s.bind((host, port_num))
                    s.close()
                    return True
                except Exception:
                    try:
                        s.close()
                    except Exception:
                        pass
                    return False

            if not _port_free(bind, port):
                logger.warning('SSH REPL: port %s:%d already in use; will not start embedded SSH server', bind, port)
            else:
                from .ssh_repl import start_server as _start_ssh
                try:
                    ssh_server = await _start_ssh()
                    logger.info('SSH REPL: started (bind=%s port=%s)', bind, port)
                except OSError as _e:
                    # Common case: address already in use if another process bound the port between our check and bind
                    logger.warning('SSH REPL: failed to start (bind error): %s', _e)
                    ssh_server = None
                except Exception:
                    logger.exception('SSH REPL: failed to start')
                    ssh_server = None
        except Exception:
            logger.exception('SSH REPL: unexpected error during startup attempt')
    else:
        logger.info('SSH REPL: disabled (set SSH_REPL_ENABLE=1 to enable)')
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
        # Stop SSH REPL server on shutdown
        try:
            if ssh_server is not None:
                logger.info('SSH REPL: stopping')
                ssh_server.close()
                await ssh_server.wait_closed()
                logger.info('SSH REPL: stopped')
        except Exception:
            logger.exception('SSH REPL: error while stopping')


app = FastAPI(lifespan=lifespan)
# Log CSRF expiry configuration at startup
try:
    mins = CSRF_TOKEN_EXPIRE_SECONDS / 60.0
    logger.info('config: CSRF_TOKEN_EXPIRE_SECONDS=%d (~%.1f minutes)', CSRF_TOKEN_EXPIRE_SECONDS, mins)
except Exception:
    pass

# serve static assets (manifest, service-worker, icons, pwa helper JS, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Startup instrumentation: write a marker to scripts/index_calendar.log when
# assertion logging is enabled so we can detect the feature is active in logs.
@app.on_event('startup')
async def _index_calendar_startup_marker():
    try:
        from .utils import index_calendar_assert
        # remove existing log to provide a fresh trace for this run
        try:
            fn = os.path.join(os.getcwd(), 'scripts', 'index_calendar.log')
            if os.path.exists(fn):
                os.remove(fn)
        except Exception:
            pass
        index_calendar_assert('startup_enabled', extra={'pid': os.getpid()})
    except Exception:
        # Be silent on failure; do not prevent startup
        logger.exception('failed to write index_calendar startup marker')

# include JSON API router for web clients
try:
    from .client_json_api import router as json_api_router
    app.include_router(json_api_router)
    print("INFO: client_json_api router loaded successfully")
except Exception as e:
    # importing the router is best-effort during static analysis; runtime import errors
    # will surface when the server is run in the proper environment.
    print(f"WARNING: Failed to load client_json_api router: {e}")
    import traceback
    traceback.print_exc()

# include JSON note-edit/login service router
try:
    from .note_edit_service import router as note_service_router
    app.include_router(note_service_router)
    print("INFO: note_edit_service router loaded successfully")
except Exception as e:
    print(f"WARNING: Failed to load note_edit_service router: {e}")
    import traceback
    traceback.print_exc()


# include PWA sync and push router
try:
    from .pwa_sync import router as pwa_sync_router
    app.include_router(pwa_sync_router)
    print("INFO: pwa_sync router loaded successfully")
except Exception as e:
    print(f"WARNING: Failed to load pwa_sync router: {e}")
    import traceback
    traceback.print_exc()

# Templates for Tailwind client (minimal, separate directory)
TEMPLATES_TAILWIND = Jinja2Templates(directory="html_tailwind")
TEMPLATES_TAILWIND.env.auto_reload = True
# Ensure the Tailwind templates have the same helper filters as the no-JS templates
try:
    TEMPLATES_TAILWIND.env.filters['server_local_dt'] = format_server_local
    TEMPLATES_TAILWIND.env.filters['in_tz'] = format_in_timezone
    TEMPLATES_TAILWIND.env.filters['linkify'] = linkify
    TEMPLATES_TAILWIND.env.filters['render_fn_tags'] = render_fn_tags
except Exception:
    # Best-effort only; template rendering will raise if critical filters missing
    logger.exception('failed to register filters on TEMPLATES_TAILWIND')


@app.get('/html_tailwind', response_class=HTMLResponse)
async def html_tailwind_index(request: Request):
    """Serve the minimal Tailwind-based client.

    This route intentionally keeps data minimal. If a logged-in user is
    available we try to fetch them (non-fatal); otherwise we render a
    simple page with an empty todos list. Extend as needed.
    """
    # attempt to detect current_user; if not present redirect to login
    try:
        from .auth import get_current_user as _gcu
        current_user = await _gcu(token=None, request=request)
    except Exception:
        current_user = None

    if not current_user:
        return RedirectResponse(url='/html_tailwind/login', status_code=303)

    # Use shared data-prep so Tailwind and no-js clients render consistent data.
    try:
        ctx = await _prepare_index_context(request, current_user)
    except Exception:
        # Fallback to safe defaults to avoid rendering errors
        try:
            client_tz = await get_session_timezone(request)
        except Exception:
            client_tz = None
        ctx = {"request": request, "title": "Fast Todo Tailwind", "todos": [], "current_user": current_user, "lists_by_category": {}, "categories": [], "pinned_todos": [], "calendar_occurrences": [], "cursors": None, "client_tz": client_tz}

    return TEMPLATES_TAILWIND.TemplateResponse('index.html', ctx)


@app.get('/html_tailwind/list', response_class=HTMLResponse)
async def html_tailwind_view_list(request: Request):
    """Render a single list using the Tailwind templates.

    Accepts query param `id=<list_id>`. Mirrors the data prepared by the
    no-JS list view so templates receive the same keys, but renders the
    `html_tailwind/list.html` template.
    """
    id_str = request.query_params.get('id')
    try:
        list_id = int(id_str) if id_str is not None else None
    except Exception:
        list_id = None

    try:
        from .auth import get_current_user as _gcu
        current_user = await _gcu(token=None, request=request)
    except Exception:
        current_user = None

    if not current_user:
        return RedirectResponse(url='/html_tailwind/login', status_code=303)

    if list_id is None:
        raise HTTPException(status_code=404, detail='list id required')

    # Reuse the same list-loading logic as the html_no_js handler so
    # templates get consistent context. This duplicates the data-fetching
    # but intentionally keeps behaviour identical.
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # completion types
        qct = await sess.scalars(
            select(CompletionType)
            .where(CompletionType.list_id == list_id)
            .order_by(CompletionType.id.asc())
        )
        ctypes = qct.all()

        # todos and completion states
        try:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.priority.desc().nullslast(), Todo.created_at.desc()))
        except Exception:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
        todos = q2.all()
        todo_ids = [t.id for t in todos]
        ctype_ids = [c.id for c in ctypes]
        status_map: dict[tuple[int, int], bool] = {}
        if todo_ids and ctype_ids:
            qtc = select(TodoCompletion.todo_id, TodoCompletion.completion_type_id, TodoCompletion.done).where(TodoCompletion.todo_id.in_(todo_ids)).where(TodoCompletion.completion_type_id.in_(ctype_ids))
            r = await sess.exec(qtc)
            for tid, cid, done_val in r.all():
                status_map[(tid, cid)] = bool(done_val)

        default_ct = next((c for c in ctypes if c.name == 'default'), None)
        default_id = default_ct.id if default_ct else None

        todo_rows = []
        for t in todos:
            completed_default = False
            if default_id is not None:
                completed_default = status_map.get((t.id, default_id), False)
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
                "priority": getattr(t, 'priority', None),
                "extra_completions": extra,
            })

        def _todo_display_sort_key(row):
            p = row.get('priority') if not row.get('completed') else None
            has_p = 1 if p is not None else 0
            pr_val = p if p is not None else -999
            return (has_p, pr_val, row.get('created_at').timestamp() if row.get('created_at') else 0)

        todo_rows.sort(key=_todo_display_sort_key, reverse=True)

        # fetch hashtags for todos
        todo_ids = [r['id'] for r in todo_rows]
        tags_map = {}
        if todo_ids:
            qth = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_ids))
            tres = await sess.exec(qth)
            for tid, tag in tres.all():
                tags_map.setdefault(tid, []).append(tag)
        for r in todo_rows:
            r['tags'] = tags_map.get(r['id'], [])

        # list-level hashtags
        ql = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == list_id)
        lres = await sess.exec(ql)
        _rows = lres.all()
        list_tags: list[str] = []
        for row in _rows:
            if isinstance(row, (tuple, list)):
                val = row[0]
            else:
                val = row
            if isinstance(val, str) and val:
                list_tags.append(val)

        list_row = {
            "id": lst.id,
            "name": lst.name,
            "completed": lst.completed,
            "hashtags": list_tags,
            "hide_icons": getattr(lst, 'hide_icons', False),
            "category_id": getattr(lst, 'category_id', None),
            "list_id": lst.id,
            "lists_up_top": getattr(lst, 'lists_up_top', False),
            "priority": getattr(lst, 'priority', None),
            "parent_todo_id": getattr(lst, 'parent_todo_id', None),
            "parent_list_id": getattr(lst, 'parent_list_id', None),
        }

        if getattr(lst, 'parent_todo_id', None):
            try:
                qpt = await sess.exec(select(Todo.text).where(Todo.id == lst.parent_todo_id))
                row = qpt.first()
                todo_text = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(todo_text, str):
                    list_row["parent_todo_text"] = todo_text
            except Exception:
                list_row["parent_todo_text"] = None

        if getattr(lst, 'parent_list_id', None):
            try:
                qpl = await sess.exec(select(ListState.name).where(ListState.id == lst.parent_list_id))
                row = qpl.first()
                parent_list_name = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(parent_list_name, str):
                    list_row["parent_list_name"] = parent_list_name
            except Exception:
                list_row["parent_list_name"] = None

        completion_types = [{'id': c.id, 'name': c.name} for c in ctypes]

        # gather user hashtags
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

        # categories (user-scoped)
        try:
            qcat = select(Category).where(Category.owner_id == current_user.id).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cres.all()]
        except Exception:
            categories = []

        # sublists
        sublists = []
        try:
            qsubs = select(ListState).where(ListState.parent_list_id == list_id).where(ListState.owner_id == current_user.id)
            rsubs = await sess.exec(qsubs)
            rows = rsubs.all()
            def _sort_key(l):
                pos = getattr(l, 'parent_list_position', None)
                created = getattr(l, 'created_at', None)
                return (0 if pos is not None else 1, pos if pos is not None else 0, created or now_utc())
            rows.sort(key=_sort_key)
            sub_ids = [l.id for l in rows if l.id is not None]
            tag_map: dict[int, list[str]] = {}
            if sub_ids:
                qlh = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(sub_ids))
                rlh = await sess.exec(qlh)
                for lid, tag in rlh.all():
                    tag_map.setdefault(lid, []).append(tag)
            for l in rows:
                sublists.append({
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'created_at': getattr(l, 'created_at', None),
                    'modified_at': getattr(l, 'modified_at', None),
                    'hashtags': tag_map.get(l.id, []),
                    'parent_list_position': getattr(l, 'parent_list_position', None),
                    'override_priority': None,
                    'priority': getattr(l, 'priority', None),
                })
            try:
                if sub_ids:
                    todo_q = await sess.scalars(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(sub_ids)).where(Todo.priority != None))
                    todo_id_rows = todo_q.all()
                    todo_map: dict[int, list[tuple[int,int]]] = {}
                    todo_ids = []
                    for tid, lid, pri in todo_id_rows:
                        todo_map.setdefault(lid, []).append((tid, pri))
                        todo_ids.append(tid)
                    completed_ids = set()
                    if todo_ids:
                        try:
                            qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                            cres = await sess.exec(qcomp)
                            completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                        except Exception:
                            completed_ids = set()
                    for sub in sublists:
                        lid = sub.get('id')
                        candidates = todo_map.get(lid, [])
                        max_p = None
                        for tid, pri in candidates:
                            if tid in completed_ids:
                                continue
                            try:
                                if pri is None:
                                    continue
                                pv = int(pri)
                            except Exception:
                                continue
                            if max_p is None or pv > max_p:
                                max_p = pv
                        if max_p is not None:
                            sub['override_priority'] = max_p
            except Exception:
                pass
        except Exception:
            sublists = []

    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    try:
        await record_list_visit(list_id=list_id, current_user=current_user)
    except Exception:
        logger.exception('failed to record list visit for list %s', list_id)
    client_tz = await get_session_timezone(request)
    return TEMPLATES_TAILWIND.TemplateResponse(request, "list.html", {"request": request, "list": list_row, "todos": todo_rows, "csrf_token": csrf_token, "client_tz": client_tz, "completion_types": completion_types, "all_hashtags": all_hashtags, "categories": categories, "sublists": sublists, "current_user": current_user})


@app.get('/html_tailwind/login', response_class=HTMLResponse)
async def html_tailwind_login_get(request: Request):
    client_tz = await get_session_timezone(request)
    return TEMPLATES_TAILWIND.TemplateResponse(request, 'login.html', {"request": request, "client_tz": client_tz})


@app.post('/html_tailwind/login')
async def html_tailwind_login(request: Request):
    """JSON login endpoint for the Tailwind client.

    Expects a JSON body: {"username": "...", "password": "..."}.
    On success returns {'ok': True, 'session_token': ..., 'access_token': ..., 'csrf_token': ...}
    and sets the same cookies as the existing login flow so browser clients
    receive session cookies.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({'ok': False, 'error': 'invalid_json'}, status_code=400)

    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        return JSONResponse({'ok': False, 'error': 'missing_credentials'}, status_code=400)

    from .auth import create_access_token, get_user_by_username, verify_password
    user = await get_user_by_username(username)
    ok = False
    if user:
        ok = await verify_password(password, user.password_hash)
    if not user or not ok:
        return JSONResponse({'ok': False, 'error': 'invalid_credentials'}, status_code=401)

    token = create_access_token({"sub": user.username})
    from .auth import create_session_for_user, create_csrf_token
    client_tz = request.cookies.get('tz')
    session_token = await create_session_for_user(user, session_timezone=client_tz)
    csrf = create_csrf_token(user.username)

    # Return JSON and set cookies on the response so browsers persist them.
    resp = JSONResponse({'ok': True, 'session_token': session_token, 'access_token': token, 'csrf_token': csrf})
    resp.set_cookie('session_token', session_token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('access_token', token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE, path='/')
    try:
        csrf_assert(True, 'csrf_cookie_set', source='login_tailwind', path='/')
    except Exception:
        pass
    try:
        # Record the login-issued CSRF and assert it matches what cookie should carry
        _record_issued_csrf(user.username, csrf, source='login')
        info_login = _csrf_token_info(csrf)
        cookie_token = csrf  # we just set it; for server-side assertion this equals csrf
        info_cookie = _csrf_token_info(cookie_token)
        same_hash = info_login.get('hash') == info_cookie.get('hash')
        csrf_assert(same_hash, 'csrf_login_cookie_same', login_hash=info_login.get('hash'), cookie_hash=info_cookie.get('hash'))
    except Exception:
        pass
    return resp


# Simple ASGI middleware: set `_sse_origin` contextvar for the scope of each
# HTTP request handler so debug events can be annotated with their request
# origin (path). Background tasks or other contexts will leave the origin as None.
@app.middleware('http')
async def _sse_origin_middleware(request: Request, call_next):
    token = None
    token_allowed = None
    try:
        # set the contextvar to a concise string we can surface in SSE
        token = _sse_origin.set(f'http_request:{request.url.path}')
        # set allow flag: permit local requests by default; allow non-local via env override
        try:
            allow_nonlocal = os.getenv('SSE_DEBUG_ALLOW_NONLOCAL', '0').lower() in ('1', 'true', 'yes')
        except Exception:
            allow_nonlocal = False
        try:
            is_local = _is_local_request(request)
        except Exception:
            is_local = False
        token_allowed = _sse_allowed.set(bool(is_local or allow_nonlocal))
    except Exception:
        token = None
    try:
        resp = await call_next(request)
        return resp
    finally:
        try:
            if token is not None:
                _sse_origin.reset(token)
            if token_allowed is not None:
                _sse_allowed.reset(token_allowed)
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


# ---------------- REPL (no-JS) -----------------
@app.get('/html_no_js/repl', response_class=HTMLResponse)
async def html_repl_page(request: Request, current_user: User = Depends(require_login)):
    # require login; show a simple textarea, submit button, and output area
    client_tz = await get_session_timezone(request)
    # issue/refresh csrf cookie for convenience
    try:
        from .auth import create_csrf_token
        csrf = create_csrf_token(current_user.username)
    except Exception:
        csrf = None
    # determine if this user may manage SSH REPL keys
    allow_all = os.getenv('ALLOW_SSH_KEYS_FOR_ALL', '0').lower() in ('1', 'true', 'yes')
    ssh_enabled = bool(current_user.is_admin or allow_all)
    # load current user's SSH REPL keys
    from .models import SshPublicKey
    keys: list[dict] = []
    try:
        async with async_session() as sess:
            res = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == current_user.id).order_by(SshPublicKey.created_at.desc()))
            rows = res.all()
            for r in rows:
                keys.append({"id": r.id, "comment": r.comment, "public_key": r.public_key, "enabled": r.enabled, "created_at": r.created_at})
    except Exception:
        keys = []
    return TEMPLATES.TemplateResponse(request, 'repl.html', {
        "request": request,
        "client_tz": client_tz,
        "csrf_token": csrf,
        "ssh_enabled": ssh_enabled,
        "ssh_keys": keys,
    })


@app.post('/html_no_js/repl/exec', response_class=HTMLResponse)
async def html_repl_exec(request: Request, code: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Run user code in a background thread to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    out: str = ''
    val: Any = None
    # prepare a fresh csrf for the returned page so the next submit is valid
    from .auth import create_csrf_token
    next_csrf = create_csrf_token(current_user.username)
    try:
        out, val = await loop.run_in_executor(None, run_code_for_user, current_user, code)
        # present last value as text
        if val is None:
            result = ''
        elif isinstance(val, str):
            result = val
        else:
            try:
                result = json.dumps(val, default=str, indent=2)
            except Exception:
                result = str(val)
    except Exception as e:
        out = ''
        result = f"Error: {e}"
    client_tz = await get_session_timezone(request)
    # reload user's keys after action
    from .models import SshPublicKey
    keys: list[dict] = []
    try:
        async with async_session() as sess:
            res = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == current_user.id).order_by(SshPublicKey.created_at.desc()))
            rows = res.all()
            for r in rows:
                keys.append({"id": r.id, "comment": r.comment, "public_key": r.public_key, "enabled": r.enabled, "created_at": r.created_at})
    except Exception:
        keys = []
    accept = (request.headers.get('Accept') or '')
    # If the caller expects JSON (AJAX), return a small structured payload.
    if 'application/json' in accept.lower():
        from fastapi.responses import JSONResponse
        return JSONResponse({
            'ok': True,
            'output': out,
            'result': result,
            'code': code,
            'csrf_token': next_csrf,
            'ssh_enabled': bool(current_user.is_admin or os.getenv('ALLOW_SSH_KEYS_FOR_ALL', '0').lower() in ('1','true','yes')),
            'ssh_keys': keys,
        })

    return TEMPLATES.TemplateResponse(request, 'repl.html', {
        "request": request,
        "client_tz": client_tz,
        "output": out,
        "result": result,
        "code": code,
        "csrf_token": next_csrf,
        "ssh_enabled": bool(current_user.is_admin or os.getenv('ALLOW_SSH_KEYS_FOR_ALL', '0').lower() in ('1','true','yes')),
        "ssh_keys": keys,
    })


@app.post('/html_no_js/repl/ssh_keys', response_class=HTMLResponse)
async def html_repl_ssh_keys(request: Request, pubkeys: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token, create_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    allow_all = os.getenv('ALLOW_SSH_KEYS_FOR_ALL', '0').lower() in ('1', 'true', 'yes')
    if not (current_user.is_admin or allow_all):
        raise HTTPException(status_code=403, detail='forbidden')
    # Parse and validate keys
    lines = [ln.strip() for ln in (pubkeys or '').splitlines()]
    key_re = re.compile(r'^(ssh-(rsa|ed25519)|ecdsa-sha2-[^ ]+|sk-ssh-[^ ]+) [A-Za-z0-9+/=]+( .*)?$')
    candidates = [ln for ln in lines if ln and key_re.match(ln)]
    added = 0
    skipped = 0
    from .models import SshPublicKey
    try:
        async with async_session() as sess:
            for k in candidates:
                # skip duplicates for this user
                exists = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == current_user.id, SshPublicKey.public_key == k))
                if exists.first():
                    skipped += 1
                    continue
                comment = None
                parts = k.split(' ', 2)
                if len(parts) == 3:
                    comment = parts[2]
                row = SshPublicKey(user_id=current_user.id, public_key=k, comment=comment)
                sess.add(row)
                added += 1
            await sess.commit()
        msg = f"Saved {added} key(s)" + (f", skipped {skipped}" if skipped else '')
    except Exception as e:
        msg = f"Error saving keys: {e}"
    # Prepare response context
    client_tz = await get_session_timezone(request)
    next_csrf = create_csrf_token(current_user.username)
    # reload user's keys
    keys: list[dict] = []
    try:
        async with async_session() as sess:
            res = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == current_user.id).order_by(SshPublicKey.created_at.desc()))
            rows = res.all()
            for r in rows:
                keys.append({"id": r.id, "comment": r.comment, "public_key": r.public_key, "enabled": r.enabled, "created_at": r.created_at})
    except Exception:
        keys = []
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'ssh_enabled': bool(current_user.is_admin or allow_all), 'ssh_message': msg, 'ssh_keys': keys, 'csrf_token': next_csrf})
    return TEMPLATES.TemplateResponse(request, 'repl.html', {
        "request": request,
        "client_tz": client_tz,
        "csrf_token": next_csrf,
        "ssh_enabled": bool(current_user.is_admin or allow_all),
        "ssh_message": msg,
        "ssh_keys": keys,
    })



@app.post('/html_no_js/repl/ssh_keys/delete', response_class=HTMLResponse)
async def html_repl_ssh_keys_delete(request: Request, key_id: int = Form(...), current_user: User = Depends(require_login)):
    # CSRF
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token, create_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    allow_all = os.getenv('ALLOW_SSH_KEYS_FOR_ALL', '0').lower() in ('1', 'true', 'yes')
    if not (current_user.is_admin or allow_all):
        raise HTTPException(status_code=403, detail='forbidden')
    from .models import SshPublicKey
    # delete if owned by user
    deleted = False
    try:
        async with async_session() as sess:
            res = await sess.exec(select(SshPublicKey).where(SshPublicKey.id == key_id))
            row = res.first()
            if row and row.user_id == current_user.id:
                await sess.delete(row)
                await sess.commit()
                deleted = True
    except Exception:
        deleted = False
    msg = "Deleted key" if deleted else "Key not found or not yours"
    # Prepare response context
    client_tz = await get_session_timezone(request)
    next_csrf = create_csrf_token(current_user.username)
    # reload user's keys
    keys: list[dict] = []
    try:
        async with async_session() as sess:
            res = await sess.exec(select(SshPublicKey).where(SshPublicKey.user_id == current_user.id).order_by(SshPublicKey.created_at.desc()))
            rows = res.all()
            for r in rows:
                keys.append({"id": r.id, "comment": r.comment, "public_key": r.public_key, "enabled": r.enabled, "created_at": r.created_at})
    except Exception:
        keys = []
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': deleted, 'message': msg, 'ssh_keys': keys})

    return TEMPLATES.TemplateResponse(request, 'repl.html', {
        "request": request,
        "client_tz": client_tz,
        "csrf_token": next_csrf,
        "ssh_enabled": bool(current_user.is_admin or allow_all),
        "ssh_message": msg,
        "ssh_keys": keys,
    })


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


# CSRF refresh endpoint: requires login and issues a new csrf_token cookie.
@app.post('/csrf/refresh')
async def csrf_refresh(current_user: User = Depends(require_login)):
    from fastapi.responses import JSONResponse
    resp = JSONResponse({'ok': True})
    try:
        resp.delete_cookie('csrf_token', path='/')
        resp.delete_cookie('csrf_token', path='/html_no_js')
        csrf_assert(True, 'csrf_cookie_cleared', source='refresh', paths=['/', '/html_no_js'])
    except Exception:
        pass
    _issue_csrf_cookie(resp, getattr(current_user, 'username', None))
    try:
        # After refresh, record a checkpoint that a new token was issued for user
        csrf_assert(True, 'csrf_refresh_issued', user=getattr(current_user, 'username', None))
    except Exception:
        pass
    return resp


# CSRF refresh middleware: refresh CSRF cookie for authenticated sessions when
# missing or expiring within threshold seconds (default 5 minutes).
from starlette.middleware.base import BaseHTTPMiddleware


class _CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, threshold_seconds: int = 300):
        super().__init__(app)
        self.threshold_seconds = threshold_seconds

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            token = request.cookies.get('csrf_token')
            try:
                user = await get_current_user(request=request)
            except Exception:
                user = None
            if user and getattr(user, 'username', None):
                need_issue = False
                remaining = None
                if not token:
                    need_issue = True
                else:
                    try:
                        import base64
                        import json
                        import datetime
                        parts = token.split('.')
                        if len(parts) >= 2:
                            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                            exp = payload.get('exp')
                            if isinstance(exp, int):
                                now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                                remaining = exp - now_ts
                                # Refresh if near expiry OR token lifetime exceeds configured seconds (config changed)
                                if remaining < self.threshold_seconds or remaining > (CSRF_TOKEN_EXPIRE_SECONDS + 30):
                                    need_issue = True
                        # Assert that any observed token remaining isn't absurdly high (when computed)
                        if remaining is not None:
                            csrf_assert(remaining <= (CSRF_TOKEN_EXPIRE_SECONDS + 3600), 'csrf_mw_remaining_reasonable', remaining=remaining, configured=CSRF_TOKEN_EXPIRE_SECONDS)
                    except Exception:
                        need_issue = True
                if need_issue:
                    _issue_csrf_cookie(response, user.username)
                    try:
                        # Hint clients that a refreshed CSRF cookie was set so they can retry once.
                        response.headers['X-CSRF-Refreshed'] = '1'
                        csrf_logger.info('csrf middleware: reissued csrf cookie')
                        csrf_assert(True, 'csrf_mw_refreshed', user=getattr(user, 'username', None))
                        # Also record last-issued for compatibility checks
                        try:
                            cookie_val = response.headers.get('set-cookie')
                            # We cannot reliably parse Set-Cookie header here into token; rely on _record_issued_csrf in _issue_csrf_cookie
                            pass
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            logger.exception('csrf refresh middleware failed')
        return response


# Refresh threshold: refresh when less than 1/10th of CSRF lifetime remains, min 60s, max 5 minutes.
try:
    _thresh = max(60, min(300, max(1, CSRF_TOKEN_EXPIRE_SECONDS // 10)))
except Exception:
    _thresh = 300
app.add_middleware(_CSRFMiddleware, threshold_seconds=_thresh)


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


@app.get('/__debug_echo', response_class=JSONResponse)
@app.post('/__debug_echo', response_class=JSONResponse)
async def __debug_echo(request: Request):
    """Debug endpoint: returns request headers, cookies and client connection info.

    Useful when debugging proxies/TLS terminators to see what the FastAPI app actually
    receives. Keep this route minimal and safe (no side-effects).
    """
    try:
        headers = {k: v for k, v in request.headers.items()}
    except Exception:
        headers = {}
    try:
        cookies = {k: v for k, v in request.cookies.items()}
    except Exception:
        cookies = {}
    # client info available in ASGI scope
    client_info = None
    try:
        client = request.client
        if client:
            client_info = {"host": client.host, "port": client.port}
    except Exception:
        client_info = None

    # Common headers that proxies/terminators may set for client certs
    forwarded_client_cert = headers.get('x-forwarded-client-cert') or headers.get('x-ssl-client-cert') or headers.get('x-ssl-client-verify')

    payload = {
        "headers": headers,
        "cookies": cookies,
        "client": client_info,
        "forwarded_client_cert": forwarded_client_cert,
    }
    return JSONResponse(payload)



@app.get('/html_tailwind/search', response_class=JSONResponse)
async def html_tailwind_search(request: Request):
    """JSON search API for the Tailwind client. Mirrors /html_no_js/search logic but returns JSON."""
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    qparam = request.query_params.get('q', '').strip()
    include_list_todos = str(request.query_params.get('include_list_todos', '')).lower() in ('1','true','yes','on')
    if 'exclude_completed' in request.query_params:
        exclude_completed = str(request.query_params.get('exclude_completed', '')).lower() in ('1','true','yes','on')
    else:
        exclude_completed = True
    results = {'lists': [], 'todos': []}
    if qparam:
        like = f"%{qparam}%"
        try:
            search_tags = extract_hashtags(qparam)
        except Exception:
            search_tags = []
        async with async_session() as sess:
            owner_id = current_user.id
            qlists = select(ListState).where(ListState.owner_id == owner_id).where(ListState.name.ilike(like))
            rlists = await sess.exec(qlists)
            lists_by_id: dict[int, ListState] = {l.id: l for l in rlists.all()}
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
            # Build list results including priority and hashtags
            list_ids = [l.id for l in lists_by_id.values()]
            list_tags_map: dict[int, list[str]] = {}
            if list_ids:
                try:
                    qlt = (
                        select(ListHashtag.list_id, Hashtag.tag)
                        .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                        .where(ListHashtag.list_id.in_(list_ids))
                    )
                    for row in (await sess.exec(qlt)).all():
                        # row may be (list_id, tag) or a single object depending on driver
                        if isinstance(row, (tuple, list)) and len(row) >= 2:
                            lid, tag = row[0], row[1]
                        else:
                            # fallback: try to attribute access
                            try:
                                lid = row.list_id
                                tag = row.tag
                            except Exception:
                                continue
                        list_tags_map.setdefault(int(lid), []).append(tag)
                except Exception:
                    list_tags_map = {}

            results['lists'] = [
                {
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'priority': getattr(l, 'priority', None),
                    'tags': sorted(list_tags_map.get(int(l.id), [])) if list_tags_map else [],
                }
                for l in lists_by_id.values()
                if not (exclude_completed and getattr(l, 'completed', False))
            ]
            # todos
            qvis = select(ListState).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            rvis = await sess.exec(qvis)
            vis_ids = [l.id for l in rvis.all()]
            todos_acc: dict[int, Todo] = {}
            if vis_ids:
                qtodos = (
                    select(Todo)
                    .where(Todo.list_id.in_(vis_ids))
                    .where((Todo.text.ilike(like)) | (Todo.note.ilike(like)))
                    .where(Todo.search_ignored == False)
                )
                for t in (await sess.exec(qtodos)).all():
                    todos_acc.setdefault(t.id, t)
                if search_tags:
                    qth = (
                        select(Todo)
                        .join(TodoHashtag, TodoHashtag.todo_id == Todo.id)
                        .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                        .where(Todo.list_id.in_(vis_ids))
                        .where(Hashtag.tag.in_(search_tags))
                        .where(Todo.search_ignored == False)
                    )
                    for t in (await sess.exec(qth)).all():
                        todos_acc.setdefault(t.id, t)
                if include_list_todos and lists_by_id:
                    list_ids_match = list(lists_by_id.keys())
                    qall = select(Todo).where(Todo.list_id.in_(list_ids_match)).where(Todo.search_ignored == False)
                    for t in (await sess.exec(qall)).all():
                        todos_acc.setdefault(t.id, t)
                lm = {l.id: l.name for l in (await sess.scalars(select(ListState).where(ListState.id.in_(vis_ids)))).all()}
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
    return JSONResponse({'ok': True, 'q': qparam, 'results': results})


@app.post('/html_tailwind/lists', response_class=JSONResponse)
async def html_tailwind_create_list(request: Request):
    """Create a list via JSON for the Tailwind client. Reuses create_list logic.
    Accepts JSON body {name: string, hashtags?: [...], category_id?: int, parent_list_id?: int} and returns created list id/name.
    """
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        raise HTTPException(status_code=401, detail='authentication required')

    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get('name') or request.query_params.get('name')
    if not name:
        raise HTTPException(status_code=400, detail='name is required')
    
    # Extract parent_list_id from JSON body
    parent_list_id = body.get('parent_list_id')
    if parent_list_id is not None:
        try:
            parent_list_id = int(parent_list_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail='parent_list_id must be an integer')
    
    # Optional metadata
    metadata = body.get('metadata') if isinstance(body, dict) else None

    # emulate form/query behavior of create_list; pass metadata via request.state
    try:
        request.state._list_metadata = metadata
    except Exception:
        pass
    # call existing create_list helper
    new_list = await create_list(request, name=name, current_user=current_user, parent_list_id=parent_list_id)
    payload = {'ok': True}
    try:
        if new_list is not None:
            payload.update({'id': getattr(new_list, 'id', None), 'name': getattr(new_list, 'name', None), 'category_id': getattr(new_list, 'category_id', None)})
    except Exception:
        pass
    return JSONResponse(payload)


@app.post("/lists")
async def create_list(request: Request, name: str = Form(None), current_user: User = Depends(require_login), parent_list_id: int = None):
    # Accept name from form (normal HTML/PWA) or fallback to query params so
    # test clients that post with `params={'name': ...}` continue to work.
    if not name:
        name = request.query_params.get('name')
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    # Preserve the original submitted name so we can extract hashtags from it
    # before we remove them for the saved list name. This ensures clients that
    # POST via fetch/JSON or via URLSearchParams still have their typed
    # hashtags captured and synced to the list-level tags.
    original_submitted_name = name
    # strip leading whitespace and remove inlined hashtags from saved name
    name = remove_hashtags_from_text(name.lstrip())
    async with async_session() as sess:
        # Always create a new list row for the authenticated user. We allow
        # duplicate names per user (multiple lists with the same name).
        owner_id = current_user.id
        # Optional metadata: prefer request.state (JSON callers), else form field 'metadata' (stringified JSON), else none
        list_metadata = None
        try:
            list_metadata = getattr(request.state, '_list_metadata', None)
        except Exception:
            list_metadata = None
        if list_metadata is None:
            try:
                form2 = await request.form()
            except Exception:
                form2 = {}
            if form2 and form2.get('metadata') is not None:
                list_metadata = form2.get('metadata')
        # Accept explicit category_id from form or query params; prefer it over user's default
        try:
            form = await request.form()
        except Exception:
            form = {}
        raw_cat = None
        if form and form.get('category_id') is not None:
            raw_cat = form.get('category_id')
        elif request.query_params.get('category_id') is not None:
            raw_cat = request.query_params.get('category_id')
        cid = None
        if raw_cat is not None and str(raw_cat).strip() != '' and str(raw_cat).strip() != '-1':
            try:
                cid = int(str(raw_cat))
            except Exception:
                cid = None
        if cid is None:
            default_cat = getattr(current_user, 'default_category_id', None)
            cid = default_cat if default_cat is not None else None
        # Validate category ownership if a category_id is provided
        if cid is not None:
            try:
                cobj = await sess.get(Category, int(cid))
                if not cobj or getattr(cobj, 'owner_id', None) != current_user.id:
                    raise HTTPException(status_code=403, detail='invalid category')
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=400, detail='invalid category')
        
        # Handle parent_list_id validation
        if parent_list_id is not None:
            # Verify the parent list exists and belongs to the user
            parent_list = await sess.get(ListState, parent_list_id)
            if not parent_list or parent_list.owner_id != current_user.id:
                raise HTTPException(status_code=404, detail='parent list not found')
        
        # validate/encode metadata
        meta_col = None
        try:
            meta_col = validate_metadata_for_storage(list_metadata)
        except Exception:
            meta_col = None

        lst = ListState(name=name, owner_id=owner_id, category_id=cid, parent_list_id=parent_list_id, metadata_json=meta_col)
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
        # Allow clients to explicitly provide hashtags via a form/query param named
        # `hashtags`. Accept either a JSON array, comma-separated string, or space
        # separated list. If not provided, fall back to extracting hashtags from
        # the original submitted name (preserves prior behavior).
        try:
            form = await request.form()
        except Exception:
            form = {}
        tags = []
        try:
            explicit = None
            if form and form.get('hashtags'):
                explicit = form.get('hashtags')
            elif request.query_params.get('hashtags'):
                explicit = request.query_params.get('hashtags')

            if explicit:
                # try JSON array first
                try:
                    import json
                    parsed = json.loads(explicit)
                    if isinstance(parsed, (list, tuple)):
                        cand = [str(x) for x in parsed if x]
                    else:
                        # fallback to string processing
                        raise ValueError('not an array')
                except Exception:
                    # split on commas or whitespace
                    cand = [s for s in re.split(r'[,\s]+', explicit) if s]
                # normalize and deduplicate
                seen = set()
                for c in cand:
                    try:
                        # allow clients to pass tags with or without leading '#'
                        norm = normalize_hashtag(c if c.startswith('#') else ('#' + c))
                    except Exception:
                        continue
                    if norm not in seen:
                        seen.add(norm)
                        tags.append(norm)
            else:
                source_for_tags = request.query_params.get('name') or original_submitted_name or ''
                tags = extract_hashtags(source_for_tags)
        except Exception:
            tags = []
        # Persist extracted or explicit tags to the created list
        try:
            if tags:
                # open a new session and sync tags for the created list
                async with async_session() as sess2:
                    await _sync_list_hashtags(sess2, lst.id, tags)
        except Exception:
            # best-effort: tagging shouldn't block list creation
            pass
    # return the created list object (API clients expect the new list)
    return lst


@app.get("/lists")
async def list_lists(current_user: User = Depends(require_login)):
    async with async_session() as sess:
        owner_id = current_user.id if current_user else None
        res = await sess.exec(select(ListState).where(ListState.owner_id == owner_id))
        rows = res.all()
        # Serialize with metadata for consistency with client JSON API
        try:
            return [
                {
                    'id': l.id,
                    'name': l.name,
                    'owner_id': l.owner_id,
                    'category_id': getattr(l, 'category_id', None),
                    'parent_list_id': getattr(l, 'parent_list_id', None),
                    'parent_todo_id': getattr(l, 'parent_todo_id', None),
                    'created_at': (l.created_at.isoformat() if getattr(l, 'created_at', None) else None),
                    'modified_at': (l.modified_at.isoformat() if getattr(l, 'modified_at', None) else None),
                    'metadata': parse_metadata_json(getattr(l, 'metadata_json', None)),
                }
                for l in rows
            ]
        except Exception:
            return rows


@app.get('/html_no_js/priorities')
async def html_priorities(request: Request, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        # determine whether to hide completed items (cookie defaults to on)
        hide_completed_cookie = request.cookies.get('priorities_hide_completed')
        hide_completed = True if hide_completed_cookie is None else (hide_completed_cookie == '1')

        # lists with priority (optionally exclude completed)
        ql_stmt = select(ListState).where(ListState.owner_id == current_user.id).where(ListState.priority != None)
        if hide_completed:
            ql_stmt = ql_stmt.where(ListState.completed == False)
        ql = await sess.exec(ql_stmt)
        lists = ql.all()
        # todos with priority: fetch todos that have a priority (and optionally exclude completed)
        qt2_stmt = select(Todo).where(Todo.priority != None)
        # do not attempt to filter by a non-existent Todo.completed column here;
        # some backends represent todo completion via TodoCompletion rows.
        qt2 = await sess.exec(qt2_stmt)
        todos = []
        for t in qt2.all():
            ql2 = await sess.exec(select(ListState).where(ListState.id == t.list_id))
            lst = ql2.first()
            if not lst:
                continue
            if lst.owner_id is None or lst.owner_id == current_user.id:
                # If hide_completed is requested, skip todos that have any
                # completion rows marked done=True.
                if hide_completed:
                    qc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == t.id).where(TodoCompletion.done == True))
                    if qc.first():
                        continue
                todos.append((t, lst))
        # sort lists and todos by priority descending
        # completed items should not affect priority ordering: treat their priority as None
        def _list_priority_key(l):
            p = getattr(l, 'priority', None)
            if getattr(l, 'completed', False):
                p = None
            return p if p is not None else -999

        def _todo_priority_key(tl):
            t = tl[0]
            p = getattr(t, 'priority', None)
            # detect completed by checking TodoCompletion rows was done earlier; here prefer to treat attribute 'completed' on the joined list tuple if available
            # when rendering priorities view we don't have per-todo 'completed' flag on the Todo object, so assume not completed (server-side filtering handled hide_completed)
            return p if p is not None else -999

        lists_sorted = sorted(lists, key=_list_priority_key, reverse=True)
        # Build lightweight list view models (avoid mutating ORM objects)
        lists_vm = [
            {
                'id': getattr(l, 'id', None),
                'name': getattr(l, 'name', ''),
                'priority': getattr(l, 'priority', None),
                'completed': bool(getattr(l, 'completed', False)),
                'modified_at': getattr(l, 'modified_at', None),
                'created_at': getattr(l, 'created_at', None),
            }
            for l in lists_sorted
        ]
        todos_sorted = sorted(todos, key=_todo_priority_key, reverse=True)
        # Compute completion flags for prioritised todos (without mutating ORM objects)
        prior_ids = [getattr(tl[0], 'id', None) for tl in todos_sorted if getattr(tl[0], 'id', None) is not None]
        done_ids: set[int] = set()
        if prior_ids:
            try:
                res_done = await sess.exec(
                    select(TodoCompletion.todo_id).where(
                        TodoCompletion.todo_id.in_(prior_ids)
                    ).where(TodoCompletion.done == True)
                )
                rows = res_done.all()
                # rows may be a list of tuples or scalars depending on backend
                for r in rows:
                    if isinstance(r, tuple):
                        done_ids.add(r[0])
                    else:
                        try:
                            done_ids.add(int(r))
                        except Exception:
                            try:
                                done_ids.add(int(getattr(r, 'todo_id')))
                            except Exception:
                                pass
            except Exception:
                # if completion lookup fails, leave done_ids empty
                done_ids = set()
        # Additionally, fetch unprioritised todos (no per-todo priority) that are not completed
        todos_unprio = []
        qt3_stmt = select(Todo).where(Todo.priority == None)
        qt3 = await sess.exec(qt3_stmt)
        for t in qt3.all():
            ql2 = await sess.exec(select(ListState).where(ListState.id == t.list_id))
            lst = ql2.first()
            if not lst:
                continue
            if lst.owner_id is None or lst.owner_id == current_user.id:
                # skip if todo is completed (check TodoCompletion rows)
                qc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == t.id).where(TodoCompletion.done == True))
                if qc.first():
                    continue
                todos_unprio.append((t, lst))
        # Build lightweight view models that include a computed 'completed' flag without mutating ORM objects
        def _compute_completed(todo: Todo) -> bool:
            tid = getattr(todo, 'id', None)
            return bool(tid in done_ids) if tid is not None else False

        todos_vm = [({'id': t.id, 'text': t.text, 'priority': t.priority, 'completed': _compute_completed(t), 'modified_at': getattr(t, 'modified_at', None), 'created_at': getattr(t, 'created_at', None)}, l) for (t, l) in todos_sorted]
        todos_unprio_vm = [({'id': t.id, 'text': t.text, 'priority': t.priority, 'completed': False, 'modified_at': getattr(t, 'modified_at', None), 'created_at': getattr(t, 'created_at', None)}, l) for (t, l) in todos_unprio]

        # Instrumentation: log a couple of samples to verify completed flag
        try:
            if lists_vm:
                l0 = lists_vm[0]
                logger.info('DEBUG_STRIKETHROUGH: list vm sample id=%s name=%s completed=%s', l0.get('id'), l0.get('name'), l0.get('completed'))
            if todos_vm:
                t0 = todos_vm[0][0]
                logger.info('DEBUG_STRIKETHROUGH: vm sample id=%s name=%s completed=%s', t0.get('id'), t0.get('text'), t0.get('completed'))
            if todos_unprio_vm:
                u0 = todos_unprio_vm[0][0]
                logger.info('DEBUG_STRIKETHROUGH: vm unprio sample id=%s name=%s completed=%s', u0.get('id'), u0.get('text'), u0.get('completed'))
        except Exception:
            pass

        # leave todos_unprio unsorted here; client-side will sort by modified date when shown
    return TEMPLATES.TemplateResponse(request, 'priorities.html', {
        'request': request,
        'lists': lists_vm,
        'todos': todos_vm,
        'todos_unprio': todos_unprio_vm,
        'client_tz': await get_session_timezone(request)
    })


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
    # Optional profiling (env-gated via CALENDAR_PROFILE and CALENDAR_PROFILE_INCLUDE_RESPONSE)
    import os as _os
    import time as _time
    _prof_enabled = _os.getenv('CALENDAR_PROFILE', '0').lower() in ('1', 'true', 'yes')
    _prof_include_resp = _os.getenv('CALENDAR_PROFILE_INCLUDE_RESPONSE', '0').lower() in ('1', 'true', 'yes')
    _prof = {'times': {}, 'counts': {}, 'notes': {}}
    _t0_total = _time.perf_counter() if _prof_enabled else None
    def _pt(key: str):
        return _time.perf_counter() if _prof_enabled else None
    def _pa(key: str, t0):
        if not _prof_enabled or t0 is None:
            return
        try:
            dt = _time.perf_counter() - t0
            _prof['times'][key] = float(_prof['times'].get(key, 0.0)) + float(dt)
        except Exception:
            pass
    def _pc(key: str, n: int = 1):
        if not _prof_enabled:
            return
        try:
            _prof['counts'][key] = int(_prof['counts'].get(key, 0)) + int(n)
        except Exception:
            pass
    logger.info('calendar_events called owner_id=%s start=%s end=%s', owner_id, start, end)
    start_dt = _parse_iso_to_utc(start)
    end_dt = _parse_iso_to_utc(end)

    events: list[Dict[str, Any]] = []
    async with async_session() as sess:
        # fetch lists for this owner
        qlists = await sess.exec(select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None))
        lists = qlists.all()
        # fetch todos that belong to these lists
        if lists:
            list_ids = [l.id for l in lists if l.id is not None]
        else:
            list_ids = []
        logger.info('calendar_occurrences fetched %d lists for owner_id=%s', len(lists) if lists is not None else 0, owner_id)

        todos = []
        if list_ids:
            qtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)).where(Todo.calendar_ignored == False))
            todos = qtodos.all()
        logger.info('calendar_occurrences fetched %d todos for owner_id=%s', len(todos) if todos is not None else 0, owner_id)

        # helper to filter by optional window
        def in_window(dt: datetime) -> bool:
            if start_dt and dt < start_dt:
                return False
            if end_dt and dt > end_dt:
                return False
            return True

        # scan lists
        for l in lists:
            texts = [l.name or '']
            # also consider hashtags (joined) in case dates are in tags
            try:
                tags = getattr(l, 'hashtags', None)
                if tags:
                    for ht in tags:
                        try:
                            tg = getattr(ht, 'tag', None)
                            if tg:
                                texts.append(f"#{tg}")
                        except Exception:
                            pass
            except Exception:
                # if relationship not loaded or any error, ignore
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
    # Optional profiling (env-gated via CALENDAR_PROFILE and CALENDAR_PROFILE_INCLUDE_RESPONSE)
    import os as _os
    import time as _time
    _prof_enabled = _os.getenv('CALENDAR_PROFILE', '0').lower() in ('1', 'true', 'yes')
    _prof_include_resp = _os.getenv('CALENDAR_PROFILE_INCLUDE_RESPONSE', '0').lower() in ('1', 'true', 'yes')
    _prof = {'times': {}, 'counts': {}, 'notes': {}}
    _t0_total = _time.perf_counter() if _prof_enabled else None
    def _pt(key: str):
        return _time.perf_counter() if _prof_enabled else None
    def _pa(key: str, t0):
        if not _prof_enabled or t0 is None:
            return
        try:
            dt = _time.perf_counter() - t0
            _prof['times'][key] = float(_prof['times'].get(key, 0.0)) + float(dt)
        except Exception:
            pass
    def _pc(key: str, n: int = 1):
        if not _prof_enabled:
            return
        try:
            _prof['counts'][key] = int(_prof['counts'].get(key, 0)) + int(n)
        except Exception:
            pass
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
    # Instrumentation: counters for how occurrences are derived before filtering.
    # We classify sources by branch (e.g., todo-rrule, list-explicit, todo-yearless, etc.)
    counts_by_source_pre: dict[str, int] = {}
    agg_pre: dict[str, int] = {'rrule': 0, 'text': 0}

    def _inc_counter(d: dict, k: str, n: int = 1):
        try:
            d[k] = int(d.get(k, 0)) + int(n)
        except Exception:
            # defensive fallback
            d[k] = (d.get(k, 0) or 0) + 1
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
    t_fetch_lists = _pt('fetch_lists')
    async with async_session() as sess:
        # fetch lists for this owner
        qlists = await sess.exec(select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None))
        lists = qlists.all()
        _pa('fetch_lists', t_fetch_lists)
        _pc('lists_count', len(lists) if lists else 0)
        _sse_debug('calendar_occurrences.lists_fetched', {'count': len(lists) if lists else 0})
        if lists:
            list_ids = [l.id for l in lists if l.id is not None]
        else:
            list_ids = []

        todos = []
        if list_ids:
            t_fetch_todos = _pt('fetch_todos')
            qtodos = await sess.exec(select(Todo).where(Todo.list_id.in_(list_ids)).where(Todo.calendar_ignored == False))
            todos = qtodos.all()
            _pa('fetch_todos', t_fetch_todos)
        _sse_debug('calendar_occurrences.todos_fetched', {'count': len(todos)})
        _pc('todos_count', len(todos))
        # Build helper maps for todos and lists and compute per-list override priorities
        t_build_maps = _pt('build_maps')
        todo_map: dict[int, object] = {}
        try:
            for t in todos:
                if getattr(t, 'id', None) is not None:
                    todo_map[int(t.id)] = t
        except Exception:
            todo_map = {}
        # compute completed todo ids to exclude when computing override priorities
        completed_ids = set()
        try:
            todo_ids = [int(getattr(t, 'id')) for t in todos if getattr(t, 'id', None) is not None]
            if todo_ids:
                qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                cres = await sess.exec(qcomp)
                completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
        except Exception:
            completed_ids = set()
        # per-list highest uncompleted todo priority
        list_override_map: dict[int, int] = {}
        try:
            for t in todos:
                try:
                    lid = getattr(t, 'list_id', None)
                    if lid is None:
                        continue
                    tid = getattr(t, 'id', None)
                    if tid in completed_ids:
                        continue
                    pri = getattr(t, 'priority', None)
                    if pri is None:
                        continue
                    try:
                        pv = int(pri)
                    except Exception:
                        continue
                    cur = list_override_map.get(lid)
                    if cur is None or pv > cur:
                        list_override_map[lid] = pv
                except Exception:
                    continue
        except Exception:
            list_override_map = {}
        try:
            # Avoid constructing large log payloads unless DEBUG is enabled
            if logger.isEnabledFor(logging.DEBUG):
                _dbg_rows = [
                    (getattr(tt, 'id', None), (getattr(tt, 'text', None) or '')[:40],
                     (getattr(tt, 'created_at', None).isoformat() if getattr(tt, 'created_at', None) and getattr(tt, 'created_at', None).tzinfo else str(getattr(tt, 'created_at', None))))
                    for tt in todos
                ]
                logger.debug('calendar_occurrences.fetched_todos %s', _dbg_rows)
        except Exception:
            pass
        _pa('build_maps', t_build_maps)

        def add_occ(item_type: str, item_id: int, list_id: int | None, title: str, occ_dt, dtstart, is_rec, rrule_str, rec_meta, source: str | None = None):
            nonlocal occurrences, truncated, counts_by_source_pre, agg_pre
            if len(occurrences) >= max_total:
                # global truncation reached
                try:
                    _sse_debug('calendar_occurrences.truncated', {'when': 'max_total', 'item_type': item_type, 'item_id': item_id, 'current_total': len(occurrences)})
                except Exception:
                    pass
                try:
                    # Emit an explicit INFO log so truncation events (and offending item ids)
                    # are visible in pytest-captured server output for debugging.
                    import traceback as _tb
                    logger.info('DEBUG_TRUNCATION_TRIGGER item_type=%s item_id=%s current_total=%s', item_type, item_id, len(occurrences))
                    logger.debug('DEBUG_TRUNCATION_STACK:\n%s', ''.join(_tb.format_stack(limit=6)))
                except Exception:
                    pass
                truncated = True
                return
            # compute occurrence hash for client/server idempotency
            from .utils import occurrence_hash
            occ_hash = occurrence_hash(item_type, item_id, occ_dt, rrule_str or '', title)
            # precompute a numeric timestamp to avoid repeated ISO parsing during sort
            try:
                _od = occ_dt
                if hasattr(_od, 'tzinfo') and _od.tzinfo is None:
                    _od = _od.replace(tzinfo=timezone.utc)
                _occ_ts = int(_od.timestamp())
            except Exception:
                _occ_ts = 0
            occ_record = {
                'occurrence_dt': occ_dt.isoformat(),
                # date-only string for UI (YYYY-MM-DD)
                'occurrence_date': (occ_dt.date().isoformat() if hasattr(occ_dt, 'date') else (occ_dt.isoformat().split('T')[0] if isinstance(occ_dt, str) else None)),
                'item_type': item_type,
                'id': item_id,
                'list_id': list_id,
                'title': title,
                'dtstart': dtstart.isoformat() if dtstart is not None else None,
                'is_recurring': bool(is_rec),
                'rrule': rrule_str or '',
                'recurrence_meta': rec_meta,
                'occ_hash': occ_hash,
                'occ_ts': _occ_ts,
                'source': source or None,
                # effective priority: for todos use todo.priority, for lists use max(list.priority, highest uncompleted todo priority in that list)
                'effective_priority': None,
            }
            occurrences.append(occ_record)
            # Update instrumentation counters based on source classification
            try:
                _src = source or 'unknown'
                _inc_counter(counts_by_source_pre, _src, 1)
                if _src in ('list-rrule', 'todo-rrule', 'todo-inline-rrule'):
                    _inc_counter(agg_pre, 'rrule', 1)
                else:
                    _inc_counter(agg_pre, 'text', 1)
            except Exception:
                pass
            # Emit an SSE debug event so callers can see which occurrences were added
            try:
                pay = {'item_type': item_type, 'item_id': item_id, 'occurrence_dt': occ_dt.isoformat(), 'title': title or '', 'rrule': rrule_str or '', 'is_recurring': bool(is_rec)}
                if source:
                    pay['source'] = source
                _sse_debug('calendar_occurrences.added', pay)
                # Also emit an INFO log so appended occurrences are visible in server stdout and in /server/logs
                try:
                    # include title to make it easier to correlate occurrences
                    # include occ_hash for easier tracing when filtering occurs later
                    logger.info('calendar_occurrences.added owner_id=%s item_type=%s item_id=%s title=%s occurrence=%s rrule=%s recurring=%s source=%s occ_hash=%s', owner_id, item_type, item_id, (title or '')[:60], occ_dt.isoformat(), rrule_str or '', bool(is_rec), source, pay.get('occ_hash'))
                except Exception:
                    pass
                # Guarded retention debug: when DEBUG_RETENTION_ID is set to an item id,
                # emit an explicit debug log if this occurrence belongs to that id.
                try:
                    _ret_id = os.environ.get('DEBUG_RETENTION_ID')
                    _ret_any = os.environ.get('DEBUG_RETENTION_ANY_WINDOWEVENT')
                    if (_ret_id and str(item_id) == str(_ret_id)) or (_ret_any and title and 'WindowEvent Jan 22' in title):
                        # include a short stack to make it easy to see call-site in tests
                        import traceback as _tb
                        logger.info('DEBUG_RETENTION_MATCH item_type=%s item_id=%s title=%s occurrence=%s source=%s', item_type, item_id, (title or '')[:80], occ_dt.isoformat(), source)
                        logger.debug('DEBUG_RETENTION_STACK:\n%s', ''.join(_tb.format_stack(limit=6)))
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
        t_scan_lists = _pt('scan_lists')
        for l in lists:
            if truncated:
                break
            texts = [l.name or '']
            # Low-risk perf: avoid lazy-loading hashtags per list to prevent N+1 queries.
            # If date extraction from tags is desired in the future, prefetch them in a single query.
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
                    _t = _pt('list_rrule_between')
                    occs = list(r.between(start_dt, end_dt, inc=True))[:max_per_item]
                    _pa('list_rrule_between', _t)
                    # signal when per-item limit reached
                    try:
                        if len(occs) >= max_per_item:
                            _sse_debug('calendar_occurrences.per_item_limit', {'when': 'list-rrule', 'list_id': l.id, 'limit': max_per_item})
                    except Exception:
                        pass
                    for od in occs:
                        add_occ('list', l.id, None, l.name, od, rec_dtstart, True, rec_rrule, getattr(l, 'recurrence_meta', None), source='list-rrule')
                        if truncated:
                            break
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
            _t = _pt('extract_meta_lists')
            meta = extract_dates_meta(combined)
            _pa('extract_meta_lists', _t)
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
                    if truncated:
                        break
                    mon = int(m.get('month'))
                    day = int(m.get('day'))
                    for y in ys:
                        try:
                            from datetime import datetime as _dt
                            cand = _dt(y, mon, day, tzinfo=timezone.utc)
                        except Exception:
                            # invalid date (e.g., Feb 29 on non-leap year)
                            continue
                        # include candidate if it falls inside the allowed window
                        # or if it is on the same calendar date as the list's
                        # creation date (handle lists created later the same day)
                        same_calendar_date_as_created = False
                        try:
                            if item_created and cand.date() == item_created.date():
                                same_calendar_date_as_created = True
                        except Exception:
                            pass
                        if (cand >= allowed_start and cand <= allowed_end) or (same_calendar_date_as_created and cand >= start_dt and cand <= allowed_end):
                            add_occ('list', l.id, None, l.name, cand, None, False, '', None, source='list-yearless')
                            if truncated:
                                break

        # close profiling timer for scanning lists
        _pa('scan_lists', t_scan_lists)

        # scan todos
        t_scan_todos = _pt('scan_todos')
        for t in todos:
            if truncated:
                break
            # Refresh the todo from the current session to pick up any recent
            # commits (tests may update created_at shortly before calling this
            # handler). This avoids using a stale object from a different session
            # snapshot.
            # Low-risk perf: skip per-todo refresh to avoid N+1 queries; rely on
            # the initial batch load. If specific fields require freshness,
            # consider a single batched refresh earlier.
            texts = [t.text or '']
            if getattr(t, 'note', None):
                # Cap note length for parsing to avoid excessive CPU on large notes
                try:
                    texts.append((t.note or '')[:8192])
                except Exception:
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
                    _t = _pt('todo_rrule_between')
                    occs = list(r.between(start_dt, end_dt, inc=True))[:max_per_item]
                    _pa('todo_rrule_between', _t)
                    for od in occs:
                        add_occ('todo', t.id, t.list_id, t.text, od, rec_dtstart, True, rec_rrule, getattr(t, 'recurrence_meta', None), source='todo-rrule')
                        if truncated:
                            break
                    continue
                except Exception:
                    pass
            # If no persisted recurrence, attempt to parse an inline recurrence phrase
            # If recurring detection is disabled, skip inline recurrence parsing
            if expand and not rec_rrule and recurring_enabled:
                # Cheap keyword prefilter to avoid running the expensive inline recurrence parser
                # on todos that clearly don't contain recurrence language.
                def _likely_inline_rrule_text(_s: str) -> bool:
                    try:
                        s = (_s or '').lower()
                        # Expanded but still conservative keyword set for recurrence phrases.
                        keywords = [
                            # high-signal general terms
                            'every ', 'each ', 'daily', 'weekly', 'monthly', 'yearly', 'annually',
                            'biweekly', 'bi-weekly', 'fortnight', 'fortnightly', 'bimonthly', 'bi-monthly',
                            'repeat', 'repeats', 'repeating', 'rrule', 'until ', 'byweekday', 'byday',
                            'weekdays', 'weekend', 'every other', 'every 2', 'every two', 'every second',
                            # weekday names (with preceding space to reduce false positives inside words)
                            ' on monday', ' on tuesday', ' on wednesday', ' on thursday', ' on friday', ' on saturday', ' on sunday',
                            ' mondays', ' tuesdays', ' wednesdays', ' thursdays', ' fridays', ' saturdays', ' sundays',
                            # frequency units commonly used in natural language
                            ' per day', ' per week', ' per month', ' per year'
                        ]
                        return any(k in s for k in keywords)
                    except Exception:
                        return False
                if _likely_inline_rrule_text(combined):
                    try:
                        # parse_text_to_rrule returns (rrule_obj, dtstart)
                        from .utils import parse_text_to_rrule, parse_text_to_rrule_string
                        _t = _pt('todo_inline_parse')
                        r_obj, dtstart = parse_text_to_rrule(combined)
                        _pa('todo_inline_parse', _t)
                        if r_obj is not None and dtstart is not None:
                            try:
                                _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-inline-rrule'})
                            except Exception:
                                pass
                            if dtstart.tzinfo is None:
                                dtstart = dtstart.replace(tzinfo=timezone.utc)
                            # build rrule string for reporting
                            _dt, rrule_str_local = parse_text_to_rrule_string(combined)
                            _t = _pt('todo_inline_between')
                            occs = list(r_obj.between(start_dt, end_dt, inc=True))[:max_per_item]
                            _pa('todo_inline_between', _t)
                            try:
                                if len(occs) >= max_per_item:
                                    _sse_debug('calendar_occurrences.per_item_limit', {'when': 'todo-inline-rrule', 'todo_id': t.id, 'limit': max_per_item})
                            except Exception:
                                pass
                            for od in occs:
                                add_occ('todo', t.id, t.list_id, t.text, od, dtstart, True, rrule_str_local, None, source='todo-inline-rrule')
                                if truncated:
                                    break
                            continue
                    except Exception as e:
                        logger.exception('inline recurrence expansion failed')
                        try:
                            _sse_debug('calendar_occurrences.inline_rrule_parse_failed', {'todo_id': t.id, 'error': str(e)})
                        except Exception:
                            pass
                else:
                    # Emit a debug signal to indicate we skipped inline parse due to prefilter
                    try:
                        _sse_debug('calendar_occurrences.inline_rrule_skipped', {'todo_id': t.id, 'reason': 'keyword_prefilter'})
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
            try:
                if CALENDAR_VERBOSE_DEBUG and logger.isEnabledFor(logging.DEBUG):
                    logger.debug('calendar_occurrences.todo.inspect id=%s title=%s created_at=%s', getattr(t, 'id', None), (getattr(t, 'text', '') or '')[:60], (ca.isoformat() if isinstance(ca, datetime) else str(ca)))
            except Exception:
                pass
            # Lightweight instrumentation: emit a clear debug marker when we
            # encounter todos with the literal text 'WindowEvent' so test runs
            # produce an easy-to-find log line. This is safe for local debug and
            # can be removed after diagnosis.
            try:
                if getattr(t, 'text', None) and 'WindowEvent' in getattr(t, 'text'):
                    logger.info('DEBUG_WINDOWEVENT_MARKER todo_id=%s title=%s created_at=%s', getattr(t, 'id', None), (getattr(t, 'text', '') or '')[:120], (ca.isoformat() if isinstance(ca, datetime) else str(ca)))
            except Exception:
                pass
            # Cheap pre-regex gate: only run extract_dates_meta if text likely contains date tokens
            def _likely_has_date_tokens(_s: str) -> bool:
                try:
                    import re as _re
                    s = _s or ''
                    # numeric date shapes like 12/9 or 12-9-2025
                    if _re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", s):
                        return True
                    # month names (short or long)
                    if _re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", s, flags=_re.IGNORECASE):
                        return True
                    # weekday names
                    if _re.search(r"\b(?:mon|tue|wed|thu|fri|sat|sun)(?:day|days)?\b", s, flags=_re.IGNORECASE):
                        return True
                    # ordinal day tokens (1st, 22nd)
                    if _re.search(r"\b\d{1,2}(?:st|nd|rd|th)\b", s, flags=_re.IGNORECASE):
                        return True
                    # relative date phrases (in 2 days, next week)
                    if _re.search(r"\b(?:in\s+\d+\s+(?:day|days|week|weeks|month|months|year|years)|next\s+(?:week|month|year|mon|tue|wed|thu|fri|sat|sun))\b", s, flags=_re.IGNORECASE):
                        return True
                    return False
                except Exception:
                    return True  # fail-open to avoid hiding dates on error

            meta = []
            # Negative cache gate: if metadata_json has a matching hash with found_any=false, skip extraction
            _neg_cache_enabled = str(os.environ.get('CALENDAR_NEG_CACHE', '1')).lower() in ('1','true','yes')
            _neg_cache_write = str(os.environ.get('CALENDAR_NEG_CACHE_WRITE', '0')).lower() in ('1','true','yes')
            _neg_cache_commit = str(os.environ.get('CALENDAR_NEG_CACHE_COMMIT', '0')).lower() in ('1','true','yes')
            _cache_hit_skip = False
            _combined_hash = None
            try:
                import hashlib as _hashlib
                _combined_hash = 'sha1:' + _hashlib.sha1((combined or '').encode('utf-8', errors='ignore')).hexdigest()
            except Exception:
                _combined_hash = None

            _cache_dict = None
            if _neg_cache_enabled and _combined_hash is not None:
                try:
                    import json as _json
                    raw = getattr(t, 'metadata_json', None)
                    md = _json.loads(raw) if raw else {}
                    if isinstance(md, dict):
                        cd = md.get('calendar_extract_cache')
                        if isinstance(cd, dict) and cd.get('v') == 1 and cd.get('text_hash') == _combined_hash and (cd.get('found_any') is False):
                            _cache_dict = cd
                            _cache_hit_skip = True
                except Exception:
                    _cache_hit_skip = False

            if _cache_hit_skip:
                # Skip extraction entirely due to negative cache
                try:
                    _sse_debug('calendar_occurrences.todo.date_extract_skipped', {'todo_id': t.id, 'reason': 'neg_cache'})
                except Exception:
                    pass
            elif _likely_has_date_tokens(combined):
                _t = _pt('extract_meta_todos')
                meta = extract_dates_meta(combined)
                _pa('extract_meta_todos', _t)
                # Opportunistically update cache
                if _neg_cache_enabled and _combined_hash is not None and _neg_cache_write:
                    try:
                        import json as _json
                        raw = getattr(t, 'metadata_json', None)
                        md = _json.loads(raw) if raw else {}
                        if not isinstance(md, dict):
                            md = {}
                        md['calendar_extract_cache'] = {
                            'v': 1,
                            'text_hash': _combined_hash,
                            'found_any': bool(meta and len(meta) > 0),
                            'checked_at': now_utc().isoformat(),
                        }
                        t.metadata_json = _json.dumps(md)
                        try:
                            sess.add(t)
                            if _neg_cache_commit:
                                await sess.commit()
                            else:
                                await sess.flush()
                        except Exception:
                            pass
                    except Exception:
                        pass
            else:
                try:
                    _sse_debug('calendar_occurrences.todo.date_extract_skipped', {'todo_id': t.id, 'reason': 'regex_prefilter'})
                except Exception:
                    pass
            # collect explicit dates for this todo
            dates: list[datetime] = []
            try:
                # prepare JSON-friendly summary of meta
                meta_summary = []
                for m in meta:
                    dd = m.get('dt')
                    meta_summary.append({'year_explicit': bool(m.get('year_explicit')), 'match_text': m.get('match_text'), 'month': m.get('month'), 'day': m.get('day'), 'dt': (dd.isoformat() if isinstance(dd, datetime) else str(dd))})
                _sse_debug('calendar_occurrences.todo.meta', {'todo_id': t.id, 'meta': meta_summary})
                # Extra targeted debug logging for test diagnosis: when the
                # todo title contains 'WindowEvent' emit the meta summary and
                # the reference/created times so test runs show why a
                # candidate may or may not be produced.
                try:
                    if getattr(t, 'text', None) and 'WindowEvent' in getattr(t, 'text'):
                        logger.info('DEBUG_WINDOWEVENT_META todo_id=%s meta=%s created_at=%s', getattr(t, 'id', None), meta_summary, (ca.isoformat() if isinstance(ca, datetime) else str(ca)))
                except Exception:
                    pass
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
                if truncated:
                    break
                d = m.get('dt')
                if d >= start_dt and d <= end_dt:
                    try:
                        _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-explicit'})
                    except Exception:
                        pass
                    try:
                        if getattr(t, 'id', None) == 10017:
                            _sse_debug('calendar_occurrences.GUARDED_DEBUG', {'todo_id': t.id, 'stage': 'explicit', 'candidate': (d.isoformat() if isinstance(d, datetime) else str(d))})
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
                        try:
                            if getattr(t, 'id', None) == 10017:
                                _sse_debug('calendar_occurrences.GUARDED_DEBUG', {'todo_id': t.id, 'stage': 'deferred', 'candidate': (du.isoformat() if isinstance(du, datetime) else str(du))})
                        except Exception:
                            pass
                        add_occ('todo', t.id, t.list_id, t.text, du, None, False, '', None, source='todo-deferred')
                        if truncated:
                            pass
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
                        if truncated:
                            break
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
                                try:
                                    if getattr(t, 'id', None) == 10017:
                                        _sse_debug('calendar_occurrences.GUARDED_DEBUG', {'todo_id': t.id, 'stage': 'yearless-multi', 'candidate': cand.isoformat()})
                                except Exception:
                                    pass
                                add_occ('todo', t.id, t.list_id, t.text, cand, None, False, '', None, source='todo-yearless')
                                if truncated:
                                    break
                                try:
                                    if getattr(t, 'text', None) and 'WindowEvent' in getattr(t, 'text'):
                                        logger.info('DEBUG_WINDOWEVENT_CANDIDATE todo_id=%s candidate=%s source=%s ref_dt=%s created_at=%s', getattr(t, 'id', None), cand.isoformat(), 'todo-yearless-multi', (ref_dt.isoformat() if isinstance(ref_dt, datetime) else str(ref_dt)), (getattr(t, 'created_at').isoformat() if getattr(t, 'created_at', None) else None))
                                except Exception:
                                    pass
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
                            try:
                                if getattr(t, 'text', None) and 'WindowEvent' in getattr(t, 'text'):
                                    logger.info('DEBUG_WINDOWEVENT_EARLIEST todo_id=%s earliest=%s ref_dt=%s created_at=%s cap_dt=%s', getattr(t, 'id', None), earliest_cand.isoformat(), (ref_dt.isoformat() if isinstance(ref_dt, datetime) else str(ref_dt)), (item_created.isoformat() if item_created else None), (cap_dt.isoformat() if 'cap_dt' in locals() else None))
                            except Exception:
                                pass
                            if earliest_cand >= start_dt and earliest_cand <= end_dt:
                                    try:
                                        _sse_debug('calendar_occurrences.branch_choice', {'todo_id': t.id, 'chosen_branch': 'todo-yearless-earliest'})
                                    except Exception:
                                        pass
                                    try:
                                        if getattr(t, 'text', None) and 'WindowEvent' in getattr(t, 'text'):
                                            logger.info('DEBUG_WINDOWEVENT_CHOSEN_EARLIEST todo_id=%s chosen=%s', getattr(t, 'id', None), earliest_cand.isoformat())
                                    except Exception:
                                        pass
                                    # Diagnostic: log immediately before adding occurrence so we can see if add_occ is reached
                                    try:
                                        logger.info('DEBUG_BEFORE_ADD todo_id=%s candidate=%s source=%s', getattr(t, 'id', None), (earliest_cand.isoformat() if isinstance(earliest_cand, datetime) else str(earliest_cand)), 'todo-yearless-earliest')
                                    except Exception:
                                        pass
                                    add_occ('todo', t.id, t.list_id, t.text, earliest_cand, None, False, '', None, source='todo-yearless-earliest')
                                    if truncated:
                                        pass
                                    try:
                                        _sse_debug('calendar_occurrences.todo.added', {'todo_id': t.id, 'occurrence': earliest_cand.isoformat()})
                                    except Exception:
                                        pass
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
                                try:
                                    if getattr(t, 'id', None) == 10017:
                                        _sse_debug('calendar_occurrences.GUARDED_DEBUG', {'todo_id': t.id, 'stage': 'yearless-fallback', 'candidate': cand.isoformat()})
                                except Exception:
                                    pass
                                add_occ('todo', t.id, t.list_id, t.text, cand, None, False, '', None, source='todo-yearless-fallback')
                                break

        # close profiling timer for scanning todos
        _pa('scan_todos', t_scan_todos)

    # Compute effective priority per occurrence and sort:
    # Primary = max(normal priority, override_priority) where missing is lowest.
    # Secondary = occurrence datetime (ascending).
    try:
        t_sort = _pt('sort')
        # build maps for quick lookup
        list_map = {l.id: l for l in lists} if lists else {}
        def _parse_dt_str(s):
            try:
                ss = (s or '').replace('Z', '+00:00')
                d = datetime.fromisoformat(ss) if not isinstance(s, datetime) else s
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return int(d.timestamp())
            except Exception:
                return 0

        def _priority_of_occ(o):
            # determine effective priority for the occurrence
            try:
                item_type = o.get('item_type')
                iid = o.get('id')
                ep = None
                if item_type == 'todo':
                    t = todo_map.get(int(iid)) if todo_map else None
                    lp = getattr(t, 'priority', None) if t is not None else None
                    op = getattr(t, 'override_priority', None) if t is not None and hasattr(t, 'override_priority') else None
                else:
                    # list occurrence
                    lobj = list_map.get(int(iid)) if list_map else None
                    lp = getattr(lobj, 'priority', None) if lobj is not None else None
                    # override priority for lists comes from highest uncompleted todo in that list
                    op = list_override_map.get(int(iid)) if list_override_map else None
                try:
                    lpv = int(lp) if lp is not None else None
                except Exception:
                    lpv = None
                try:
                    opv = int(op) if op is not None else None
                except Exception:
                    opv = None
                if lpv is None and opv is None:
                    ep = None
                else:
                    ep = lpv if (opv is None or (lpv is not None and lpv >= opv)) else opv
                return ep
            except Exception:
                return None

        for o in occurrences:
            try:
                o['effective_priority'] = _priority_of_occ(o)
            except Exception:
                o['effective_priority'] = None

        # sort by (-priority, occurrence_ts) so higher priorities come first and earlier occurrences first on ties
        occurrences.sort(key=lambda x: (-(int(x.get('effective_priority')) if x.get('effective_priority') is not None else -9999), int(x.get('occ_ts') or 0)))
    except Exception:
        # fallback: sort by timestamp ascending if present, else occurrence_dt
        try:
            occurrences.sort(key=lambda x: int(x.get('occ_ts') or 0))
        except Exception:
            occurrences.sort(key=lambda x: x.get('occurrence_dt'))
    # Emit a compact SSE summary so tools can observe which occurrences were computed
    try:
        _sse_debug('calendar_occurrences.summary', {'count': len(occurrences), 'items': [{'id': o.get('id'), 'title': o.get('title'), 'occurrence_dt': o.get('occurrence_dt')} for o in occurrences]})
    except Exception:
        pass
    _pa('sort', t_sort)
    logger.info('calendar_occurrences computed %d occurrences before user filters (truncated=%s)', len(occurrences), truncated)

    # filter out occurrences ignored by the current user and mark completed
    t_filter = _pt('filter')
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
            # Additionally, respect per-todo calendar_ignored flag by filtering out occurrences
            # for any todo with calendar_ignored=True. We don't need a separate DB lookup here;
            # occurrences for ignored todos are prevented earlier by not scanning such todos when possible,
            # but guard here for any legacy entries. We mark them as ignored to reuse include_ignored logic.
            try:
                if not is_ignored and o.get('item_type') == 'todo':
                    # lightweight check: fetch flag for this id if needed
                    tid = int(o.get('id')) if o.get('id') is not None else None
                    if tid is not None:
                        trow = await sess.get(Todo, tid)
                        if trow is not None and bool(getattr(trow, 'calendar_ignored', False)):
                            ignored_scopes.append('calendar_ignored')
                            is_ignored = True
            except Exception:
                pass
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
    _pa('filter', t_filter)
    if _prof_enabled and _t0_total is not None:
        try:
            _prof['times']['total'] = float(_time.perf_counter() - _t0_total)
            _prof['counts']['occurrences_pre'] = int(sum(agg_pre.values())) if 'agg_pre' in locals() else int(len(occurrences))
            _prof['counts']['occurrences_post'] = int(len(occurrences))
            # Compact ms summary for logs
            def _ms(v):
                try:
                    return int(float(v) * 1000)
                except Exception:
                    return None
            summary = {k: _ms(v) for k, v in _prof['times'].items()}
            logger.info('calendar_profile owner=%s ms=%s counts=%s', owner_id, summary, _prof.get('counts'))
        except Exception:
            pass

    # Post-filter metrics: recompute source counts on the filtered set to report visible ratios
    counts_by_source_post: dict[str, int] = {}
    agg_post: dict[str, int] = {'rrule': 0, 'text': 0}
    try:
        for o in occurrences:
            _src = o.get('source') or 'unknown'
            counts_by_source_post[_src] = int(counts_by_source_post.get(_src, 0)) + 1
            if _src in ('list-rrule', 'todo-rrule', 'todo-inline-rrule'):
                agg_post['rrule'] = int(agg_post.get('rrule', 0)) + 1
            else:
                agg_post['text'] = int(agg_post.get('text', 0)) + 1
    except Exception:
        pass
    # Compute ratios defensively
    def _ratio(n: int, d: int) -> float:
        try:
            return (float(n) / float(d)) if int(d) > 0 else 0.0
        except Exception:
            return 0.0

    metrics = {
        'pre': {
            'counts_by_source': counts_by_source_pre,
            'aggregate': agg_pre,
            'fallback_text_ratio': _ratio(agg_pre.get('text', 0), (agg_pre.get('text', 0) + agg_pre.get('rrule', 0)))
        },
        'post': {
            'counts_by_source': counts_by_source_post,
            'aggregate': agg_post,
            'fallback_text_ratio': _ratio(agg_post.get('text', 0), (agg_post.get('text', 0) + agg_post.get('rrule', 0)))
        }
    }
    # Optionally emit a compact log summary for diagnostics
    try:
        logger.info('calendar_occurrences.metrics pre=%s post=%s total_pre=%s total_post=%s', agg_pre, agg_post, sum(agg_pre.values()), sum(agg_post.values()))
    except Exception:
        pass
    resp_obj = {'occurrences': occurrences, 'truncated': truncated, 'metrics': metrics}
    if _prof_enabled and _prof_include_resp:
        try:
            resp_profile = {
                'times_ms': {k: int(v * 1000) for k, v in (_prof.get('times') or {}).items()},
                'counts': _prof.get('counts') or {},
            }
            resp_obj['profile'] = resp_profile
        except Exception:
            pass
    return resp_obj



@app.post('/occurrence/complete')
async def mark_occurrence_completed(request: Request, hash: str = Form(...), current_user: User = Depends(require_login)):
    """Mark a single occurrence hash as completed for the current user.

    For browser clients using cookie/session authentication require a valid
    CSRF token. Bearer-token API clients (Authorization header) are allowed
    to call this endpoint without CSRF.
    """
    # Early marker so logs clearly show entry into this handler before any other debug lines
    try:
        csrf_logger.info('----- /occurrence/complete BEGIN -----')
    except Exception:
        pass
    # Determine whether request used bearer token (Authorization header)
    auth_hdr = request.headers.get('authorization')
    # Add verbose debug logging to help diagnose 403s. Controlled by
    # ENABLE_VERBOSE_DEBUG environment variable to avoid leaking secrets.
    try:
        csrf_logger.info('/occurrence/complete called user=%s auth_hdr_present=%s', getattr(current_user, 'username', None), bool(auth_hdr))
    except Exception:
        pass

    # If no Authorization header, this is likely a cookie-authenticated browser
    # request — require CSRF token. Accept token from form field _csrf or
    # cookie 'csrf_token'. Log masked token info when verbose debugging is enabled.
    if not auth_hdr:
        form = await request.form()
        form_token = form.get('_csrf')
        cookie_token = request.cookies.get('csrf_token')
        token = form_token or cookie_token
        csrf_assert(bool(token), 'csrf_req_token_present', form_present=bool(form_token), cookie_present=bool(cookie_token))
        try:
            # Assert configured server-side expiry and compare to token remaining
            from .auth import CSRF_TOKEN_EXPIRE_SECONDS as _CFG_EXP_S
            info_tok = _csrf_token_info(token)
            csrf_assert(True, 'csrf_server_config_exp', configured=_CFG_EXP_S)
            if info_tok.get('remaining') is not None:
                csrf_assert(abs(int(info_tok.get('remaining')) - int(_CFG_EXP_S)) <= 3605 or info_tok.get('remaining') <= int(_CFG_EXP_S) + 120, 'csrf_used_remaining_vs_config', remaining=info_tok.get('remaining'), configured=_CFG_EXP_S)
        except Exception:
            pass
        try:
            # Compare form vs cookie tokens
            info_form = _csrf_token_info(form_token)
            info_cookie = _csrf_token_info(cookie_token)
            same_fc = (info_form.get('hash') == info_cookie.get('hash')) if (form_token and cookie_token) else True
            csrf_assert(same_fc, 'csrf_form_cookie_same', form_hash=info_form.get('hash'), cookie_hash=info_cookie.get('hash'))
            # Enumerate any duplicate csrf_token cookies from raw header
            raw_cookie = request.headers.get('cookie')
            infos = _extract_all_csrf_from_cookie_header(raw_cookie)
            csrf_assert(True, 'csrf_cookie_header_enumerated', count=len(infos), hashes=[i.get('hash') for i in infos], remainings=[i.get('remaining') for i in infos], exps=[i.get('exp') for i in infos])
        except Exception:
            pass
        try:
            import base64
            import json
            import datetime
            import hashlib
            tok_hash = hashlib.sha256((token or '').encode('utf-8')).hexdigest()[:12] if token else None
            parts = (token or '').split('.')
            token_sub = token_exp_iso = token_seconds_left = token_expired = None
            if len(parts) >= 2:
                try:
                    payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                    token_sub = payload.get('sub')
                    exp = payload.get('exp')
                    if exp is not None:
                        token_exp_iso = datetime.datetime.utcfromtimestamp(int(exp)).isoformat() + 'Z'
                        # Use timezone-aware UTC now to avoid local offset being applied
                        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                        token_seconds_left = int(exp) - now_ts
                        token_expired = token_seconds_left <= 0
                except Exception:
                    pass
            csrf_logger.info('/occurrence/complete debug: token_present=%s token_hash_prefix=%s token_sub=%s token_exp=%s token_exp_seconds_left=%s token_expired=%s csrf_timeout_minutes=%s form_keys=%s cookie_names=%s header_keys=%s remote=%s',
                              bool(token), tok_hash, token_sub, token_exp_iso, token_seconds_left, token_expired, CSRF_TOKEN_EXPIRE_MINUTES, list(form.keys()), list(request.cookies.keys()), list(request.headers.keys()), (request.client.host if request.client else None))
        except Exception:
            csrf_logger.exception('occurrence/complete: verbose debug block failed')

        from .auth import verify_csrf_token
        ok = False
        used = None
        if form_token:
            try:
                ok = verify_csrf_token(form_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(form) raised an exception')
                ok = False
            if ok:
                used = 'form'
        if not ok and cookie_token:
            try:
                ok = verify_csrf_token(cookie_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(cookie) raised an exception')
                ok = False
            if ok:
                used = 'cookie'
        if not ok:
            try:
                csrf_logger.warning('/occurrence/complete CSRF verification failed for user=%s tokens_present form=%s cookie=%s', getattr(current_user, 'username', None), bool(form_token), bool(cookie_token))
            except Exception:
                pass
            # Additional immediate diagnostics: log token expiry/sub if available
            try:
                if token:
                    import base64
                    import json
                    import datetime
                    try:
                        parts = token.split('.')
                        if len(parts) >= 2:
                            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                            token_sub = payload.get('sub')
                            exp = payload.get('exp')
                            if exp is not None:
                                # record raw exp and types for diagnosis
                                raw_exp = exp
                                raw_exp_type = type(exp).__name__
                                # try to interpret as int-seconds since epoch
                                try:
                                    exp_int = int(exp)
                                except Exception:
                                    exp_int = None
                                if exp_int is not None:
                                    # use timezone-aware UTC timestamps to avoid naive->local interpretation
                                    token_exp_iso = datetime.datetime.fromtimestamp(int(exp_int), datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
                                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                                    now_ts = int(now_dt.timestamp())
                                    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
                                    token_seconds_left = int(exp_int) - now_ts
                                    token_expired = token_seconds_left <= 0
                                else:
                                    token_exp_iso = None
                                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                                    now_ts = int(now_dt.timestamp())
                                    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
                                    token_seconds_left = None
                                    token_expired = None
                            else:
                                token_exp_iso = token_seconds_left = token_expired = raw_exp = raw_exp_type = None
                    except Exception:
                        token_sub = token_exp_iso = token_seconds_left = token_expired = raw_exp = raw_exp_type = now_ts = now_iso = None
                else:
                    token_sub = token_exp_iso = token_seconds_left = token_expired = None
                try:
                    csrf_logger.info('/occurrence/complete CSRF diagnostic: token_sub=%s token_exp=%s token_exp_seconds_left=%s token_expired=%s csrf_timeout_minutes=%s raw_exp=%s raw_exp_type=%s now_ts=%s now_iso=%s',
                                token_sub, token_exp_iso, token_seconds_left, token_expired, CSRF_TOKEN_EXPIRE_MINUTES, repr(raw_exp) if 'raw_exp' in locals() else None, (raw_exp_type if 'raw_exp_type' in locals() else None), (now_ts if 'now_ts' in locals() else None), (now_iso if 'now_iso' in locals() else None))
                except Exception:
                    pass
            except Exception:
                csrf_logger.exception('occurrence/complete: failed to log immediate CSRF diagnostics')
            raise HTTPException(status_code=403, detail='invalid csrf token')
        else:
            try:
                csrf_logger.info('/occurrence/complete CSRF verification ok used=%s', used)
                csrf_assert(used in ('form', 'cookie'), 'csrf_verify_used_source', used=used)
                # Assert the used token is compatible with the most recently issued one for this user
                used_token = form_token if used == 'form' else cookie_token
                info_used = _csrf_token_info(used_token)
                last = _last_csrf_by_user.get(getattr(current_user, 'username', None))
                if last:
                    same_hash = (last.get('token_hash') == info_used.get('hash'))
                    csrf_assert(same_hash, 'csrf_used_matches_last_issued', used_hash=info_used.get('hash'), last_hash=last.get('token_hash'), last_source=last.get('source'))
                    # Remaining time should not be absurd; expect <= configured seconds + clock skew
                    rem = info_used.get('remaining')
                    if rem is not None:
                        csrf_assert(rem <= (CSRF_TOKEN_EXPIRE_SECONDS + 120), 'csrf_used_remaining_reasonable', remaining=rem, configured=CSRF_TOKEN_EXPIRE_SECONDS)
                else:
                    csrf_assert(False, 'csrf_no_last_issued_record', user=getattr(current_user, 'username', None))
            except Exception:
                pass
    else:
        try:
            csrf_logger.info('/occurrence/complete Authorization header present; CSRF check bypassed')
        except Exception:
            pass

    from .models import CompletedOccurrence
    async with async_session() as sess:
        # idempotent upsert: insert row if not exists
        exists_q = await sess.scalars(select(CompletedOccurrence).where(CompletedOccurrence.user_id == current_user.id).where(CompletedOccurrence.occ_hash == hash))
        if exists_q.first():
            try:
                logger.info('/occurrence/complete idempotent (already completed) user_id=%s hash=%s', getattr(current_user, 'id', None), hash)
            except Exception:
                pass
            return {'ok': True, 'created': False}
        row = CompletedOccurrence(user_id=current_user.id, occ_hash=hash)
        sess.add(row)
        await sess.commit()
        try:
            logger.info('/occurrence/complete persisted completion user_id=%s hash=%s', getattr(current_user, 'id', None), hash)
            csrf_assert(True, 'csrf_complete_persisted', user_id=getattr(current_user, 'id', None), occ_hash=hash)
        except Exception:
            pass
        # Ensure positions are unique and sequential. If previous data had
        # duplicate positions (can happen with older imports or a bug),
        # normalize positions so order becomes deterministic and contiguous.
        try:
            cres = await sess.exec(select(Category).where(Category.owner_id == current_user.id).order_by(Category.position.asc(), Category.id.asc()))
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
    # token clients to call without CSRF. Add verbose debugging similar to
    # /occurrence/complete to diagnose 403 failures.
    auth_hdr = request.headers.get('authorization')
    try:
        logger.info('/occurrence/uncomplete called user=%s auth_hdr_present=%s', getattr(current_user, 'username', None), bool(auth_hdr))
    except Exception:
        pass

    if not auth_hdr:
        form = await request.form()
        form_token = form.get('_csrf')
        cookie_token = request.cookies.get('csrf_token')
        token = form_token or cookie_token
        try:
            if ENABLE_VERBOSE_DEBUG:
                import hashlib
                tok_hash = None
                try:
                    if token:
                        tok_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()[:12]
                except Exception:
                    tok_hash = None
                    try:
                        # Decode CSRF JWT payload for expiry diagnostics
                        token_exp_iso = None
                        token_seconds_left = None
                        token_expired = None
                        token_sub = None
                        try:
                            import base64
                            import json
                            import datetime
                            parts = (token or '').split('.')
                            if len(parts) >= 2:
                                payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                                token_sub = payload.get('sub')
                                exp = payload.get('exp')
                                if exp is not None:
                                    token_exp_iso = datetime.datetime.utcfromtimestamp(int(exp)).isoformat() + 'Z'
                                    # Use timezone-aware UTC now to avoid local offset being applied
                                    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
                                    token_seconds_left = int(exp) - now_ts
                                    token_expired = token_seconds_left <= 0
                        except Exception:
                            token_exp_iso = token_seconds_left = token_expired = token_sub = None
                        logger.info('/occurrence/uncomplete debug: token_present=%s token_hash_prefix=%s token_sub=%s token_exp=%s token_exp_seconds_left=%s token_expired=%s csrf_timeout_minutes=%s form_keys=%s cookie_names=%s header_keys=%s remote=%s',
                                    bool(token), tok_hash, token_sub, token_exp_iso, token_seconds_left, token_expired, CSRF_TOKEN_EXPIRE_MINUTES, list(form.keys()), list(request.cookies.keys()), list(request.headers.keys()), (request.client.host if request.client else None))
                    except Exception:
                        logger.exception('occurrence/uncomplete: failed to log debug info')
        except Exception:
            logger.exception('occurrence/uncomplete: verbose debug block failed')

        from .auth import verify_csrf_token
        ok = False
        used = None
        if form_token:
            try:
                ok = verify_csrf_token(form_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(form) raised an exception')
                ok = False
            if ok:
                used = 'form'
        if not ok and cookie_token:
            try:
                ok = verify_csrf_token(cookie_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(cookie) raised an exception')
                ok = False
            if ok:
                used = 'cookie'
        if not ok:
            try:
                logger.warning('/occurrence/uncomplete CSRF verification failed for user=%s tokens_present form=%s cookie=%s', getattr(current_user, 'username', None), bool(form_token), bool(cookie_token))
            except Exception:
                pass
            # Additional immediate diagnostics: log token expiry/sub if available
            try:
                if token:
                    import base64
                    import json
                    import datetime
                    try:
                        parts = token.split('.')
                        if len(parts) >= 2:
                            payload = json.loads(base64.urlsafe_b64decode(parts[1] + '=='))
                            token_sub = payload.get('sub')
                            exp = payload.get('exp')
                            if exp is not None:
                                raw_exp = exp
                                raw_exp_type = type(exp).__name__
                                try:
                                    exp_int = int(exp)
                                except Exception:
                                    exp_int = None
                                if exp_int is not None:
                                    token_exp_iso = datetime.datetime.fromtimestamp(int(exp_int), datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
                                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                                    now_ts = int(now_dt.timestamp())
                                    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
                                    token_seconds_left = int(exp_int) - now_ts
                                    token_expired = token_seconds_left <= 0
                                else:
                                    token_exp_iso = None
                                    now_dt = datetime.datetime.now(datetime.timezone.utc)
                                    now_ts = int(now_dt.timestamp())
                                    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
                                    token_seconds_left = None
                                    token_expired = None
                            else:
                                token_exp_iso = token_seconds_left = token_expired = raw_exp = raw_exp_type = None
                    except Exception:
                        token_sub = token_exp_iso = token_seconds_left = token_expired = raw_exp = raw_exp_type = now_ts = now_iso = None
                else:
                    token_sub = token_exp_iso = token_seconds_left = token_expired = None
                try:
                    logger.info('/occurrence/uncomplete CSRF diagnostic: token_sub=%s token_exp=%s token_exp_seconds_left=%s token_expired=%s csrf_timeout_minutes=%s raw_exp=%s raw_exp_type=%s now_ts=%s now_iso=%s',
                                token_sub, token_exp_iso, token_seconds_left, token_expired, CSRF_TOKEN_EXPIRE_MINUTES, repr(raw_exp) if 'raw_exp' in locals() else None, (raw_exp_type if 'raw_exp_type' in locals() else None), (now_ts if 'now_ts' in locals() else None), (now_iso if 'now_iso' in locals() else None))
                except Exception:
                    pass
            except Exception:
                logger.exception('occurrence/uncomplete: failed to log immediate CSRF diagnostics')
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import CompletedOccurrence
    async with async_session() as sess:
        # delete all rows matching this user+hash (should be at most one)
        q = await sess.scalars(select(CompletedOccurrence).where(CompletedOccurrence.user_id == current_user.id).where(CompletedOccurrence.occ_hash == hash))
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
    try:
        logger.info('create_ignore_scope entry: scope_type=%s scope_key=%s from_dt=%s current_user=%s', scope_type, scope_key, from_dt, getattr(current_user, 'username', None))
        # Verbose debug logging may expose sensitive values; only enable when
        # explicitly toggled during debugging.
        try:
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('create_ignore_scope headers: %s', dict(request.headers))
                    logger.info('create_ignore_scope cookies: %s', dict(request.cookies))
                except Exception:
                    logger.exception('failed to read request headers/cookies for debug')
        except Exception:
            logger.exception('failed to evaluate ENABLE_VERBOSE_DEBUG')
    except Exception:
        logger.exception('early logging in create_ignore_scope failed')

    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        # parse form fields and log them (safe for debugging in dev)
        form = await request.form()
        try:
            if ENABLE_VERBOSE_DEBUG:
                # show form keys and values (beware of sensitive values in prod)
                logger.info('create_ignore_scope form fields: %s', {k: form.get(k) for k in form.keys()})
        except Exception:
            logger.exception('failed to log form fields')

        # token may come from the form or from the csrf cookie. Prefer a
        # successful verification from the freshest source: try form token
        # first (matching client-submitted hidden field), but if it fails and
        # a csrf cookie is present, try verifying the cookie as a fallback.
        form_token = form.get('_csrf')
        cookie_token = request.cookies.get('csrf_token')
        try:
            logger.info('create_ignore_scope csrf tokens: form_present=%s cookie_present=%s', bool(form_token), bool(cookie_token))
        except Exception:
            logger.exception('failed to log csrf token info')

        from .auth import verify_csrf_token
        try:
            logger.info('create_ignore_scope verifying csrf token for user=%s', getattr(current_user, 'username', None))
            ok = False
            used = None
            # Try form token first if provided
            if form_token:
                try:
                    ok = verify_csrf_token(form_token, current_user.username)
                except Exception:
                    logger.exception('verify_csrf_token(form) raised an exception')
                    ok = False
                if ok:
                    used = 'form'
            # If form token absent or failed, fall back to cookie token
            if not ok and cookie_token:
                try:
                    ok = verify_csrf_token(cookie_token, current_user.username)
                except Exception:
                    logger.exception('verify_csrf_token(cookie) raised an exception')
                    ok = False
                if ok:
                    used = 'cookie'

            logger.info('create_ignore_scope verify_csrf_token result: ok=%s used=%s', ok, used)
            if not ok:
                logger.info('create_ignore_scope failing CSRF check: form_present=%s cookie_present=%s', bool(form_token), bool(cookie_token))
                raise HTTPException(status_code=403, detail='invalid csrf token')
        except HTTPException:
            # re-raise HTTPException to preserve intended response
            raise
        except Exception:
            logger.exception('unexpected error during CSRF verification')
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import IgnoredScope
    # compute scope_hash conservatively
    from .utils import ignore_list_hash, ignore_todo_from_hash
    # Normalize from_dt into a Python datetime so SQLite DateTime column
    # receives a proper datetime object instead of a string (which causes
    # a TypeError on insert). Accept ISO date or datetime strings; for
    # date-only strings like 'YYYY-MM-DD' treat them as midnight UTC.
    parsed_from_dt = None
    if from_dt:
        try:
            from datetime import datetime as _dt
            # Try full ISO parse first
            try:
                parsed = _dt.fromisoformat(from_dt)
                # If parsed has no tzinfo, assume UTC for consistency
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed_from_dt = parsed
            except Exception:
                # Fallback for date-only strings YYYY-MM-DD
                import re as _re
                m = _re.match(r'^(\d{4})-(\d{2})-(\d{2})$', from_dt)
                if m:
                    y, mo, d = map(int, m.groups())
                    parsed_from_dt = _dt(y, mo, d, tzinfo=timezone.utc)
                else:
                    raise HTTPException(status_code=400, detail='invalid from_dt')
        except HTTPException:
            raise
        except Exception:
            logger.exception('failed to parse from_dt: %s', from_dt)
            raise HTTPException(status_code=400, detail='invalid from_dt')

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
            # Pass the parsed datetime (if available) to the hash helper so
            # the canonical iso-form used for hashing matches the DB stored
            # datetime value. Fall back to raw string if parsing failed.
            scope_hash = ignore_todo_from_hash(scope_key, parsed_from_dt if parsed_from_dt is not None else from_dt)
    else:
        raise HTTPException(status_code=400, detail='invalid scope_type')
    async with async_session() as sess:
        # store parsed_from_dt (datetime) when available so DB insert uses
        # a proper datetime object instead of the raw string
        rec = IgnoredScope(user_id=current_user.id, scope_type=scope_type, scope_key=str(scope_key), from_dt=parsed_from_dt, scope_hash=scope_hash, active=True)
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
        form_token = form.get('_csrf')
        cookie_token = request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        ok = False
        # prefer form token but fallback to cookie if form fails/absent
        if form_token:
            try:
                ok = verify_csrf_token(form_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(form) raised an exception')
                ok = False
        if not ok and cookie_token:
            try:
                ok = verify_csrf_token(cookie_token, current_user.username)
            except Exception:
                logger.exception('verify_csrf_token(cookie) raised an exception')
                ok = False
        if not ok:
            raise HTTPException(status_code=403, detail='invalid csrf token')

    from .models import IgnoredScope
    from .utils import ignore_list_hash, ignore_todo_from_hash
    async with async_session() as sess:
        if scope_type == 'list':
            scope_hash = ignore_list_hash(scope_key, owner_id=current_user.id)
            q = await sess.scalars(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
        elif scope_type == 'occurrence':
            scope_hash = str(scope_key)
            q = await sess.scalars(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
        elif scope_type == 'todo_from':
            # If from_dt is provided, target the exact hash; otherwise, deactivate any
            # todo_from scopes for this scope_key (id) regardless of from_dt.
            if from_dt:
                # Parse from_dt into a timezone-aware datetime so the hash
                # helper receives the same canonical input used when creating
                # the IgnoredScope row.
                try:
                    parsed = _parse_iso_to_utc(from_dt)
                except HTTPException:
                    # If parse failed, treat as no matching rows (invalid input)
                    return {'ok': True, 'updated': 0}
                scope_hash = ignore_todo_from_hash(scope_key, parsed)
                q = await sess.scalars(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_hash == scope_hash).where(IgnoredScope.active == True))
            else:
                q = await sess.scalars(select(IgnoredScope).where(IgnoredScope.user_id == current_user.id).where(IgnoredScope.scope_type == 'todo_from').where(IgnoredScope.scope_key == str(scope_key)).where(IgnoredScope.active == True))
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
async def get_default_list(current_user: User = Depends(require_login)):
    """Return the server default list only if it's visible to the caller (owned or public).

    Previously this endpoint was unauthenticated and could leak private list
    metadata. Now it requires login and enforces visibility.
    """
    async with async_session() as sess:
        qs = await sess.exec(select(ServerState))
        ss = qs.first()
        if not ss or not ss.default_list_id:
            raise HTTPException(status_code=404, detail="default list not set")
        q = await sess.scalars(select(ListState).where(ListState.id == ss.default_list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        # Enforce visibility: allow if public (owner_id is NULL) or owned by caller
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail="forbidden")
        return lst


# Register the exact route for '/lists/recent' BEFORE the variable '/lists/{list_id}' route
# to avoid Starlette matching '/lists/recent' against '{list_id}' and returning 422.
@app.get('/lists/recent')
async def get_recent_lists(limit: int = 25, current_user: User = Depends(require_login)):
    return await _get_recent_lists_impl(limit, current_user)


@app.get('/lists/{list_id}')
async def get_list(list_id: int, current_user: User = Depends(require_login)):
    """Return a JSON representation of a list the user owns (or public).

    Used by client-side move page to display names for marked ids.
    """
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        return _serialize_list(lst)


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
async def set_default_list(list_id: int, current_user: User = Depends(require_login)):
    """Set the server default list.

    Only allow setting to a list visible to the caller (owned by them or public).
    This avoids unauthenticated callers hijacking the default pointer.
    """
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        # Restrict: user may only select a list they own or a public list
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail="forbidden")
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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

        # detach any child sublists owned by this list so they don't dangle
        try:
            await sess.exec(sqlalchemy_update(ListState).where(ListState.parent_list_id == list_id).values(parent_list_id=None, parent_list_position=None))
            await sess.commit()
        except Exception:
            await sess.rollback()
        # remove any list-level artifacts (completion types, list hashtags)
        await sess.exec(sqlalchemy_delete(CompletionType).where(CompletionType.list_id == list_id))
        await sess.exec(sqlalchemy_delete(ListHashtag).where(ListHashtag.list_id == list_id))
        # cleanup any trash metadata for this list (if present)
        try:
            await sess.exec(sqlalchemy_delete(ListTrashMeta).where(ListTrashMeta.list_id == list_id))
        except Exception:
            pass
        # remove collation registration rows for this list
        try:
            await sess.exec(sqlalchemy_delete(UserCollation).where(UserCollation.list_id == list_id))
        except Exception:
            pass
        # remove ItemLink edges where this list is the source or the target
        try:
            await sess.exec(sqlalchemy_delete(ItemLink).where(ItemLink.src_type == 'list').where(ItemLink.src_id == list_id))
            await sess.exec(sqlalchemy_delete(ItemLink).where(ItemLink.tgt_type == 'list').where(ItemLink.tgt_id == list_id))
        except Exception:
            pass
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
        return _redirect_or_json(request, '/html_no_js/login')
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, cu.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Attempt soft-delete by moving the list under the user's Trash list.
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            return _redirect_or_json(request, '/html_no_js/')
        # ensure ownership
        if lst.owner_id != cu.id:
            raise HTTPException(status_code=403, detail='forbidden')

        # Find or create user's Trash list
        q = await sess.scalars(select(ListState).where(ListState.owner_id == cu.id).where(ListState.name == 'Trash'))
        trash = q.first()
        if not trash:
            trash = ListState(name='Trash', owner_id=cu.id)
            sess.add(trash)
            await sess.commit()
            await sess.refresh(trash)

        # If already in trash, perform permanent delete
        if lst.parent_list_id == trash.id:
            await delete_list(list_id=list_id, current_user=cu)
            return _redirect_or_json(request, '/html_no_js/')

        # create ListTrashMeta and move the list under Trash (preserve owner)
        meta = ListTrashMeta(list_id=list_id, original_parent_list_id=getattr(lst, 'parent_list_id', None), original_owner_id=getattr(lst, 'owner_id', None))
        sess.add(meta)
        lst.parent_list_id = trash.id
        lst.modified_at = now_utc()
        sess.add(lst)
        try:
            await _touch_list_modified(sess, meta.original_parent_list_id)
            await _touch_list_modified(sess, trash.id)
        except Exception:
            pass
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': list_id})
    return _redirect_or_json(request, '/html_no_js/')


@app.post("/lists/{list_id}/hashtags")
async def add_list_hashtag(list_id: int, tag: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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


@app.post("/lists/{list_id}/hashtags/json")
async def add_list_hashtag_json(list_id: int, body: dict, current_user: User = Depends(require_login)):
    tag = body.get('tag') if isinstance(body, dict) else None
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    return await add_list_hashtag(list_id=list_id, tag=tag, current_user=current_user)


@app.get("/lists/{list_id}/hashtags")
async def get_list_hashtags(
    list_id: int,
    include_todo_tags: bool = False,
    include_sublists: bool = False,
    combine: bool = False,
    current_user: User = Depends(require_login),
):
    """Return hashtags for a list.

        Query params:
            - include_todo_tags (bool): if true, also collect hashtags attached to todos in the list.
            - include_sublists (bool): if true, also collect hashtags from immediate sublists:
                    • sublists' list-level hashtags
                    • hashtags on todos within those sublists
            - combine (bool): if true, return a single deduplicated `hashtags` array combining
                list, todo, and optional sublist tags in SSR order:
                    list-level -> todo-level -> sublist list-level -> sublist todo-level

    Ownership rules: only the list owner may call this API (same as other list APIs).
    """
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
            # Collect todo-level tags in deterministic order (by Todo.id then TodoHashtag.id)
            # and preserve first-seen order when deduplicating so the combined list
            # matches server-side SSR merging which iterates todos in order.
            qtt = (
                select(Todo.id, Hashtag.tag)
                .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
                .join(Todo, Todo.id == TodoHashtag.todo_id)
                .where(Todo.list_id == list_id)
                # Order by Todo id then tag text to be deterministic and avoid
                # referencing ORM attributes that may not be present in some envs.
                .order_by(Todo.id.asc(), Hashtag.tag.asc())
            )
            tres = await sess.exec(qtt)
            seen = set()
            for row in tres.all():
                # row is (todo_id, tag)
                try:
                    _tid, val = row
                except Exception:
                    val = row[1] if isinstance(row, (tuple, list)) and len(row) > 1 else row[0]
                if isinstance(val, str) and val and val not in seen:
                    todo_tags.append(val)
                    seen.add(val)

        # optionally include immediate sublists' tags (list-level and their todos)
        sublist_list_tags: list[str] = []
        sublist_todo_tags: list[str] = []
        if include_sublists:
            # discover immediate sublists
            qsubs = await sess.exec(
                select(ListState.id)
                .where(ListState.parent_list_id == list_id)
                .order_by(ListState.id.asc())
            )
            sublist_ids = [sid for (sid,) in qsubs.all()] if qsubs is not None else []
            # list-level tags on sublists
            if sublist_ids:
                qsl = await sess.exec(
                    select(Hashtag.tag)
                    .join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id)
                    .where(ListHashtag.list_id.in_(sublist_ids))
                    .order_by(Hashtag.tag.asc())
                )
                for row in qsl.all():
                    val = row[0] if isinstance(row, (tuple, list)) else row
                    if isinstance(val, str) and val and val not in sublist_list_tags:
                        sublist_list_tags.append(val)
                # todo-level tags within sublists
                qst = await sess.exec(
                    select(Todo.id, Hashtag.tag)
                    .join(TodoHashtag, TodoHashtag.hashtag_id == Hashtag.id)
                    .join(Todo, Todo.id == TodoHashtag.todo_id)
                    .where(Todo.list_id.in_(sublist_ids))
                    .order_by(Todo.id.asc(), Hashtag.tag.asc())
                )
                seen_sub_todo = set()
                for row in qst.all():
                    try:
                        _tid2, val = row
                    except Exception:
                        val = row[1] if isinstance(row, (tuple, list)) and len(row) > 1 else row[0]
                    if isinstance(val, str) and val and val not in seen_sub_todo:
                        sublist_todo_tags.append(val)
                        seen_sub_todo.add(val)

        # return shape: preserve backwards compatibility when include_todo_tags is false
        if not include_todo_tags and not include_sublists and not combine:
            return {"list_id": list_id, "hashtags": list_tags}

        if combine:
            # combined deduped list: list-level -> todo-level -> sublist list-level -> sublist todo-level
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
            for t in sublist_list_tags:
                if t not in seen:
                    seen.add(t)
                    combined.append(t)
            for t in sublist_todo_tags:
                if t not in seen:
                    seen.add(t)
                    combined.append(t)
            return {"list_id": list_id, "hashtags": combined}

        # otherwise return separate keys
        out = {"list_id": list_id, "list_hashtags": list_tags}
        if include_todo_tags:
            out["todo_hashtags"] = todo_tags
        if include_sublists:
            out["sublist_hashtags"] = sublist_list_tags
            out["sublist_todo_hashtags"] = sublist_todo_tags
        return out


@app.get("/lists/{list_id}/completion_types")
async def get_completion_types(list_id: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        # Return completion types in creation order (id ASC) for a stable UI order
        qc = await sess.exec(select(CompletionType).where(CompletionType.list_id == list_id).order_by(CompletionType.id.asc()))
        return qc.all()


@app.post("/lists/{list_id}/completion_types")
async def create_completion_type_endpoint(list_id: int, name: str, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
        q = await sess.scalars(select(CompletionType).where(CompletionType.list_id == list_id).where(CompletionType.name == name))
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


@app.delete("/lists/{list_id}/hashtags/json")
async def remove_list_hashtag_json(list_id: int, body: dict, current_user: User = Depends(require_login)):
    tag = body.get('tag') if isinstance(body, dict) else None
    if not tag:
        raise HTTPException(status_code=400, detail='tag is required')
    return await remove_list_hashtag(list_id=list_id, tag=tag, current_user=current_user)


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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'removed': tag})
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'tag': tag})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


from typing import Optional as _Optional


@app.post("/todos/{todo_id}/hashtags")
async def add_todo_hashtag(todo_id: int, tag: str, current_user: _Optional[User] = Depends(get_current_user)):
    async with async_session() as sess:
        q = await sess.scalars(select(Todo).where(Todo.id == todo_id))
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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


@app.get("/lists/{list_id}/todos")
async def get_list_todos(list_id: int, current_user: User = Depends(require_login)):
    """Get all todos for a list that the user owns."""
    async with async_session() as sess:
        # Verify list exists and user owns it
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # Fetch todos for the list
        try:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.priority.desc().nullslast(), Todo.created_at.desc()))
        except Exception:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
        todos = q2.all()

        # Serialize todos
        result = []
        for todo in todos:
            result.append(_serialize_todo(todo, []))

        return result


@app.patch("/lists/{list_id}")
async def patch_list(list_id: int, payload: dict, current_user: User = Depends(require_login)):
    """Patch list fields via JSON. Accepts optional keys: name (str), priority (int|null), completed (bool), lists_up_top (bool)."""
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        changed = False
        if 'name' in payload:
            name = payload.get('name')
            if not isinstance(name, str):
                raise HTTPException(status_code=400, detail='invalid name')
            lst.name = name
            changed = True
        if 'priority' in payload:
            pr = payload.get('priority')
            if pr is None:
                lst.priority = None
                changed = True
            else:
                try:
                    pr_int = int(pr)
                except Exception:
                    raise HTTPException(status_code=400, detail='invalid priority')
                if pr_int < 1 or pr_int > 10:
                    raise HTTPException(status_code=400, detail='priority out of range')
                lst.priority = pr_int
                changed = True
        if 'completed' in payload:
            comp = payload.get('completed')
            if not isinstance(comp, bool):
                raise HTTPException(status_code=400, detail='invalid completed value')
            lst.completed = comp
            changed = True
        if 'lists_up_top' in payload:
            lists_up_top = payload.get('lists_up_top')
            if not isinstance(lists_up_top, bool):
                raise HTTPException(status_code=400, detail='invalid lists_up_top value')
            lst.lists_up_top = lists_up_top
            changed = True
        if changed:
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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


@app.post('/todos/{todo_id}/visit')
async def record_todo_visit(todo_id: int, current_user: User = Depends(require_login)):
    """Record that the current_user visited the given todo (mirrors list visits).

    Preserves a top-N order via position field and prunes older rows per-user.
    """
    async with async_session() as sess:
        # ensure todo exists and is visible to user via parent list ownership or public
        t = await sess.get(Todo, todo_id)
        if not t:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == t.list_id))
        lst = ql.first()
        if lst and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        now = now_utc()
        try:
            top_n = int(os.getenv('RECENT_TODOS_TOP_N', '10'))
        except Exception:
            top_n = 10
        qv = await sess.exec(select(RecentTodoVisit).where(RecentTodoVisit.user_id == current_user.id).where(RecentTodoVisit.todo_id == todo_id))
        rv = qv.first()
        if rv and rv.position is not None and rv.position < top_n:
            rv.visited_at = now
            sess.add(rv)
            await sess.commit()
        else:
            try:
                evict_pos = max(0, top_n - 1)
                shift_sql = text(
                    "UPDATE recenttodovisit SET position = position + 1 "
                    "WHERE user_id = :uid AND position IS NOT NULL AND position < :maxpos"
                )
                await sess.exec(shift_sql.bindparams(uid=current_user.id, maxpos=evict_pos))
                clear_sql = text(
                    "UPDATE recenttodovisit SET position = NULL WHERE user_id = :uid AND position >= :maxpos"
                )
                await sess.exec(clear_sql.bindparams(uid=current_user.id, maxpos=evict_pos))
            except Exception:
                logger.exception('failed to shift recenttodo positions')
            if rv:
                rv.position = 0
                rv.visited_at = now
                sess.add(rv)
            else:
                rv = RecentTodoVisit(user_id=current_user.id, todo_id=todo_id, visited_at=now, position=0)
                sess.add(rv)
            await sess.commit()

        try:
            cap = int(os.getenv('RECENT_TODOS_PER_USER', '100'))
        except Exception:
            cap = 100
        if cap > 0:
            prune_sql = text(
                "DELETE FROM recenttodovisit WHERE (user_id, todo_id) IN ("
                "SELECT user_id, todo_id FROM ("
                "SELECT user_id, todo_id, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY visited_at DESC) AS rn "
                "FROM recenttodovisit WHERE user_id = :uid) t WHERE t.rn > :cap)"
            )
            try:
                await sess.exec(prune_sql.bindparams(uid=current_user.id, cap=cap))
                await sess.commit()
            except Exception:
                pass
        return {"todo_id": todo_id, "visited_at": now}


async def _get_recent_lists_impl(limit: int, current_user: User):
    """Return the recent lists visited by the current user ordered by preserved top-N then recent views."""
    try:
        top_n = int(os.getenv('RECENT_LISTS_TOP_N', '10'))
    except Exception:
        top_n = 10
    async with async_session() as sess:
        # First fetch top-N positioned rows ordered by position ASC
        top_q = (
            select(RecentListVisit)
            .where(RecentListVisit.user_id == current_user.id)
            .where(RecentListVisit.position != None)
            .order_by(RecentListVisit.position.asc())
            .limit(top_n)
        )
        top_res = await sess.exec(top_q)
        top_rows = top_res.all()
        top_ids = [r.list_id for r in top_rows]

        results: list[dict] = []
        # load ListState for top rows preserving order
        if top_ids:
            qlists = (
                select(ListState)
                .where(ListState.id.in_(top_ids))
                .where(ListState.parent_todo_id == None)
                .where(ListState.parent_list_id == None)
            )
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
            q = (
                select(ListState)
                .join(RecentListVisit, RecentListVisit.list_id == ListState.id)
                .where(RecentListVisit.user_id == current_user.id)
                .where(ListState.parent_todo_id == None)
                .where(ListState.parent_list_id == None)
            )
            if top_ids:
                q = q.where(RecentListVisit.list_id.notin_(top_ids))
            q = q.order_by(RecentListVisit.visited_at.desc()).limit(remaining)
            res = await sess.exec(q)
            other_lists = res.all()
            for lst in other_lists:
                results.append(lst)

        return results


@app.post("/todos")
async def create_todo(request: Request, current_user: User = Depends(require_login)):
    """
    Create a todo in an explicit, existing list. Expects JSON payload with:
    - text: str (required)
    - note: str (optional)
    - list_id: int (required)
    - priority: int (optional)
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    text = payload.get('text')
    note = payload.get('note')
    list_id = payload.get('list_id')
    priority = payload.get('priority')

    if not text or not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text is required and must be a string")
    if list_id is None:
        raise HTTPException(status_code=400, detail="list_id is required")

    try:
        list_id = int(list_id)
    except Exception:
        raise HTTPException(status_code=400, detail="list_id must be an integer")

    if priority is not None:
        try:
            priority = int(priority)
        except Exception:
            raise HTTPException(status_code=400, detail="priority must be an integer")

    # Optional metadata
    metadata = payload.get('metadata') if isinstance(payload, dict) else None
    return await _create_todo_internal(text, note, list_id, priority, current_user, metadata=metadata)


async def _create_todo_internal(text: str, note: Optional[str], list_id: int, priority: Optional[int], current_user: User, *, metadata: dict | str | None = None):
    """Internal function to create a todo. Used by both JSON API and form-based endpoints."""
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
        from .utils import parse_text_to_rrule_string, parse_date_and_recurrence
        dtstart_val, rrule_str = parse_text_to_rrule_string(text or '')
        _, recdict = parse_date_and_recurrence(text or '')
        import json
        meta_json = json.dumps(recdict) if recdict else None
        # validate/encode metadata
        meta_col: str | None = None
        try:
            meta_col = validate_metadata_for_storage(metadata)
        except Exception:
            meta_col = None
        todo = Todo(text=clean_text, note=note, list_id=list_id, priority=priority, recurrence_rrule=rrule_str or None, recurrence_meta=meta_json, recurrence_dtstart=dtstart_val, metadata_json=meta_col)
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
    # Log WindowEvent todo creation to help map test-created ids to calendar traces
    try:
        if todo_resp and todo_resp.get('text', '').startswith('WindowEvent'):
            logger.info(f"POST /todos created WindowEvent todo id={todo_id_val} title={todo_resp.get('text')}")
    except Exception:
        logger.debug('failed to log WindowEvent todo creation')
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
        
        # Transform completions to object format expected by client
        # Fetch completion types for this list to map IDs to names
        qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == todo.list_id).order_by(CompletionType.id.asc()))
        completion_types = qct.all()
        
        # Create completion object keyed by completion type names
        completion_obj = {}
        completion_type_map = {ct.id: ct.name for ct in completion_types}
        for comp in completions:
            type_name = completion_type_map.get(comp["completion_type_id"])
            if type_name:
                completion_obj[type_name] = comp["done"]
        
        await sess.refresh(todo)
        return _serialize_todo(todo, completion_obj)


@app.patch("/todos/{todo_id}")
async def update_todo(todo_id: int, request: Request, current_user: User = Depends(require_login)):
    """
    Update a todo. Expects JSON payload with optional fields:
    - text: str
    - note: str
    - list_id: int
    - priority: int
    - completed: bool
    - pinned: bool
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    return await _update_todo_internal(todo_id, payload, current_user)


async def _update_todo_internal(todo_id: int, payload: dict, current_user: User):
    """Internal function to update a todo. Used by both JSON API and form-based endpoints."""
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")

        # Verify ownership through list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # capture original parent list for potential move
        old_list_id = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None

        # Update fields from payload
        if 'text' in payload:
            text = payload['text']
            if text is not None:
                # Strip inline hashtags from saved text; tags will be managed separately
                try:
                    todo.text = remove_hashtags_from_text(text.lstrip())
                except Exception:
                    todo.text = text

        if 'note' in payload:
            todo.note = payload['note']

        if 'priority' in payload:
            priority = payload['priority']
            if priority is None or (isinstance(priority, str) and str(priority).strip() == ''):
                todo.priority = None
            else:
                try:
                    todo.priority = int(priority)
                except Exception:
                    raise HTTPException(status_code=400, detail="priority must be an integer")

        if 'sort_links' in payload:
            try:
                v = payload['sort_links']
                if isinstance(v, str):
                    vv = v.strip().lower()
                    todo.sort_links = vv in ('1', 'true', 'yes', 'on')
                else:
                    todo.sort_links = bool(v)
            except Exception:
                # ignore invalid value
                pass

        if 'completed' in payload:
            completed = payload['completed']
            if completed is not None:
                # Check if a specific completion type was provided
                completion_type_id = payload.get('completion_type_id')
                if completion_type_id is not None:
                    # Handle specific completion type
                    try:
                        completion_type_id = int(completion_type_id)
                    except Exception:
                        raise HTTPException(status_code=400, detail="completion_type_id must be an integer")
                    
                    # Verify the completion type exists and belongs to the correct list
                    qct = await sess.scalars(select(CompletionType).where(CompletionType.id == completion_type_id).where(CompletionType.list_id == todo.list_id))
                    ct = qct.first()
                    if not ct:
                        raise HTTPException(status_code=404, detail="completion type not found")
                    
                    # Check if completion record exists
                    qtc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == todo_id).where(TodoCompletion.completion_type_id == completion_type_id))
                    completion = qtc.first()
                    if not completion:
                        completion = TodoCompletion(todo_id=todo_id, completion_type_id=completion_type_id, done=bool(completed))
                        sess.add(completion)
                    else:
                        completion.done = bool(completed)
                        sess.add(completion)
                else:
                    # Handle legacy completion (default completion type)
                    qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == todo.list_id).where(CompletionType.name == 'default'))
                    default_ct = qct.first()
                    if not default_ct:
                        # Create default completion type if it doesn't exist
                        default_ct = CompletionType(name="default", list_id=todo.list_id)
                        sess.add(default_ct)
                        await sess.commit()
                        await sess.refresh(default_ct)
                    
                    # Check if completion record exists
                    qtc = await sess.exec(select(TodoCompletion).where(TodoCompletion.todo_id == todo_id).where(TodoCompletion.completion_type_id == default_ct.id))
                    completion = qtc.first()
                    if not completion:
                        completion = TodoCompletion(todo_id=todo_id, completion_type_id=default_ct.id, done=bool(completed))
                        sess.add(completion)
                    else:
                        completion.done = bool(completed)
                        sess.add(completion)

        if 'pinned' in payload:
            pinned = payload['pinned']
            if pinned is not None:
                todo.pinned = bool(pinned)

        # metadata update (dict or null clears). Ignore invalid types.
        if 'metadata' in payload:
            try:
                todo.metadata_json = validate_metadata_for_storage(payload.get('metadata'))
            except Exception:
                # leave unchanged on validation error
                pass

        # Allow toggling search_ignored via JSON payload (optional)
        if 'search_ignored' in payload:
            try:
                todo.search_ignored = bool(payload['search_ignored'])
            except Exception:
                pass

        if 'list_id' in payload:
            list_id = payload['list_id']
            if list_id is not None:
                # ensure the target list exists
                ql = await sess.exec(select(ListState).where(ListState.id == list_id))
                lst = ql.first()
                if not lst:
                    raise HTTPException(status_code=404, detail="target list not found")
                # enforce ownership rules: only owners or public lists allowed
                user_id = current_user.id
                if lst.owner_id not in (None, user_id):
                    raise HTTPException(status_code=403, detail="forbidden")
                todo.list_id = list_id

        # If text or note changed, recompute recurrence metadata
        if 'text' in payload or 'note' in payload:
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

        # Transform completions to object format expected by client
        # Fetch completion types for this list to map IDs to names
        qct = await sess.scalars(select(CompletionType).where(CompletionType.list_id == todo.list_id).order_by(CompletionType.id.asc()))
        completion_types = qct.all()
        
        # Create completion object keyed by completion type names
        completion_obj = {}
        completion_type_map = {ct.id: ct.name for ct in completion_types}
        for comp in completions:
            type_name = completion_type_map.get(comp["completion_type_id"])
            if type_name:
                completion_obj[type_name] = comp["done"]

        # Precompute response dict before further commits
        todo_resp = _serialize_todo(todo, completion_obj)

        # Handle hashtags if text or note was updated
        provided_new_tags = []
        if 'text' in payload:
            provided_new_tags += extract_hashtags(payload['text'])
        if 'note' in payload and payload['note']:
            provided_new_tags += extract_hashtags(payload['note'])

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
        # Detach any sublists owned by this todo so they don't dangle
        try:
            await sess.exec(sqlalchemy_update(ListState).where(ListState.parent_todo_id == todo_id).values(parent_todo_id=None))
            await sess.commit()
        except Exception:
            await sess.rollback()
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


async def _delete_todo_internal(todo_id: int, current_user):
    """Internal function to delete a todo, used by client_json_api.py"""
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
        # Detach any sublists owned by this todo so they don't dangle
        try:
            await sess.exec(sqlalchemy_update(ListState).where(ListState.parent_todo_id == todo_id).values(parent_todo_id=None))
            await sess.commit()
        except Exception:
            await sess.rollback()
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
    # If the client asked for JSON, return minimal JSON; otherwise redirect back to the list/todo
    accept = (request.headers.get('Accept') or '')
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if todo and getattr(todo, 'list_id', None):
            if 'application/json' in accept.lower():
                return JSONResponse({'ok': True, 'id': todo.id, 'pinned': todo.pinned})
            return RedirectResponse(url=f'/html_no_js/lists/{todo.list_id}#todo-{todo_id}', status_code=303)
    # fallback: return JSON or redirect to the todo page
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': todo_id, 'pinned': pinned_bool})
    return RedirectResponse(url=f'/html_no_js/todos/{todo_id}', status_code=303)


# ===== Move UI (mark+move) =====
@app.get('/html_no_js/move', response_class=HTMLResponse)
async def html_move_ui(request: Request, current_user: User = Depends(require_login)):
    """Render the move UI page (client-side JS reads marked items from localStorage)."""
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    client_tz = await get_session_timezone(request)
    return TEMPLATES.TemplateResponse(request, 'move.html', {"request": request, "csrf_token": csrf_token, "client_tz": client_tz})


def _ensure_owner_list(lst: ListState | None, user: User):
    if not lst:
        raise HTTPException(status_code=404, detail='list not found')
    if lst.owner_id not in (None, user.id):
        raise HTTPException(status_code=403, detail='forbidden')


def _ensure_owner_todo_parent_list(todo: Todo | None, lst: ListState | None, user: User):
    if not todo:
        raise HTTPException(status_code=404, detail='todo not found')
    # todo must have a parent list and that list must be owned by the current user (or public)
    if not lst:
        raise HTTPException(status_code=404, detail='parent list not found')
    if lst.owner_id not in (None, user.id):
        raise HTTPException(status_code=403, detail='forbidden')


async def _next_position_for_parent(sess, *, parent_todo_id: int | None = None, parent_list_id: int | None = None) -> int:
    if parent_todo_id is not None:
        q = await sess.scalars(select(ListState.parent_todo_position).where(ListState.parent_todo_id == parent_todo_id))
        positions = [p[0] if isinstance(p, (tuple, list)) else p for p in q.fetchall()]
        try:
            return (max([pp for pp in positions if pp is not None]) + 1) if positions else 0
        except Exception:
            return 0
    if parent_list_id is not None:
        q = await sess.scalars(select(ListState.parent_list_position).where(ListState.parent_list_id == parent_list_id))
        positions = [p[0] if isinstance(p, (tuple, list)) else p for p in q.fetchall()]
        try:
            return (max([pp for pp in positions if pp is not None]) + 1) if positions else 0
        except Exception:
            return 0
    return 0


@app.post('/html_no_js/move/list_to_todo')
async def html_move_list_to_todo(request: Request, source_list_id: int = Form(...), target_todo_id: int = Form(...), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        src = await sess.get(ListState, source_list_id)
        _ensure_owner_list(src, current_user)
        todo = await sess.get(Todo, target_todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # ensure user owns the todo via its parent list
        tl = await sess.get(ListState, todo.list_id)
        _ensure_owner_todo_parent_list(todo, tl, current_user)
        # Guard: prevent creating a cycle by placing a list under a todo that belongs to the same list subtree.
        # Immediate self-cycle: todo currently belongs to the source list.
        try:
                if todo.list_id is not None and int(todo.list_id) == int(source_list_id):
                    return _redirect_or_json(request, f'/html_no_js/lists/{source_list_id}')
        except Exception:
            pass
        # Ascend from the todo's current list through parent_list/parent_todo chain; if we encounter source_list_id, this move would create a cycle.
        try:
            seen = 0
            cur_list_id = int(todo.list_id) if getattr(todo, 'list_id', None) is not None else None
            while cur_list_id is not None and seen < 200:
                if int(cur_list_id) == int(source_list_id):
                    # would create a cycle
                    return _redirect_or_json(request, f'/html_no_js/lists/{source_list_id}')
                cur_list = await sess.get(ListState, cur_list_id)
                if not cur_list:
                    break
                if getattr(cur_list, 'parent_list_id', None) is not None:
                    cur_list_id = int(cur_list.parent_list_id)
                    seen += 1
                    continue
                ptid = getattr(cur_list, 'parent_todo_id', None)
                if ptid is not None:
                    pt = await sess.get(Todo, int(ptid))
                    if not pt:
                        break
                    cur_list_id = int(pt.list_id) if getattr(pt, 'list_id', None) is not None else None
                    seen += 1
                    continue
                break
        except Exception:
            # If traversal fails, proceed (better UX than throwing) – server still safe from obvious self-cycle.
            pass
        # reparent list under the todo
        src.parent_list_id = None
        src.parent_list_position = None
        src.parent_todo_id = target_todo_id
        src.parent_todo_position = await _next_position_for_parent(sess, parent_todo_id=target_todo_id)
        src.modified_at = now_utc()
        sess.add(src)
        # touch involved lists
        try:
            await _touch_list_modified(sess, todo.list_id)
        except Exception:
            pass
        await sess.commit()
        accept = (request.headers.get('Accept') or '')
        # prefer JSON for AJAX clients, otherwise redirect to the todo page
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'moved_to_todo': target_todo_id, 'source_list': source_list_id})
        return RedirectResponse(url=f'/html_no_js/todos/{target_todo_id}', status_code=303)


@app.post('/html_no_js/move/list_to_list')
async def html_move_list_to_list(request: Request, source_list_id: int = Form(...), target_list_id: int = Form(...), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        src = await sess.get(ListState, source_list_id)
        _ensure_owner_list(src, current_user)
        dst = await sess.get(ListState, target_list_id)
        _ensure_owner_list(dst, current_user)
        # Prevent moving a list into itself
        if int(source_list_id) == int(target_list_id):
            # no-op; redirect back to the list page
            return _redirect_or_json(request, f'/html_no_js/lists/{source_list_id}')
        # Prevent cycles: if target is a descendant of source, moving source under target would create a cycle.
        try:
            seen = 0
            cur = dst
            while cur is not None and getattr(cur, 'parent_list_id', None) is not None and seen < 100:
                if int(cur.parent_list_id) == int(source_list_id):
                    # would create a cycle; reject politely
                    return _redirect_or_json(request, f'/html_no_js/lists/{source_list_id}')
                seen += 1
                try:
                    cur = await sess.get(ListState, cur.parent_list_id)
                except Exception:
                    break
        except Exception:
            # on any error, fall through; better to proceed than break UX
            pass
        # reparent list under list
        src.parent_todo_id = None
        src.parent_todo_position = None
        src.parent_list_id = target_list_id
        src.parent_list_position = await _next_position_for_parent(sess, parent_list_id=target_list_id)
        src.modified_at = now_utc()
        sess.add(src)
        # touch destination list
        try:
            await _touch_list_modified(sess, target_list_id)
        except Exception:
            pass
        await sess.commit()
    return _redirect_or_json(request, f'/html_no_js/lists/{target_list_id}')


@app.post('/html_no_js/move/todo_to_list')
async def html_move_todo_to_list(request: Request, source_todo_id: int = Form(...), target_list_id: int = Form(...), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        todo = await sess.get(Todo, source_todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # check ownership via current parent list
        cur_parent = await sess.get(ListState, todo.list_id)
        _ensure_owner_todo_parent_list(todo, cur_parent, current_user)
        # ensure destination list is owned by user/public
        dst = await sess.get(ListState, target_list_id)
        _ensure_owner_list(dst, current_user)
        # No-op guard: moving into the same list
        try:
            if todo.list_id is not None and int(todo.list_id) == int(target_list_id):
                return _redirect_or_json(request, f'/html_no_js/lists/{target_list_id}#todo-{source_todo_id}')
        except Exception:
            pass
        # Cycle guard: prevent moving a todo into a list that is inside this todo's own subtree (descendant).
        # Ascend from the target list via (parent_list_id) or (parent_todo_id -> that todo's list_id) until root; if we hit this todo id, reject.
        try:
            seen = 0
            cur_list_id = int(target_list_id)
            while cur_list_id is not None and seen < 200:
                lst = await sess.get(ListState, cur_list_id)
                if not lst:
                    break
                ptid = getattr(lst, 'parent_todo_id', None)
                if ptid is not None and int(ptid) == int(source_todo_id):
                    # target is within the subtree of the source todo -> cycle
                    return _redirect_or_json(request, f'/html_no_js/lists/{todo.list_id}#todo-{source_todo_id}')
                # climb up
                if getattr(lst, 'parent_list_id', None) is not None:
                    cur_list_id = int(lst.parent_list_id)
                    seen += 1
                    continue
                if ptid is not None:
                    pt = await sess.get(Todo, int(ptid))
                    if not pt:
                        break
                    cur_list_id = int(pt.list_id) if getattr(pt, 'list_id', None) is not None else None
                    seen += 1
                    continue
                break
        except Exception:
            # On traversal error, continue; the most problematic self-cases are already handled by no-op guard.
            pass
        old_list_id = int(todo.list_id)
        todo.list_id = target_list_id
        todo.modified_at = now_utc()
        sess.add(todo)
        try:
            await _touch_list_modified(sess, target_list_id)
            if old_list_id != target_list_id:
                await _touch_list_modified(sess, old_list_id)
        except Exception:
            pass
        await sess.commit()
        tid = int(todo.id)
    return _redirect_or_json(request, f'/html_no_js/lists/{target_list_id}#todo-{tid}')


@app.post('/html_no_js/move/clear')
async def html_move_clear_parent(request: Request, item_type: str = Form(...), item_id: int = Form(...), current_user: User = Depends(require_login)):
    """Clear ownership: for lists, detach from any parent; for todos, move to server default list if available."""
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        if item_type == 'list':
            lst = await sess.get(ListState, item_id)
            _ensure_owner_list(lst, current_user)
            lst.parent_todo_id = None
            lst.parent_todo_position = None
            lst.parent_list_id = None
            lst.parent_list_position = None
            lst.modified_at = now_utc()
            sess.add(lst)
            await sess.commit()
            return _redirect_or_json(request, f'/html_no_js/lists/{item_id}')
        elif item_type == 'todo':
            todo = await sess.get(Todo, item_id)
            if not todo:
                raise HTTPException(status_code=404, detail='todo not found')
            cur_parent = await sess.get(ListState, todo.list_id)
            _ensure_owner_todo_parent_list(todo, cur_parent, current_user)
            # find default list
            qs = await sess.exec(select(ServerState))
            ss = qs.first()
            if not ss or not ss.default_list_id:
                raise HTTPException(status_code=400, detail='no default list configured')
            dst = await sess.get(ListState, ss.default_list_id)
            _ensure_owner_list(dst, current_user)
            old_list_id = int(todo.list_id)
            todo.list_id = int(dst.id)
            todo.modified_at = now_utc()
            sess.add(todo)
            try:
                await _touch_list_modified(sess, todo.list_id)
                if old_list_id != todo.list_id:
                    await _touch_list_modified(sess, old_list_id)
            except Exception:
                pass
            await sess.commit()
            accept = (request.headers.get('Accept') or '')
            if 'application/json' in accept.lower():
                return JSONResponse({'ok': True, 'moved_to_list': int(dst.id), 'todo_id': todo.id})
            return RedirectResponse(url=f'/html_no_js/lists/{dst.id}#todo-{todo.id}', status_code=303)
        else:
            raise HTTPException(status_code=400, detail='invalid item_type')


@app.post("/todos/{todo_id}/defer")
async def defer_todo(todo_id: int, hours: int, current_user: User = Depends(require_login)):
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # enforce visibility via parent list (owner or public)
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if lst and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
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


async def _complete_todo_impl(todo_id: int, completion_type: str = "default", done: bool = True, current_user: User = None):
    """Internal implementation for completing a todo. `current_user` must be a User instance when called internally."""
    async with async_session() as sess:
        q = select(Todo).where(Todo.id == todo_id)
        res = await sess.exec(q)
        todo = res.first()
        if not todo:
            raise HTTPException(status_code=404, detail="todo not found")
        # enforce ownership/visibility via parent list: allow owner or public list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        # current_user must be provided by callers (endpoint wrapper or internal callers)
        if lst and current_user is not None and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
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


@app.post("/todos/{todo_id}/complete")
async def complete_todo(todo_id: int, completion_type: str = "default", done: bool = True, current_user: User = Depends(require_login)):
    """Endpoint wrapper that injects `current_user` and calls internal implementation."""
    return await _complete_todo_impl(todo_id=todo_id, completion_type=completion_type, done=done, current_user=current_user)


@app.post("/admin/undefer")
async def undefer_due(current_user: User = Depends(require_login)):
    # Allow any authenticated user by default to trigger undefer to retain
    # backward compatibility with tests. If the environment variable
    # REQUIRE_ADMIN_FOR_UNDEFER is set (to any truthy value), then restrict
    # this endpoint to admins only.
    if os.getenv('REQUIRE_ADMIN_FOR_UNDEFER'):
        try:
            if not getattr(current_user, 'is_admin', False):
                raise HTTPException(status_code=403, detail='forbidden')
        except Exception:
            raise HTTPException(status_code=403, detail='forbidden')
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


def _serialize_todo(todo: Todo, completions: list[dict] | dict | None = None) -> dict:
    def _fmt(dt):
        if not dt:
            return None
        # If the DB returned a naive datetime, assume UTC and attach tzinfo
        if dt.tzinfo is None:
            from datetime import timezone as _tz

            dt = dt.replace(tzinfo=_tz.utc)
        return dt.isoformat()

    # Determine completion status from completions data
    completed = False
    if completions:
        if isinstance(completions, list):
            # Handle array format: [{"completion_type_id": 169, "done": true}]
            for comp in completions:
                if comp.get('completion_type_id') and comp.get('done', False):
                    completed = True
                    break
        elif isinstance(completions, dict):
            # Handle object format: {"default": true}
            completed = any(completions.values())

    return {
        "id": todo.id,
        "text": todo.text,
        "pinned": getattr(todo, 'pinned', False),
        "note": todo.note,
        "created_at": _fmt(todo.created_at),
        "modified_at": _fmt(todo.modified_at),
        "deferred_until": _fmt(todo.deferred_until),
        "list_id": todo.list_id,
        "completions": completions or ([] if isinstance(completions, list) else {}),
        "priority": getattr(todo, 'priority', None),
        "completed": completed,  # Add the completed field for client compatibility
        "metadata": parse_metadata_json(getattr(todo, 'metadata_json', None)),
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
    "hide_icons": getattr(lst, 'hide_icons', False),
    # number of uncompleted todos in this list (computed by caller when available)
    "uncompleted_count": getattr(lst, 'uncompleted_count', None),
    "metadata": parse_metadata_json(getattr(lst, 'metadata_json', None)),
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
        q = select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
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
            q_prev_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
                or_(ListState.created_at > first.created_at,
                    and_(ListState.created_at == first.created_at, ListState.id > first.id))
            ).limit(1)
            r_prev = await sess.exec(q_prev_exists)
            has_prev = r_prev.first() is not None
            # is there anything older than last?
            q_next_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
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
        # Diagnostic: snapshot the tag_map keys and a small sample for debugging
        try:
            from .utils import index_calendar_assert
            # limit sample size to avoid huge logs
            sample = {str(k): v for i, (k, v) in enumerate(tag_map.items()) if i < 20}
            index_calendar_assert('tag_map_snapshot', extra={'list_ids_requested': [str(x) for x in list_ids], 'tag_map_count': len(tag_map), 'tag_map_sample': sample})
        except Exception:
            pass
        for l in lists:
            list_rows.append({
                "id": l.id,
                "name": l.name,
                "completed": l.completed,
                "owner_id": l.owner_id,
                "created_at": l.created_at,
                "modified_at": getattr(l, 'modified_at', None),
                "category_id": l.category_id,
                "priority": getattr(l, 'priority', None),
                # placeholder for any higher-priority uncompleted todo in this list
                "override_priority": None,
                "hashtags": tag_map.get(l.id, []),
                # placeholder for number of uncompleted todos; will be filled below
                "uncompleted_count": None,
                "hide_icons": getattr(l, 'hide_icons', False),
            })
        # Diagnostic: record the hashtags attached to each list_row (sample up to 50)
        try:
            from .utils import index_calendar_assert
            sample_rows = [{ 'id': r['id'], 'hashtags': r.get('hashtags', []) } for r in list_rows[:50]]
            index_calendar_assert('list_rows_hashtags', extra={'sample_list_rows': sample_rows})
        except Exception:
            pass

        # Respect a show_all_tags preference coming from the client. The client
        # can send this as a query param ?show_all_tags=1 or via a cookie
        # 'show_all_tags'. When enabled, include hashtags present on todos in
        # each list in the server-side `combined` values (same behaviour as
        # the client-side "Show All Tags" DOM updater).
        show_all_tags = False
        try:
            qval = request.query_params.get('show_all_tags')
            if qval is not None:
                show_all_tags = str(qval).lower() in ('1', 'true', 'yes', 'on')
            else:
                cval = request.cookies.get('show_all_tags')
                if cval is not None:
                    show_all_tags = str(cval).lower() in ('1', 'true', 'yes', 'on')
        except Exception:
            show_all_tags = False

        # If show_all_tags is enabled, fetch all todo ids for the lists on the
        # page and the hashtags attached to those todos so we can include them
        # in the combined list below. Also fetch immediate sublists and their
        # hashtags (both list-level and todo-level) so parent lists can surface
        # those tags as well.
        list_todo_map: dict[int, list[int]] = {}
        todo_tags_map: dict[int, list[str]] = {}
        # Immediate sublists: parent_list_id -> [sublist_id, ...]
        parent_to_sublist_ids: dict[int, list[int]] = {}
        # Sublist list-level hashtags: sublist_id -> ["#tag", ...]
        sublist_list_tags_map: dict[int, list[str]] = {}
        # Todos that belong to a given sublist: sublist_id -> [todo_id, ...]
        sublist_todo_ids_by_list: dict[int, list[int]] = {}
        # Hashtags attached to sublist todos: todo_id -> ["#tag", ...]
        sublist_todo_tags_map: dict[int, list[str]] = {}
        try:
            if list_ids:
                # Order todos deterministically by id so merged todo-tags preserve
                # a stable order that matches the API's ordering used elsewhere.
                qtl = await sess.exec(
                    select(Todo.id, Todo.list_id).where(Todo.list_id.in_(list_ids)).order_by(Todo.id.asc())
                )
                tlrows = qtl.all()
                todo_ids_all: list[int] = []
                for tid, lid in tlrows:
                    try:
                        tid_i = int(tid)
                        lid_i = int(lid)
                    except Exception:
                        continue
                    list_todo_map.setdefault(lid_i, []).append(tid_i)
                    todo_ids_all.append(tid_i)
                if todo_ids_all:
                    qth_all = await sess.exec(select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_ids_all)))
                    for tid, tag in qth_all.all():
                        try:
                            tid_i = int(tid)
                        except Exception:
                            continue
                        if isinstance(tag, str) and tag:
                            todo_tags_map.setdefault(tid_i, []).append(tag)

                # Gather immediate sublists of the lists on this page
                qsubs_ids = await sess.exec(
                    select(ListState.id, ListState.parent_list_id)
                    .where(ListState.parent_list_id.in_(list_ids))
                    .order_by(ListState.id.asc())
                )
                sublist_pairs = qsubs_ids.all()
                sublist_ids: list[int] = []
                for sid, parent_id in sublist_pairs:
                    try:
                        sid_i = int(sid)
                        pid_i = int(parent_id)
                    except Exception:
                        continue
                    parent_to_sublist_ids.setdefault(pid_i, []).append(sid_i)
                    sublist_ids.append(sid_i)

                if sublist_ids:
                    # List-level hashtags on sublists
                    qslh = await sess.exec(
                        select(ListHashtag.list_id, Hashtag.tag)
                        .where(ListHashtag.list_id.in_(sublist_ids))
                        .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                    )
                    for lid, tag in qslh.all():
                        try:
                            lid_i = int(lid)
                        except Exception:
                            continue
                        if isinstance(tag, str) and tag:
                            sublist_list_tags_map.setdefault(lid_i, []).append(tag)

                    # Todos within sublists
                    qstl = await sess.exec(
                        select(Todo.id, Todo.list_id)
                        .where(Todo.list_id.in_(sublist_ids))
                        .order_by(Todo.id.asc())
                    )
                    stlrows = qstl.all()
                    sub_todo_ids_all: list[int] = []
                    for tid, lid in stlrows:
                        try:
                            tid_i = int(tid)
                            lid_i = int(lid)
                        except Exception:
                            continue
                        sublist_todo_ids_by_list.setdefault(lid_i, []).append(tid_i)
                        sub_todo_ids_all.append(tid_i)
                    if sub_todo_ids_all:
                        qsth = await sess.exec(
                            select(TodoHashtag.todo_id, Hashtag.tag)
                            .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                            .where(TodoHashtag.todo_id.in_(sub_todo_ids_all))
                        )
                        for tid, tag in qsth.all():
                            try:
                                tid_i = int(tid)
                            except Exception:
                                continue
                            if isinstance(tag, str) and tag:
                                sublist_todo_tags_map.setdefault(tid_i, []).append(tag)
        except Exception:
            # Non-fatal; if any DB issue occurs, simply behave as if flag disabled
            list_todo_map = {}
            todo_tags_map = {}
            parent_to_sublist_ids = {}
            sublist_list_tags_map = {}
            sublist_todo_ids_by_list = {}
            sublist_todo_tags_map = {}
    # Compute server-side combined lists:
    # - combined: respects show_all_tags (list-only when false; adds todo/sublists when true)
    # - combined_full: always includes list, todo, and immediate sublist tags (for client use)
        # merging any hashtags present on the ORM `lists` objects and the
        # `hashtags` attached to the converted `list_rows`. This ensures the
        # template can reliably render SSR anchors without depending on client
        # DOM population or subtle template scoping issues.
        try:
            # Map ORM list id -> ORM object for quick lookup
            list_obj_map = {l.id: l for l in lists} if lists else {}
            for row in list_rows:
                base = []
                try:
                    orm = list_obj_map.get(row.get('id'))
                    # Pull any hashtags persisted directly on the ORM object first
                    if orm is not None and getattr(orm, 'hashtags', None):
                        for t in getattr(orm, 'hashtags'):
                            if t and t not in base:
                                base.append(t)
                except Exception:
                    pass
                # Merge hashtags attached to the list_row dict (from tag_map)
                try:
                    for t in row.get('hashtags', []) or []:
                        if t and t not in base:
                            base.append(t)
                except Exception:
                    pass
                # Build combined_full (always include todos and immediate sublists)
                combined_full = list(base)
                try:
                    lid = row.get('id')
                    todo_ids_for_list = list_todo_map.get(int(lid), []) if lid is not None else []
                    for tid in todo_ids_for_list:
                        for t in todo_tags_map.get(tid, []):
                            if t and t not in combined_full:
                                combined_full.append(t)
                    sub_ids = parent_to_sublist_ids.get(int(lid), []) if lid is not None else []
                    for sid in sub_ids:
                        for t in sublist_list_tags_map.get(sid, []):
                            if t and t not in combined_full:
                                combined_full.append(t)
                        for stid in sublist_todo_ids_by_list.get(sid, []):
                            for t in sublist_todo_tags_map.get(stid, []):
                                if t and t not in combined_full:
                                    combined_full.append(t)
                except Exception:
                    pass
                # Build SSR-visible combined respecting show_all_tags
                combined = list(base)
                if show_all_tags:
                    for t in combined_full:
                        if t and t not in combined:
                            combined.append(t)
                row['combined'] = combined
                row['combined_full'] = combined_full
        except Exception:
            pass
        # Determine highest uncompleted todo priority per list (if any)
        try:
            todo_q = await sess.exec(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(list_ids)).where(Todo.priority != None))
            todo_rows = todo_q.all()
            todo_map: dict[int, list[tuple[int,int]]] = {}
            todo_ids = []
            for tid, lid, pri in todo_rows:
                todo_map.setdefault(lid, []).append((tid, pri))
                todo_ids.append(tid)
            completed_ids = set()
            if todo_ids:
                try:
                    qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                    cres = await sess.exec(qcomp)
                    completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                except Exception:
                    completed_ids = set()
            # compute highest uncompleted priority per list
            # Also include immediate sublists' list-level priority (not their todos)
            parent_to_sublists: dict[int, list[dict]] = {}
            try:
                if list_ids:
                    qsubs = await sess.exec(select(ListState.id, ListState.priority, ListState.parent_list_id, ListState.completed).where(ListState.parent_list_id.in_(list_ids)))
                    for sid, spri, parent_id, scompleted in qsubs.all():
                        try:
                            pid = int(parent_id)
                        except Exception:
                            continue
                        parent_to_sublists.setdefault(pid, []).append({'id': sid, 'priority': spri, 'completed': bool(scompleted) if scompleted is not None else False})
            except Exception:
                parent_to_sublists = {}

            for row in list_rows:
                lid = row.get('id')
                candidates = todo_map.get(lid, [])
                max_p = None
                # consider uncompleted todos in the list
                for tid, pri in candidates:
                    if tid in completed_ids:
                        continue
                    try:
                        if pri is None:
                            continue
                        pv = int(pri)
                    except Exception:
                        continue
                    if max_p is None or pv > max_p:
                        max_p = pv
                # consider immediate sublists' list-level priorities (require sublist not completed)
                try:
                    subs = parent_to_sublists.get(lid, [])
                    for s in subs:
                        sp = s.get('priority')
                        scomp = s.get('completed')
                        if sp is None:
                            continue
                        # skip sublists that are marked completed
                        if scomp:
                            continue
                        try:
                            spv = int(sp)
                        except Exception:
                            continue
                        if max_p is None or spv > max_p:
                            max_p = spv
                except Exception:
                    pass
                if max_p is not None:
                    row['override_priority'] = max_p

            # If any lists on this page are marked as user collations, also
            # consider the highest uncompleted priority among todos linked to
            # those lists via ItemLink (src_type='list', tgt_type='todo').
            try:
                # Fetch collation list ids for this user that are present on this page
                quc = await sess.exec(select(UserCollation.list_id).where(UserCollation.user_id == owner_id))
                uc_ids_all = [r[0] if isinstance(r, (list, tuple)) else int(getattr(r, 'list_id', r)) for r in quc.all()]
                collation_ids = [lid for lid in uc_ids_all if lid in list_ids]
                if collation_ids:
                    # Map list_id -> linked todo ids
                    qlinks = await sess.exec(
                        select(ItemLink.src_id, ItemLink.tgt_id)
                        .where(ItemLink.src_type == 'list')
                        .where(ItemLink.tgt_type == 'todo')
                        .where(ItemLink.src_id.in_(collation_ids))
                        .where(ItemLink.owner_id == owner_id)
                    )
                    link_rows = qlinks.all()
                    coll_link_map: dict[int, list[int]] = {}
                    linked_todo_ids: list[int] = []
                    for src_id, tgt_id in link_rows:
                        try:
                            sid = int(src_id); tid = int(tgt_id)
                        except Exception:
                            continue
                        coll_link_map.setdefault(sid, []).append(tid)
                        linked_todo_ids.append(tid)
                    if linked_todo_ids:
                        # Fetch priorities for linked todos
                        qtp = await sess.exec(select(Todo.id, Todo.priority).where(Todo.id.in_(linked_todo_ids)).where(Todo.priority != None))
                        pri_map: dict[int, int] = {}
                        for tid, pri in qtp.all():
                            try:
                                if pri is None:
                                    continue
                                pri_map[int(tid)] = int(pri)
                            except Exception:
                                continue
                        # Determine completed set for linked todos as well
                        try:
                            qlcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(linked_todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                            linked_completed = set(r[0] if isinstance(r, tuple) else r for r in qlcomp.all())
                        except Exception:
                            linked_completed = set()
                        # Update override_priority for collation lists considering linked todos
                        for row in list_rows:
                            lid = row.get('id')
                            if lid not in coll_link_map:
                                continue
                            max_p = row.get('override_priority')
                            try:
                                max_p = int(max_p) if max_p is not None else None
                            except Exception:
                                max_p = None
                            for tid in coll_link_map.get(lid, []):
                                if tid in linked_completed:
                                    continue
                                pv = pri_map.get(tid)
                                if pv is None:
                                    continue
                                if max_p is None or pv > max_p:
                                    max_p = pv
                            if max_p is not None:
                                row['override_priority'] = max_p
            except Exception:
                # Non-fatal; if any error occurs, skip collation-aware boost
                pass
        except Exception:
            # failure computing overrides should not break index rendering
            pass
        except Exception:
            # failure computing overrides should not break index rendering
            pass
        # Compute uncompleted todo counts per list (exclude completions marked done)
        try:
            qcnt = await sess.exec(select(Todo.list_id, func.count(Todo.id)).where(Todo.list_id.in_(list_ids)).outerjoin(TodoCompletion, TodoCompletion.todo_id == Todo.id).group_by(Todo.list_id))
            counts = {}
            for lid, cnt in qcnt.all():
                counts[lid] = int(cnt or 0)
            # Subtract completed todos (if completion records mark them done)
            try:
                qcomp = await sess.exec(select(Todo.id, Todo.list_id).join(TodoCompletion, TodoCompletion.todo_id == Todo.id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(Todo.list_id.in_(list_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                for tid, lid in qcomp.all():
                    counts[lid] = max(0, counts.get(lid, 0) - 1)
            except Exception:
                pass

            # Collation-aware: add linked, uncompleted todos to marked collation lists
            extra_counts: dict[int, int] = {}
            try:
                quc = await sess.exec(select(UserCollation.list_id).where(UserCollation.user_id == owner_id))
                uc_ids_all = [r[0] if isinstance(r, (list, tuple)) else int(getattr(r, 'list_id', r)) for r in quc.all()]
                collation_ids = [lid for lid in uc_ids_all if lid in list_ids]
                if collation_ids:
                    qlinks = await sess.exec(
                        select(ItemLink.src_id, ItemLink.tgt_id)
                        .where(ItemLink.src_type == 'list')
                        .where(ItemLink.tgt_type == 'todo')
                        .where(ItemLink.src_id.in_(collation_ids))
                        .where(ItemLink.owner_id == owner_id)
                    )
                    link_rows = qlinks.all()
                    coll_link_map: dict[int, set[int]] = {}
                    all_linked_ids: set[int] = set()
                    for src_id, tgt_id in link_rows:
                        try:
                            sid = int(src_id); tid = int(tgt_id)
                        except Exception:
                            continue
                        all_linked_ids.add(tid)
                        coll_link_map.setdefault(sid, set()).add(tid)
                    if all_linked_ids:
                        # Completed set among linked todos
                        try:
                            qlcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(list(all_linked_ids))).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                            linked_completed = set(r[0] if isinstance(r, tuple) else r for r in qlcomp.all())
                        except Exception:
                            linked_completed = set()
                        # Map linked todo -> its source list to avoid double-counting if same-list
                        qtl = await sess.exec(select(Todo.id, Todo.list_id).where(Todo.id.in_(list(all_linked_ids))))
                        todo_src_map: dict[int, int] = {int(tid): int(lid) for tid, lid in qtl.all()}
                        for lid, tids in coll_link_map.items():
                            extra = 0
                            for tid in set(tids):
                                if tid in linked_completed:
                                    continue
                                # don't double-count todos that already belong to this list
                                if todo_src_map.get(int(tid)) == int(lid):
                                    continue
                                extra += 1
                            if extra:
                                extra_counts[int(lid)] = extra
            except Exception:
                # ignore collation-aware extras on error
                pass

            for row in list_rows:
                lid = row.get('id')
                base = counts.get(lid, 0)
                row['uncompleted_count'] = base + extra_counts.get(lid, 0)
        except Exception:
            pass
        # group lists by category for easier template rendering
        lists_by_category: dict[int, list[dict]] = {}
        # Within each category, sort lists by priority (if set) ascending, then by created_at desc
        for row in list_rows:
            cid = row.get('category_id') or 0
            lists_by_category.setdefault(cid, []).append(row)
        for cid, rows in lists_by_category.items():
            # When sorting by priority, ignore priority for lists that are completed
            def _list_sort_key(r):
                # consider override_priority (highest uncompleted todo priority) if present
                lp = r.get('priority') if (r.get('priority') is not None and not r.get('completed')) else None
                op = r.get('override_priority') if (r.get('override_priority') is not None and not r.get('completed')) else None
                # use the higher of op and lp (None means absent)
                if lp is None and op is None:
                    p = None
                elif lp is None:
                    p = op
                elif op is None:
                    p = lp
                else:
                    p = lp if lp >= op else op
                # primary: presence of priority (priority items first), then priority value (asc), then newest created_at
                return (0 if p is not None else 1, p or 0, -(r.get('created_at').timestamp() if r.get('created_at') else 0))
            rows.sort(key=_list_sort_key)
        # fetch categories ordered by position
        categories = []
        try:
                qcat = select(Category).order_by(Category.position.asc())
                cres = await sess.exec(qcat)
                categories = [{'id': c.id, 'name': c.name, 'position': c.position, 'sort_alphanumeric': getattr(c, 'sort_alphanumeric', False)} for c in cres.all()]
        except Exception:
            categories = []
        # Also fetch pinned todos from lists visible to this user (owned or public)
        pinned_todos = []
        try:
            # visible lists: owned by user or public (owner_id is NULL)
            qvis = select(ListState).where(((ListState.owner_id == owner_id) | (ListState.owner_id == None))).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
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
                        'priority': getattr(t, 'priority', None),
                        'override_priority': getattr(t, 'override_priority', None) if hasattr(t, 'override_priority') else None,
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
                # determine completed state for pinned todos using the list's 'default' completion type
                try:
                    if pin_ids:
                        qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(pin_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                        cres = await sess.exec(qcomp)
                        completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                    else:
                        completed_ids = set()
                except Exception:
                    completed_ids = set()
                for p in pinned_todos:
                    p['completed'] = p['id'] in completed_ids
        except Exception:
            # if DB lacks the pinned column or some error occurs, show no pinned todos
            pinned_todos = []
        # Compute high-priority items (priority >= 7) from visible lists
        high_priority_todos = []
        high_priority_lists = []
        try:
            if vis_ids:
                # Todos with priority >=7 in visible lists, newest modified first
                qhp = select(Todo).where(Todo.list_id.in_(vis_ids)).where(Todo.priority >= 7).order_by(Todo.modified_at.desc())
                hpres = await sess.exec(qhp)
                hp_rows = hpres.all()
                lm = {l.id: l.name for l in vis_lists}
                high_priority_todos = [
                    {
                        'id': t.id,
                        'text': t.text,
                        'list_id': t.list_id,
                        'list_name': lm.get(t.list_id),
                        'modified_at': (t.modified_at.isoformat() if getattr(t, 'modified_at', None) else None),
                        'created_at': (t.created_at.isoformat() if getattr(t, 'created_at', None) else None),
                        'priority': getattr(t, 'priority', None),
                        'override_priority': getattr(t, 'override_priority', None) if hasattr(t, 'override_priority') else None,
                    }
                    for t in hp_rows
                ]
                # Lists whose priority or override_priority is >=7
                hpl = [r for r in list_rows if ((r.get('override_priority') is not None and r.get('override_priority') >= 7) or (r.get('priority') is not None and r.get('priority') >= 7))]
                high_priority_lists = hpl
                # Merge todos and lists into a single sorted list by effective priority.
                # Effective priority is override_priority when present, otherwise priority.
                high_priority_items = []
                # include created_at on todos so we can sort by creation when requested
                for t in high_priority_todos:
                    eff = None
                    # compute numeric priority values when possible
                    lp = None
                    op = None
                    if t.get('priority') is not None:
                        try:
                            lp = int(t.get('priority'))
                        except Exception:
                            lp = None
                    if t.get('override_priority') is not None:
                        try:
                            op = int(t.get('override_priority'))
                        except Exception:
                            op = None
                    # primary sort value is the maximum of normal and override priorities
                    if lp is None and op is None:
                        eff = None
                    else:
                        eff = lp if (op is None or (lp is not None and lp >= op)) else op
                    high_priority_items.append({
                        'kind': 'todo',
                        'id': t.get('id'),
                        'text': t.get('text'),
                        'list_id': t.get('list_id'),
                        'list_name': t.get('list_name'),
                        'modified_at': t.get('modified_at'),
                        'created_at': t.get('created_at') if t.get('created_at', None) else None,
                        'priority': t.get('priority'),
                        'override_priority': t.get('override_priority'),
                        'effective_priority': eff,
                    })
                # avoid duplicating a list when a high-priority todo from the
                # same list is already present in high_priority_items.
                # Build a set of list_ids already present from todo entries.
                todo_list_ids = set(it.get('list_id') for it in high_priority_items if it.get('kind') == 'todo')

                for lst in high_priority_lists:
                    if lst.get('id') in todo_list_ids:
                        # a high-priority todo from this list is already shown;
                        # skip adding the list entry to avoid duplication.
                        continue
                    # compute primary as max(priority, override_priority) similar to todos
                    lp = lst.get('priority') if lst.get('priority') is not None else None
                    op = lst.get('override_priority') if lst.get('override_priority') is not None else None
                    try:
                        lpv = int(lp) if lp is not None else None
                    except Exception:
                        lpv = None
                    try:
                        opv = int(op) if op is not None else None
                    except Exception:
                        opv = None
                    if lpv is None and opv is None:
                        eff = None
                    else:
                        eff = lpv if (opv is None or (lpv is not None and lpv >= opv)) else opv
                    high_priority_items.append({
                        'kind': 'list',
                        'id': lst.get('id'),
                        'name': lst.get('name'),
                        'uncompleted_count': lst.get('uncompleted_count'),
                        'priority': lst.get('priority'),
                        'override_priority': lst.get('override_priority'),
                        'effective_priority': eff,
                        'modified_at': lst.get('modified_at'),
                    })
                # Decide secondary date key based on the UI list-sort-order preference.
                # Preference can be passed as a query param 'hp_secondary' or read from the index_list_sort_order cookie.
                # Accept values: 'created' or 'modified' (default 'modified').
                hp_secondary = request.query_params.get('hp_secondary') or None
                if not hp_secondary:
                    # try cookie
                    hp_secondary = None
                    cookie_val = None
                    try:
                        cookie_val = request.cookies.get('index_list_sort_order')
                    except Exception:
                        cookie_val = None
                    if cookie_val:
                        hp_secondary = 'created' if cookie_val == 'created' else 'modified'
                if not hp_secondary:
                    hp_secondary = 'modified'

                def _date_value(item):
                    # returns epoch-like comparable int (larger = newer)
                    from datetime import datetime
                    v = None
                    if hp_secondary == 'created':
                        v = item.get('created_at') or item.get('modified_at')
                    else:
                        v = item.get('modified_at') or item.get('created_at')
                    if not v:
                        return 0
                    try:
                        # support ISO strings
                        if isinstance(v, str):
                            dt = datetime.fromisoformat(v)
                        else:
                            dt = v
                        return int(dt.timestamp())
                    except Exception:
                        return 0

                def _priority_value(item):
                    ep = item.get('effective_priority')
                    try:
                        return int(ep) if ep is not None else -1
                    except Exception:
                        return -1

                try:
                    # Sort: primary by effective priority desc (None -> last), secondary by chosen date desc, tie-breaker by name/id
                    high_priority_items.sort(key=lambda it: (_priority_value(it), _date_value(it), it.get('name') or it.get('text') or it.get('id') or 0), reverse=True)
                except Exception:
                    # fallback: leave unsorted if any issue
                    pass
                # expose merged list to templates
                # keep existing high_priority_todos/high_priority_lists too for compatibility
                context_high_priority_items = high_priority_items
            else:
                context_high_priority_items = []
        except Exception:
            high_priority_todos = []
            high_priority_lists = []
        # compute a small, near-term calendar summary for the index page (reuse core calendar endpoint logic)
        calendar_occurrences = []
        try:
            from datetime import timedelta as _td, timezone as _tz
            from .utils import now_utc
            now = now_utc()
            try:
                days = int(getattr(config, 'INDEX_CALENDAR_DAYS', 1))
            except Exception:
                days = 1
            cal_start = now - _td(days=days)
            cal_end = now + _td(days=days)
            # Call the calendar_occurrences endpoint function directly to share all optimizations
            try:
                from importlib import import_module as _imp_mod
                _mod = _imp_mod('app.main')
                _start_iso = cal_start.astimezone(_tz.utc).isoformat().replace('+00:00', 'Z')
                _end_iso = cal_end.astimezone(_tz.utc).isoformat().replace('+00:00', 'Z')
                resp = await _mod.calendar_occurrences(
                    request,
                    start=_start_iso,
                    end=_end_iso,
                    tz=None,
                    expand=True,
                    max_per_item=3,
                    max_total=20,
                    include_ignored=False,
                    current_user=current_user
                )
                calendar_occurrences = list(resp.get('occurrences', []))
            except Exception:
                calendar_occurrences = []
        except Exception:
            calendar_occurrences = []

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
        user_default_cat = getattr(current_user, 'default_category_id', None)
        def _render_and_log(template_name: str, ctx: dict):
            """Render template to string, log its length, and return a TemplateResponse.

            This helper uses the Jinja environment to render the template to a
            string first so we can log the final payload length in server
            logs for diagnostics. If rendering to string fails for any reason,
            fall back to returning the regular TemplateResponse to preserve
            prior behaviour.
            """
            try:
                # Render to string first so we can measure length
                tpl = TEMPLATES.env.get_template(template_name)
                rendered = tpl.render(**ctx)
                try:
                    logger.info('html_index: rendered template=%s length=%d', template_name, len(rendered))
                except Exception:
                    logger.info('html_index: rendered template=%s length=?', template_name)
                # Return a TemplateResponse using the already-rendered string
                # via HTMLResponse to ensure cookies/headers set downstream
                from fastapi.responses import HTMLResponse
                resp = HTMLResponse(content=rendered)
                # Ensure request context is available to template consumers
                try:
                    # copy common headers or cookies if needed later; keep minimal
                    pass
                except Exception:
                    pass
                return resp
            except Exception:
                # On any failure, log and fall back to TemplateResponse
                logger.exception('html_index: failed to pre-render %s; falling back', template_name)
                return TEMPLATES.TemplateResponse(request, template_name, ctx)

        if force_ios:
            logger.info('html_index: rendering index_ios_safari (forced) ua=%s', ua[:200])
            circ_map = {1:'①',2:'②',3:'③',4:'④',5:'⑤',6:'⑥',7:'⑦',8:'⑧',9:'⑨',10:'⑩'}
            ctx = {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "high_priority_todos": high_priority_todos, "high_priority_lists": high_priority_lists, "high_priority_items": context_high_priority_items if 'context_high_priority_items' in locals() else [], "cursors": cursors, "categories": categories, "calendar_occurrences": calendar_occurrences, "user_default_category_id": user_default_cat, "show_all_tags": show_all_tags, "circ": circ_map}
            return _render_and_log("index_ios_safari.html", ctx)
        if is_ios_safari(request):
            logger.info('html_index: rendering index_ios_safari (ua-detected) ua=%s', ua[:200])
            circ_map = {1:'①',2:'②',3:'③',4:'④',5:'⑤',6:'⑥',7:'⑦',8:'⑧',9:'⑨',10:'⑩'}
            ctx = {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "high_priority_todos": high_priority_todos, "high_priority_lists": high_priority_lists, "high_priority_items": context_high_priority_items if 'context_high_priority_items' in locals() else [], "cursors": cursors, "categories": categories, "calendar_occurrences": calendar_occurrences, "user_default_category_id": user_default_cat, "show_all_tags": show_all_tags, "circ": circ_map}
            return _render_and_log("index_ios_safari.html", ctx)

        logger.info('html_index: rendering index.html (default) ua=%s', ua[:200])
        try:
            from .utils import index_calendar_assert
            # write full calendar_occurrences before compacting/sorting so
            # we can see the exact set the index is using.
            try:
                occs_for_log = [
                    {'item_type': o.get('item_type'), 'id': o.get('id'), 'occurrence_dt': o.get('occurrence_dt'), 'rrule': o.get('rrule', ''), 'is_recurring': o.get('is_recurring', False)}
                    for o in calendar_occurrences
                ]
                index_calendar_assert('calendar_occurrences_snapshot', extra={'occurrences': occs_for_log})
            except Exception:
                index_calendar_assert('calendar_occurrences_snapshot', extra={'count': len(calendar_occurrences)})

            has_441 = any((o.get('item_type') == 'todo' and str(o.get('id')) == '441') for o in calendar_occurrences)
            index_calendar_assert('index_render_snapshot', extra={'count': len(calendar_occurrences), 'has_441': bool(has_441)})

            try:
                lc = {}
                for lr in (list_rows or []):
                    try:
                        lid = int(lr.get('id'))
                    except Exception:
                        continue
                    comb = lr.get('combined') if isinstance(lr, dict) else None
                    if comb is None:
                        comb = lr.get('hashtags') if isinstance(lr, dict) else None
                    lc[lid] = comb or []
                index_calendar_assert('list_combined_snapshot', extra={'list_combined': lc})
            except Exception:
                pass
        except Exception:
            # swallow any logging errors but continue to render
            pass

        circ_map = {1:'①',2:'②',3:'③',4:'④',5:'⑤',6:'⑥',7:'⑦',8:'⑧',9:'⑨',10:'⑩'}
        ctx = {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "high_priority_todos": high_priority_todos, "high_priority_lists": high_priority_lists, "high_priority_items": context_high_priority_items if 'context_high_priority_items' in locals() else [], "cursors": cursors, "categories": categories, "calendar_occurrences": calendar_occurrences, "user_default_category_id": user_default_cat, "show_all_tags": show_all_tags, "circ": circ_map}
        try:
            # non-fatal pre-render to log payload length for diagnostics
            try:
                tpl = TEMPLATES.env.get_template("index.html")
                rendered = tpl.render(**ctx)
                try:
                    logger.info('html_index: rendered template=%s length=%d', 'index.html', len(rendered))
                except Exception:
                    logger.info('html_index: rendered template=%s length=?', 'index.html')
            except Exception:
                logger.exception('html_index: pre-render for logging failed')
        except Exception:
            # ignore logging errors
            pass

        return TEMPLATES.TemplateResponse(request, "index.html", ctx)

    except Exception:
        # swallow unexpected errors during template-selection/logging but allow the
        # handler to continue (mirrors previous defensive style in this module)
        pass

async def _prepare_index_context(request: Request, current_user: User | None) -> dict:
    """Prepare the context dict used by index templates (shared by html_no_js and html_tailwind).

    Returns the same keys used by the existing html_index handler so templates can render.
    """
    # Mirror the behavior in html_index: if user missing, return safe defaults
    if not current_user:
        try:
            client_tz = await get_session_timezone(request)
        except Exception:
            client_tz = None
        return {
            "request": request,
            "lists": [],
            "lists_by_category": {},
            "csrf_token": None,
            "client_tz": client_tz,
            "pinned_todos": [],
            "high_priority_todos": [],
            "high_priority_lists": [],
            "cursors": None,
            "categories": [],
            "calendar_occurrences": [],
            "user_default_category_id": None,
            "current_user": None,
        }

    # Reuse the same per-page/cursor logic as html_index (simplified: one page)
    per_page = 50
    dir_param = request.query_params.get('dir', 'next')
    cursor_created_at_str = request.query_params.get('cursor_created_at')
    cursor_id_str = request.query_params.get('cursor_id')
    cursor_dt = None
    cursor_id = None
    if cursor_created_at_str and cursor_id_str:
        try:
            from datetime import datetime
            cursor_dt = datetime.fromisoformat(cursor_created_at_str)
            cursor_id = int(cursor_id_str)
        except Exception:
            cursor_dt, cursor_id = None, None

    async with async_session() as sess:
        owner_id = current_user.id
        q = select(ListState).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
        if cursor_dt is not None and cursor_id is not None:
            if dir_param == 'prev':
                q = q.where(or_(ListState.created_at > cursor_dt,
                                and_(ListState.created_at == cursor_dt, ListState.id > cursor_id)))
            else:
                q = q.where(or_(ListState.created_at < cursor_dt,
                                and_(ListState.created_at == cursor_dt, ListState.id < cursor_id)))
        q = q.order_by(ListState.created_at.desc(), ListState.id.desc()).limit(per_page)
        res_page = await sess.exec(q)
        lists = res_page.all()

        # cursors
        has_prev = False
        has_next = False
        next_cursor_created_at = None
        next_cursor_id = None
        prev_cursor_created_at = None
        prev_cursor_id = None
        if lists:
            first = lists[0]
            last = lists[-1]
            prev_cursor_created_at, prev_cursor_id = first.created_at, first.id
            next_cursor_created_at, next_cursor_id = last.created_at, last.id
            q_prev_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
                or_(ListState.created_at > first.created_at,
                    and_(ListState.created_at == first.created_at, ListState.id > first.id))
            ).limit(1)
            r_prev = await sess.exec(q_prev_exists)
            has_prev = r_prev.first() is not None
            q_next_exists = select(ListState.id).where(ListState.owner_id == owner_id).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None).where(
                or_(ListState.created_at < last.created_at,
                    and_(ListState.created_at == last.created_at, ListState.id < last.id))
            ).limit(1)
            r_next = await sess.exec(q_next_exists)
            has_next = r_next.first() is not None

        # convert to dict rows and fill tags/overrides/counts, pinned todos, calendar occurrences
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
                "priority": getattr(l, 'priority', None),
                "override_priority": None,
                "hashtags": tag_map.get(l.id, []),
                "uncompleted_count": None,
                "hide_icons": getattr(l, 'hide_icons', False),
            })

        # Diagnostic: record the hashtags attached to each list_row (sample up to 50)
        try:
            from .utils import index_calendar_assert
            sample_rows = [{ 'id': r['id'], 'hashtags': r.get('hashtags', []) } for r in list_rows[:50]]
            index_calendar_assert('list_rows_hashtags', extra={'sample_list_rows': sample_rows})
        except Exception:
            pass

        # (reuse existing logic: compute override_priority and uncompleted counts)
        try:
            # Find immediate sublists whose parent_list_id is one of the lists on this page.
            try:
                qsubs = await sess.exec(select(ListState.id, ListState.parent_list_id).where(ListState.parent_list_id.in_(list_ids)))
                subs_rows = qsubs.all()
            except Exception:
                subs_rows = []
            parent_to_sublists: dict[int, list[int]] = {}
            sublist_ids: list[int] = []
            for sid, pid in subs_rows:
                try:
                    sid_i = int(sid); pid_i = int(pid)
                except Exception:
                    continue
                parent_to_sublists.setdefault(pid_i, []).append(sid_i)
                sublist_ids.append(sid_i)

            # Include todos from the immediate sublists when searching for highest priorities.
            combined_list_ids = list_ids + sublist_ids if sublist_ids else list_ids

            todo_q = await sess.exec(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(combined_list_ids)).where(Todo.priority != None))
            todo_rows = todo_q.all()
            todo_map: dict[int, list[tuple[int,int]]] = {}
            todo_ids = []
            for tid, lid, pri in todo_rows:
                todo_map.setdefault(lid, []).append((tid, pri))
                todo_ids.append(tid)
            completed_ids = set()
            if todo_ids:
                try:
                    qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                    cres = await sess.exec(qcomp)
                    completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                except Exception:
                    completed_ids = set()

            for row in list_rows:
                lid = row.get('id')
                # Start with todos directly in the list
                candidates = list(todo_map.get(lid, []))
                # Add todos from immediate sublists
                for sid in parent_to_sublists.get(lid, []):
                    candidates.extend(todo_map.get(sid, []))
                max_p = None
                for tid, pri in candidates:
                    if tid in completed_ids:
                        continue
                    try:
                        if pri is None:
                            continue
                        pv = int(pri)
                    except Exception:
                        continue
                    if max_p is None or pv > max_p:
                        max_p = pv
                if max_p is not None:
                    row['override_priority'] = max_p

            # Collation-aware: include highest uncompleted priority among
            # todos linked into user collation lists on this page.
            try:
                quc = await sess.exec(select(UserCollation.list_id).where(UserCollation.user_id == owner_id))
                uc_ids_all = [r[0] if isinstance(r, (list, tuple)) else int(getattr(r, 'list_id', r)) for r in quc.all()]
                collation_ids = [lid for lid in uc_ids_all if lid in list_ids]
                if collation_ids:
                    qlinks = await sess.exec(
                        select(ItemLink.src_id, ItemLink.tgt_id)
                        .where(ItemLink.src_type == 'list')
                        .where(ItemLink.tgt_type == 'todo')
                        .where(ItemLink.src_id.in_(collation_ids))
                        .where(ItemLink.owner_id == owner_id)
                    )
                    link_rows = qlinks.all()
                    coll_link_map: dict[int, list[int]] = {}
                    linked_todo_ids: list[int] = []
                    for src_id, tgt_id in link_rows:
                        try:
                            sid = int(src_id); tid = int(tgt_id)
                        except Exception:
                            continue
                        coll_link_map.setdefault(sid, []).append(tid)
                        linked_todo_ids.append(tid)
                    if linked_todo_ids:
                        qtp = await sess.exec(select(Todo.id, Todo.priority).where(Todo.id.in_(linked_todo_ids)).where(Todo.priority != None))
                        pri_map: dict[int, int] = {}
                        for tid, pri in qtp.all():
                            try:
                                if pri is None:
                                    continue
                                pri_map[int(tid)] = int(pri)
                            except Exception:
                                continue
                        try:
                            qlcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(linked_todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                            linked_completed = set(r[0] if isinstance(r, tuple) else r for r in qlcomp.all())
                        except Exception:
                            linked_completed = set()
                        for row in list_rows:
                            lid = row.get('id')
                            if lid not in coll_link_map:
                                continue
                            max_p = row.get('override_priority')
                            try:
                                max_p = int(max_p) if max_p is not None else None
                            except Exception:
                                max_p = None
                            for tid in coll_link_map.get(lid, []):
                                if tid in linked_completed:
                                    continue
                                pv = pri_map.get(tid)
                                if pv is None:
                                    continue
                                if max_p is None or pv > max_p:
                                    max_p = pv
                            if max_p is not None:
                                row['override_priority'] = max_p
            except Exception:
                pass
        except Exception:
            pass
        except Exception:
            pass

        try:
            qcnt = await sess.exec(select(Todo.list_id, func.count(Todo.id)).where(Todo.list_id.in_(list_ids)).outerjoin(TodoCompletion, TodoCompletion.todo_id == Todo.id).group_by(Todo.list_id))
            counts = {}
            for lid, cnt in qcnt.all():
                counts[lid] = int(cnt or 0)
            try:
                qcomp = await sess.exec(select(Todo.id, Todo.list_id).join(TodoCompletion, TodoCompletion.todo_id == Todo.id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(Todo.list_id.in_(list_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                for tid, lid in qcomp.all():
                    counts[lid] = max(0, counts.get(lid, 0) - 1)
            except Exception:
                pass

            # Collation-aware extras
            extra_counts: dict[int, int] = {}
            try:
                quc = await sess.exec(select(UserCollation.list_id).where(UserCollation.user_id == owner_id))
                uc_ids_all = [r[0] if isinstance(r, (list, tuple)) else int(getattr(r, 'list_id', r)) for r in quc.all()]
                collation_ids = [lid for lid in uc_ids_all if lid in list_ids]
                if collation_ids:
                    qlinks = await sess.exec(
                        select(ItemLink.src_id, ItemLink.tgt_id)
                        .where(ItemLink.src_type == 'list')
                        .where(ItemLink.tgt_type == 'todo')
                        .where(ItemLink.src_id.in_(collation_ids))
                        .where(ItemLink.owner_id == owner_id)
                    )
                    link_rows = qlinks.all()
                    coll_link_map: dict[int, set[int]] = {}
                    all_linked_ids: set[int] = set()
                    for src_id, tgt_id in link_rows:
                        try:
                            sid = int(src_id); tid = int(tgt_id)
                        except Exception:
                            continue
                        coll_link_map.setdefault(sid, set()).add(tid)
                        all_linked_ids.add(tid)
                    if all_linked_ids:
                        try:
                            qlcomp = await sess.exec(select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(list(all_linked_ids))).where(CompletionType.name == 'default').where(TodoCompletion.done == True))
                            linked_completed = set(r[0] if isinstance(r, tuple) else r for r in qlcomp.all())
                        except Exception:
                            linked_completed = set()
                        qtl = await sess.exec(select(Todo.id, Todo.list_id).where(Todo.id.in_(list(all_linked_ids))))
                        todo_src_map: dict[int, int] = {int(tid): int(lid) for tid, lid in qtl.all()}
                        for lid, tids in coll_link_map.items():
                            extra = 0
                            for tid in set(tids):
                                if tid in linked_completed:
                                    continue
                                if todo_src_map.get(int(tid)) == int(lid):
                                    continue
                                extra += 1
                            if extra:
                                extra_counts[int(lid)] = extra
            except Exception:
                pass

            for row in list_rows:
                lid = row.get('id')
                row['uncompleted_count'] = counts.get(lid, 0) + extra_counts.get(lid, 0)
        except Exception:
            pass

        lists_by_category: dict[int, list[dict]] = {}
        for row in list_rows:
            cid = row.get('category_id') or 0
            lists_by_category.setdefault(cid, []).append(row)
        for cid, rows in lists_by_category.items():
            def _list_sort_key(r):
                lp = r.get('priority') if (r.get('priority') is not None and not r.get('completed')) else None
                op = r.get('override_priority') if (r.get('override_priority') is not None and not r.get('completed')) else None
                if lp is None and op is None:
                    p = None
                elif lp is None:
                    p = op
                elif op is None:
                    p = lp
                else:
                    p = lp if lp >= op else op
                return (0 if p is not None else 1, p or 0, -(r.get('created_at').timestamp() if r.get('created_at') else 0))
            rows.sort(key=_list_sort_key)

        categories = []
        try:
            qcat = select(Category).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position, 'sort_alphanumeric': getattr(c, 'sort_alphanumeric', False)} for c in cres.all()]
        except Exception:
            categories = []

        pinned_todos = []
        try:
            qvis = select(ListState).where(((ListState.owner_id == owner_id) | (ListState.owner_id == None))).where(ListState.parent_todo_id == None).where(ListState.parent_list_id == None)
            rvis = await sess.exec(qvis)
            vis_lists = rvis.all()
            vis_ids = [l.id for l in vis_lists]
            if vis_ids:
                qp = select(Todo).where(Todo.pinned == True).where(Todo.list_id.in_(vis_ids)).order_by(Todo.modified_at.desc())
                pres = await sess.exec(qp)
                pin_rows = pres.all()
                lm = {l.id: l.name for l in vis_lists}
                pinned_todos = [
                    {
                        'id': t.id,
                        'text': t.text,
                        'list_id': t.list_id,
                        'list_name': lm.get(t.list_id),
                        'modified_at': (t.modified_at.isoformat() if getattr(t, 'modified_at', None) else None),
                        'priority': getattr(t, 'priority', None),
                        'override_priority': getattr(t, 'override_priority', None) if hasattr(t, 'override_priority') else None,
                    }
                    for t in pin_rows
                ]
                pin_ids = [p['id'] for p in pinned_todos]
                if pin_ids:
                    qtp = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(pin_ids))
                    pres2 = await sess.exec(qtp)
                    pm = {}
                    for tid, tag in pres2.all():
                        pm.setdefault(tid, []).append(tag)
                    for p in pinned_todos:
                        p['tags'] = pm.get(p['id'], [])
                try:
                    if pin_ids:
                        qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(pin_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                        cres = await sess.exec(qcomp)
                        completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                    else:
                        completed_ids = set()
                except Exception:
                    completed_ids = set()
                for p in pinned_todos:
                    p['completed'] = p['id'] in completed_ids
        except Exception:
            pinned_todos = []

        # calendar occurrences (reuse logic from html_index)
        calendar_occurrences = []
        try:
            from datetime import timedelta as _td
            from .models import CompletedOccurrence, IgnoredScope
            from . import models
            from .utils import occurrence_hash, extract_dates_meta, resolve_yearless_date
            from .utils import now_utc
            from dateutil.rrule import rrulestr

            now = now_utc()
            try:
                days = int(getattr(config, 'INDEX_CALENDAR_DAYS', 1))
            except Exception:
                days = 1
            cal_start = now - _td(days=days)
            cal_end = now + _td(days=days)

            qc = await sess.exec(select(CompletedOccurrence).where(CompletedOccurrence.user_id == owner_id))
            done_rows = qc.all()
            done_set = set(r.occ_hash for r in done_rows)
            qi = await sess.exec(select(IgnoredScope).where(IgnoredScope.user_id == owner_id).where(IgnoredScope.active == True))
            ign_rows = qi.all()
            occ_ignore_hashes = set(r.scope_hash for r in ign_rows if getattr(r, 'scope_type', '') == 'occurrence' and r.scope_hash)
            list_ignore_ids = set(str(r.scope_key) for r in ign_rows if getattr(r, 'scope_type', '') == 'list')
            todo_from_scopes = [r for r in ign_rows if getattr(r, 'scope_type', '') == 'todo_from']

            try:
                qvis = select(models.ListState).where(((models.ListState.owner_id == owner_id) | (models.ListState.owner_id == None))).where(models.ListState.parent_todo_id == None).where(models.ListState.parent_list_id == None)
                rvis = await sess.exec(qvis)
                vis_lists = rvis.all()
            except Exception:
                vis_lists = []
            vis_ids = [l.id for l in vis_lists if l.id is not None]
            vis_todos = []
            if vis_ids:
                try:
                    base_q = select(models.Todo).where(models.Todo.list_id.in_(vis_ids))
                    if hasattr(models.Todo, 'completed'):
                        base_q = base_q.where(models.Todo.completed == False)
                    try:
                        if hasattr(models.Todo, 'calendar_ignored'):
                            base_q = base_q.where(models.Todo.calendar_ignored == False)
                    except Exception:
                        pass
                    try:
                        qtt = await sess.exec(base_q)
                        vis_todos = qtt.all()
                    except Exception:
                        qtt = await sess.exec(select(models.Todo).where(models.Todo.list_id.in_(vis_ids)))
                        vis_todos = [t for t in qtt.all() if not bool(getattr(t, 'calendar_ignored', False))]
                except Exception:
                    vis_todos = []

            # Emit a snapshot of visible todos/lists (ids only) for diagnostic tracing
            try:
                from .utils import index_calendar_assert
                index_calendar_assert('vis_snapshot', extra={'vis_todo_ids': [str(t.id) for t in vis_todos], 'vis_list_ids': [str(l.id) for l in vis_lists]})
            except Exception:
                pass

            def _occ_allowed(item_type, item_id, occ_dt, rrule_str, title=None, list_id=None):
                try:
                    if occ_dt.tzinfo is None:
                        occ_dt = occ_dt.replace(tzinfo=timezone.utc)
                    occ_hash = occurrence_hash(item_type, item_id, occ_dt, rrule_str or '', title)
                    if occ_hash in done_set:
                        return None
                    if occ_hash in occ_ignore_hashes:
                        return None
                    if item_type == 'list' and str(item_id) in list_ignore_ids:
                        return None
                    for r in todo_from_scopes:
                        try:
                            if str(item_id) != str(getattr(r, 'scope_key', '')):
                                continue
                            r_from = getattr(r, 'from_dt', None)
                            if r_from is None:
                                return None
                            if r_from.tzinfo is None:
                                r_from = r_from.replace(tzinfo=timezone.utc)
                            if occ_dt >= r_from:
                                return None
                        except Exception:
                            continue
                    return occ_hash
                except Exception:
                    return None

            for l in vis_lists:
                rec_rrule = getattr(l, 'recurrence_rrule', None)
                rec_dtstart = getattr(l, 'recurrence_dtstart', None)
                if rec_rrule:
                    try:
                        if rec_dtstart and rec_dtstart.tzinfo is None:
                            rec_dtstart = rec_dtstart.replace(tzinfo=timezone.utc)
                        r = rrulestr(rec_rrule, dtstart=rec_dtstart)
                        occs = list(r.between(cal_start, cal_end, inc=True))[:3]
                        for od in occs:
                            oh = _occ_allowed('list', l.id, od, rec_rrule, title=(l.name or ''), list_id=None)
                            if oh:
                                calendar_occurrences.append({'occurrence_dt': od.isoformat(), 'item_type': 'list', 'id': l.id, 'list_id': None, 'title': l.name, 'occ_hash': oh, 'is_recurring': True, 'rrule': rec_rrule})
                    except Exception:
                        pass
                try:
                    meta = extract_dates_meta(l.name or '')
                    for m in meta:
                        try:
                            if m.get('year_explicit'):
                                d = m.get('dt')
                                if d:
                                    try:
                                        from .utils import index_calendar_assert
                                        index_calendar_assert('list_candidate_evaluated', extra={'list_id': l.id, 'match_text': m.get('match_text'), 'candidate': d.isoformat(), 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                    except Exception:
                                        pass
                                if d and d >= cal_start and d <= cal_end:
                                    oh = _occ_allowed('list', l.id, d, '', title=(l.name or ''), list_id=None)
                                    if oh:
                                        calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'list', 'id': l.id, 'list_id': None, 'title': l.name, 'occ_hash': oh, 'is_recurring': False, 'rrule': ''})
                                    else:
                                        try:
                                            from .utils import index_calendar_assert
                                            index_calendar_assert('list_candidate_filtered', extra={'list_id': l.id, 'candidate': d.isoformat(), 'reason': 'occ_not_allowed'})
                                        except Exception:
                                            pass
                                else:
                                    try:
                                        from .utils import index_calendar_assert
                                        index_calendar_assert('list_candidate_out_of_window', extra={'list_id': l.id, 'candidate': d.isoformat() if d else None, 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                    except Exception:
                                        pass
                            else:
                                try:
                                    created = getattr(l, 'created_at', None) or now
                                    candidates = resolve_yearless_date(int(m.get('month')), int(m.get('day')), created, window_start=cal_start, window_end=cal_end)
                                    if isinstance(candidates, list):
                                        for d in candidates:
                                                try:
                                                    from .utils import index_calendar_assert
                                                    index_calendar_assert('list_yearless_candidate_evaluated', extra={'list_id': l.id, 'match_text': m.get('match_text'), 'candidate': d.isoformat(), 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                                except Exception:
                                                    pass
                                                if d and d >= cal_start and d <= cal_end:
                                                    oh = _occ_allowed('list', l.id, d, '', title=(l.name or ''), list_id=None)
                                                    if oh:
                                                        calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'list', 'id': l.id, 'list_id': None, 'title': l.name, 'occ_hash': oh, 'is_recurring': False, 'rrule': ''})
                                                    else:
                                                        try:
                                                            from .utils import index_calendar_assert
                                                            index_calendar_assert('list_candidate_filtered', extra={'list_id': l.id, 'candidate': d.isoformat(), 'reason': 'occ_not_allowed'})
                                                        except Exception:
                                                            pass
                                                else:
                                                    try:
                                                        from .utils import index_calendar_assert
                                                        index_calendar_assert('list_yearless_candidate_out_of_window', extra={'list_id': l.id, 'candidate': d.isoformat() if d else None})
                                                    except Exception:
                                                        pass
                                    else:
                                        d = candidates
                                        if d and d >= cal_start and d <= cal_end:
                                            oh = _occ_allowed('list', l.id, d, '', title=(l.name or ''), list_id=None)
                                            if oh:
                                                calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'list', 'id': l.id, 'list_id': None, 'title': l.name, 'occ_hash': oh, 'is_recurring': False, 'rrule': ''})
                                except Exception:
                                    pass
                        except Exception:
                            continue
                except Exception:
                    pass

            for t in vis_todos:
                try:
                    if bool(getattr(t, 'calendar_ignored', False)):
                        continue
                except Exception:
                    pass
                rec_rrule = getattr(t, 'recurrence_rrule', None)
                rec_dtstart = getattr(t, 'recurrence_dtstart', None)
                if rec_rrule:
                    try:
                        if rec_dtstart and rec_dtstart.tzinfo is None:
                            rec_dtstart = rec_dtstart.replace(tzinfo=timezone.utc)
                        r = rrulestr(rec_rrule, dtstart=rec_dtstart)
                        occs = list(r.between(cal_start, cal_end, inc=True))[:3]
                        for od in occs:
                            oh = _occ_allowed('todo', t.id, od, rec_rrule, title=(t.text or ''), list_id=t.list_id)
                            if oh:
                                calendar_occurrences.append({'occurrence_dt': od.isoformat(), 'item_type': 'todo', 'id': t.id, 'list_id': t.list_id, 'title': t.text, 'occ_hash': oh, 'is_recurring': True, 'rrule': rec_rrule, 'priority': getattr(t, 'priority', None)})
                    except Exception:
                        pass
                else:
                    try:
                        from .utils import parse_text_to_rrule, parse_text_to_rrule_string
                        r_obj, dtstart = parse_text_to_rrule(t.text + '\n' + (t.note or ''))
                        if r_obj is not None:
                            if dtstart and dtstart.tzinfo is None:
                                dtstart = dtstart.replace(tzinfo=timezone.utc)
                            occs = list(r_obj.between(cal_start, cal_end, inc=True))[:3]
                            _dt, rrule_str_local = parse_text_to_rrule_string(t.text + '\n' + (t.note or ''))
                            for od in occs:
                                oh = _occ_allowed('todo', t.id, od, rrule_str_local, title=(t.text or ''), list_id=t.list_id)
                                if oh:
                                    calendar_occurrences.append({'occurrence_dt': od.isoformat(), 'item_type': 'todo', 'id': t.id, 'list_id': t.list_id, 'title': t.text, 'occ_hash': oh, 'is_recurring': True, 'rrule': rrule_str_local, 'priority': getattr(t, 'priority', None)})
                    except Exception:
                        pass
                try:
                    meta = extract_dates_meta(t.text + '\n' + (t.note or ''))
                    for m in meta:
                        try:
                            if m.get('year_explicit'):
                                d = m.get('dt')
                                # evaluation log
                                if d:
                                    try:
                                        from .utils import index_calendar_assert
                                        index_calendar_assert('todo_candidate_evaluated', extra={'todo_id': t.id, 'match_text': m.get('match_text'), 'candidate': d.isoformat(), 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                    except Exception:
                                        pass
                                if d and d >= cal_start and d <= cal_end:
                                    oh = _occ_allowed('todo', t.id, d, '', title=(t.text or ''), list_id=t.list_id)
                                    if oh:
                                        calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'todo', 'id': t.id, 'list_id': t.list_id, 'title': t.text, 'occ_hash': oh, 'is_recurring': False, 'rrule': '', 'priority': getattr(t, 'priority', None)})
                                    else:
                                        try:
                                            from .utils import index_calendar_assert
                                            index_calendar_assert('todo_candidate_filtered', extra={'todo_id': t.id, 'candidate': d.isoformat(), 'reason': 'occ_not_allowed'})
                                        except Exception:
                                            pass
                                else:
                                    try:
                                        from .utils import index_calendar_assert
                                        index_calendar_assert('todo_candidate_out_of_window', extra={'todo_id': t.id, 'candidate': d.isoformat() if d else None, 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                    except Exception:
                                        pass
                            else:
                                try:
                                    created = getattr(t, 'created_at', None) or now
                                    candidates = resolve_yearless_date(int(m.get('month')), int(m.get('day')), created, window_start=cal_start, window_end=cal_end)
                                    if isinstance(candidates, list):
                                        for d in candidates:
                                            try:
                                                from .utils import index_calendar_assert
                                                index_calendar_assert('todo_yearless_candidate_evaluated', extra={'todo_id': t.id, 'match_text': m.get('match_text'), 'candidate': d.isoformat(), 'cal_start': cal_start.isoformat(), 'cal_end': cal_end.isoformat()})
                                            except Exception:
                                                pass
                                            if d and d >= cal_start and d <= cal_end:
                                                oh = _occ_allowed('todo', t.id, d, '', title=(t.text or ''), list_id=t.list_id)
                                                if oh:
                                                    calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'todo', 'id': t.id, 'list_id': t.list_id, 'title': t.text, 'occ_hash': oh, 'is_recurring': False, 'rrule': '', 'priority': getattr(t, 'priority', None)})
                                                else:
                                                    try:
                                                        from .utils import index_calendar_assert
                                                        index_calendar_assert('todo_candidate_filtered', extra={'todo_id': t.id, 'candidate': d.isoformat(), 'reason': 'occ_not_allowed'})
                                                    except Exception:
                                                        pass
                                            else:
                                                try:
                                                    from .utils import index_calendar_assert
                                                    index_calendar_assert('todo_yearless_candidate_out_of_window', extra={'todo_id': t.id, 'candidate': d.isoformat() if d else None})
                                                except Exception:
                                                    pass
                                    else:
                                        d = candidates
                                        if d and d >= cal_start and d <= cal_end:
                                            oh = _occ_allowed('todo', t.id, d, '', title=(t.text or ''), list_id=t.list_id)
                                            if oh:
                                                calendar_occurrences.append({'occurrence_dt': d.isoformat(), 'item_type': 'todo', 'id': t.id, 'list_id': t.list_id, 'title': t.text, 'occ_hash': oh, 'is_recurring': False, 'rrule': '', 'priority': getattr(t, 'priority', None)})
                                except Exception:
                                    pass
                        except Exception:
                            continue
                except Exception:
                    pass

            calendar_occurrences.sort(key=lambda x: x.get('occurrence_dt'))
            calendar_occurrences = calendar_occurrences[:20]
        except Exception:
            calendar_occurrences = []

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

    csrf_token = None
    if current_user:
        from .auth import create_csrf_token
        csrf_token = create_csrf_token(current_user.username)
    client_tz = await get_session_timezone(request)
    user_default_cat = getattr(current_user, 'default_category_id', None)
    return {"request": request, "lists": list_rows, "lists_by_category": lists_by_category, "csrf_token": csrf_token, "client_tz": client_tz, "pinned_todos": pinned_todos, "cursors": cursors, "categories": categories, "calendar_occurrences": calendar_occurrences, "user_default_category_id": user_default_cat, "current_user": current_user}


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
            cres = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .order_by(Category.position.asc(), Category.id.asc())
            )
            cats = cres.all()
            return {'categories': [{'id': c.id, 'name': c.name, 'position': c.position, 'sort_alphanumeric': getattr(c, 'sort_alphanumeric', False)} for c in cats], 'user_default_category_id': getattr(current_user, 'default_category_id', None)}
        except Exception:
            return {'categories': []}


class ExecFnRequest(BaseModel):
    name: str
    args: Optional[dict] = None
    context: Optional[dict] = None


@app.post('/api/exec-fn')
async def api_exec_fn(request: Request, payload: ExecFnRequest, current_user: User = Depends(require_login)):
    """Execute a small, server-registered function by name.

    Currently supports: 'search.multi' which accepts args: {
        tags: list[str] | str (comma-separated),
        mode: 'and'|'or' (default 'and'),
        include_list_todos: bool,
        exclude_completed: bool (default True)
    }
    """
    # Allow bearer-token API clients (Authorization header) without CSRF.
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = body.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    # debug log incoming exec requests
    try:
        logger.debug('api_exec_fn payload received', extra={'user': getattr(current_user, 'username', None), 'payload': payload.dict()})
    except Exception:
        logger.debug('api_exec_fn payload received (unable to serialize payload)')

    name = (payload.name or '').strip()
    args = payload.args or {}

    # Implement search.multi
    if name == 'search.multi':
        # parse tags
        tags = args.get('tags') or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        if not isinstance(tags, list):
            raise HTTPException(status_code=400, detail='tags must be a list or comma-separated string')
        # normalize tags using existing helper; reject invalid tags
        norm_tags: list[str] = []
        for t in tags:
            # allow quoted tags or tags with spaces by normalizing internal whitespace to underscores
            tt = t
            if isinstance(tt, str):
                tt = tt.strip()
                if (tt.startswith('"') and tt.endswith('"')) or (tt.startswith("'") and tt.endswith("'")):
                    tt = tt[1:-1]
                # replace runs of whitespace by removing them so tag becomes alphanumeric
                if re.search(r"\s", tt):
                    tt = re.sub(r"\s+", '', tt)
            try:
                nt = normalize_hashtag(tt)
            except Exception:
                raise HTTPException(status_code=400, detail=f'invalid tag: {t}')
            norm_tags.append(nt)

        mode = str(args.get('mode', 'and') or 'and').lower()
        include_list_todos = bool(args.get('include_list_todos', False))
        exclude_completed = True if ('exclude_completed' not in args) else bool(args.get('exclude_completed'))

        results = {'lists': [], 'todos': []}
        async with async_session() as sess:
            owner_id = current_user.id
            # Lists matching tags
            lists_acc: dict[int, ListState] = {}
            if norm_tags:
                # For each tag, collect matching list ids, then combine per mode
                per_tag_lists: list[set[int]] = []
                for tag in norm_tags:
                    q = (
                        select(ListState)
                        .join(ListHashtag, ListHashtag.list_id == ListState.id)
                        .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                        .where(ListState.owner_id == owner_id)
                        .where(Hashtag.tag == tag)
                    )
                    rows = (await sess.exec(q)).all()
                    per_tag_lists.append({int(r.id) for r in rows})
                    for r in rows:
                        lists_acc.setdefault(int(r.id), r)
                if per_tag_lists:
                    if mode == 'and':
                        ids = set.intersection(*per_tag_lists) if per_tag_lists else set()
                    else:
                        ids = set.union(*per_tag_lists) if per_tag_lists else set()
                    # prune lists_acc to only selected ids
                    lists_acc = {i: lists_acc[i] for i in ids if i in lists_acc}
            # prepare lists result
            results['lists'] = [
                {'id': l.id, 'name': l.name, 'completed': getattr(l, 'completed', False)}
                for l in lists_acc.values()
                if not (exclude_completed and getattr(l, 'completed', False))
            ]

            # Todos matching tags
            # Determine visible list ids (same as html_search)
            qvis = select(ListState).where((ListState.owner_id == owner_id) | (ListState.owner_id == None))
            rvis = await sess.exec(qvis)
            vis_ids = [l.id for l in rvis.all()]
            todos_acc: dict[int, Todo] = {}
            if vis_ids and norm_tags:
                # For each tag, collect matching todo ids and combine per mode
                per_tag_todos: list[set[int]] = []
                for tag in norm_tags:
                    q = (
                        select(Todo)
                        .join(TodoHashtag, TodoHashtag.todo_id == Todo.id)
                        .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                        .where(Todo.list_id.in_(vis_ids))
                        .where(Hashtag.tag == tag)
                    )
                    rows = (await sess.exec(q)).all()
                    per_tag_todos.append({int(t.id) for t in rows})
                    for t in rows:
                        todos_acc.setdefault(int(t.id), t)
                if per_tag_todos:
                    if mode == 'and':
                        todo_ids = set.intersection(*per_tag_todos) if per_tag_todos else set()
                    else:
                        todo_ids = set.union(*per_tag_todos) if per_tag_todos else set()
                    todos_acc = {i: todos_acc[i] for i in todo_ids if i in todos_acc}

            # Optionally include all todos from matching lists
            if include_list_todos and lists_acc:
                list_ids_match = list(lists_acc.keys())
                qall = select(Todo).where(Todo.list_id.in_(list_ids_match))
                for t in (await sess.exec(qall)).all():
                    todos_acc.setdefault(int(t.id), t)

            # Compute completion status (reuse same logic as html_search)
            lm = {l.id: l.name for l in (await sess.scalars(select(ListState).where(ListState.id.in_(vis_ids)))).all()} if vis_ids else {}
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

        return {'ok': True, 'results': results}

    # Unknown function
    raise HTTPException(status_code=404, detail='function not found')



class CreateCategoryRequest(BaseModel):
    name: str
    position: Optional[int] = None


@app.post('/api/categories')
async def api_create_category(request: Request, payload: CreateCategoryRequest, current_user: User = Depends(require_login)):
    """Create a category via JSON API. Accepts {name, position?}."""
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

    name = (payload.name or '').strip()[:200]
    if not name:
        raise HTTPException(status_code=400, detail='name required')
    async with async_session() as sess:
        # determine position within this user's categories only
        pos = payload.position
        if pos is None:
            qmax = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .order_by(Category.position.desc())
                .limit(1)
            )
            maxc = qmax.first()
            pos = (maxc.position + 1) if maxc else 0
        # Always set the owner to the current user
        nc = Category(name=name, position=pos, owner_id=current_user.id)
        sess.add(nc)
        await sess.commit()
        await sess.refresh(nc)
    return {'id': nc.id, 'name': nc.name, 'position': nc.position, 'sort_alphanumeric': getattr(nc, 'sort_alphanumeric', False)}


class SetUserDefaultCategoryRequest(BaseModel):
    category_id: Optional[int]


@app.post('/api/user/default_category')
async def api_set_user_default_category(request: Request, payload: SetUserDefaultCategoryRequest, current_user: User = Depends(require_login)):
    """Set or clear the current user's default category via JSON API.
    Accepts {category_id: int|null} where null/unset clears the default.
    """
    # allow bearer token clients; otherwise require CSRF
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = body.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')

    cid = getattr(payload, 'category_id', None)
    # normalize clearing values
    if cid is None:
        # clear default
        async with async_session() as sess:
            q = await sess.scalars(select(User).where(User.id == current_user.id))
            u = q.first()
            if not u:
                raise HTTPException(status_code=404, detail='user not found')
            u.default_category_id = None
            sess.add(u)
            await sess.commit()
        return {'ok': True, 'default_category_id': None}

    try:
        cid = int(cid)
    except Exception:
        raise HTTPException(status_code=400, detail='invalid category id')

    async with async_session() as sess:
        cat = await sess.get(Category, cid)
        if not cat:
            raise HTTPException(status_code=404, detail='category not found')
        if getattr(cat, 'owner_id', None) != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        q = await sess.scalars(select(User).where(User.id == current_user.id))
        u = q.first()
        if not u:
            raise HTTPException(status_code=404, detail='user not found')
        u.default_category_id = cid
        sess.add(u)
        await sess.commit()
    return {'ok': True, 'default_category_id': cid}


@app.get('/api/lists/{list_id}/sublists')
async def api_get_list_sublists(list_id: int, current_user: User = Depends(require_login)):
    """Return JSON list of sublists for a given list."""
    async with async_session() as sess:
        try:
            # Verify the parent list exists and belongs to the user
            parent_list = await sess.get(ListState, list_id)
            if not parent_list or parent_list.owner_id != current_user.id:
                raise HTTPException(status_code=404, detail='list not found')
            
            # Get sublists
            q = select(ListState).where(ListState.parent_list_id == list_id).where(ListState.owner_id == current_user.id)
            result = await sess.exec(q)
            sublists = result.all()
            
            # Get uncompleted counts for each sublist
            sublist_data = []
            for sublist in sublists:
                # Count uncompleted todos in this sublist
                q_count = select(func.count(Todo.id)).where(Todo.list_id == sublist.id).where(Todo.completed == False)
                count_result = await sess.exec(q_count)
                uncompleted_count = count_result.first() or 0
                
                sublist_data.append({
                    'id': sublist.id,
                    'name': sublist.name,
                    'uncompleted_count': uncompleted_count
                })
            
            return sublist_data
        except HTTPException:
            raise
        except Exception:
            logger.exception('Failed to get sublists for list %s', list_id)
            return []


class SetCategorySortRequest(BaseModel):
    sort: bool


@app.post('/api/categories/{cat_id}/sort')
async def api_set_category_sort(request: Request, cat_id: int, payload: SetCategorySortRequest, current_user: User = Depends(require_login)):
    """Set per-category sort_alphanumeric flag via API. Accepts {sort: true|false}."""
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

    val = bool(getattr(payload, 'sort', False))
    async with async_session() as sess:
        q = await sess.scalars(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur:
            raise HTTPException(status_code=404, detail='category not found')
        try:
            await sess.exec(sqlalchemy_update(Category).where(Category.id == cat_id).values(sort_alphanumeric=val))
            await sess.commit()
        except Exception:
            logger.exception('failed to set sort_alphanumeric for cat_id=%s', cat_id)
            raise HTTPException(status_code=500, detail='update failed')
        return {'ok': True, 'sort_alphanumeric': val}


class MoveCatRequest(BaseModel):
    direction: str


async def _normalize_category_positions(sess, owner_id: int) -> list[Category]:
    """Ensure Category.position values are contiguous (0..N-1) and unique for a single owner.
    Returns categories for that owner ordered by position after normalization."""
    cres = await sess.exec(
        select(Category)
        .where(Category.owner_id == owner_id)
        .order_by(Category.position.asc(), Category.id.asc())
    )
    cats = cres.all()
    changed = False
    for idx, c in enumerate(cats):
        try:
            if c.position != idx:
                await sess.exec(
                    sqlalchemy_update(Category)
                    .where(Category.id == c.id)
                    .values(position=idx)
                )
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
    cres2 = await sess.exec(
        select(Category)
        .where(Category.owner_id == owner_id)
        .order_by(Category.position.asc(), Category.id.asc())
    )
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
        # capture order before (user-scoped)
        bres = await sess.exec(
            select(Category)
            .where(Category.owner_id == current_user.id)
            .order_by(Category.position.asc(), Category.id.asc())
        )
        before = [{'id': c.id, 'name': c.name, 'position': c.position} for c in bres.all()]
        q = await sess.scalars(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur:
            raise HTTPException(status_code=404, detail='category not found')
        if getattr(cur, 'owner_id', None) != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        if direction == 'up':
            qprev = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .where(Category.position < cur.position)
                .order_by(Category.position.desc())
                .limit(1)
            )
            prev = qprev.first()
            if prev:
                cur_pos = cur.position
                prev_pos = prev.position
                logger.info('api_move_category: swapping up cat_id=%s cur_pos=%s prev_id=%s prev_pos=%s', cur.id, cur_pos, prev.id, prev_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == prev.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=prev_pos))
                logger.info('api_move_category: swap executed for cat_id=%s', cur.id)
        else:
            qnext = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .where(Category.position > cur.position)
                .order_by(Category.position.asc())
                .limit(1)
            )
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
        cats2 = await _normalize_category_positions(sess, current_user.id)
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
        # determine max position for this user's categories and append
        qmax = await sess.exec(
            select(Category)
            .where(Category.owner_id == current_user.id)
            .order_by(Category.position.desc())
            .limit(1)
        )
        maxc = qmax.first()
        pos = (maxc.position + 1) if maxc else 0
        nc = Category(name=name.strip()[:200], position=pos, owner_id=current_user.id)
        sess.add(nc)
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': int(nc.id), 'name': nc.name})
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.post('/html_no_js/categories/{cat_id}/rename')
async def rename_category(request: Request, cat_id: int, name: str = Form(...)):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        # ensure ownership
        q = await sess.scalars(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur or getattr(cur, 'owner_id', None) != current_user.id:
            return RedirectResponse(url='/html_no_js/categories', status_code=303)
        await sess.exec(sqlalchemy_update(Category).where(Category.id == cat_id).values(name=name.strip()[:200]))
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': cat_id, 'name': name.strip()[:200]})
    return RedirectResponse(url='/html_no_js/categories', status_code=303)


@app.post('/html_no_js/categories/{cat_id}/delete')
async def delete_category(request: Request, cat_id: int):
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except HTTPException:
        return RedirectResponse(url='/html_no_js/login', status_code=303)
    async with async_session() as sess:
        # ensure ownership
        q = await sess.scalars(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur or getattr(cur, 'owner_id', None) != current_user.id:
            return RedirectResponse(url='/html_no_js/categories', status_code=303)
        # remove category association from user's lists, then delete
        await sess.exec(
            sqlalchemy_update(ListState)
            .where(ListState.category_id == cat_id)
            .where(ListState.owner_id == current_user.id)
            .values(category_id=None)
        )
        await sess.exec(sqlalchemy_delete(Category).where(Category.id == cat_id))
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': cat_id})
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
        q = await sess.scalars(select(Category).where(Category.id == cat_id))
        cur = q.first()
        if not cur or getattr(cur, 'owner_id', None) != current_user.id:
            return RedirectResponse(url='/html_no_js/categories', status_code=303)
        if direction == 'up':
            # find previous (lower position) item
            qprev = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .where(Category.position < cur.position)
                .order_by(Category.position.desc())
                .limit(1)
            )
            prev = qprev.first()
            if prev:
                cur_pos = cur.position
                prev_pos = prev.position
                logger.info('move_category: swapping up cat_id=%s cur_pos=%s prev_id=%s prev_pos=%s', cur.id, cur_pos, prev.id, prev_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == prev.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=prev_pos))
                logger.info('move_category: swap executed for cat_id=%s', cur.id)
        elif direction == 'down':
            qnext = await sess.exec(
                select(Category)
                .where(Category.owner_id == current_user.id)
                .where(Category.position > cur.position)
                .order_by(Category.position.asc())
                .limit(1)
            )
            nxt = qnext.first()
            if nxt:
                cur_pos = cur.position
                next_pos = nxt.position
                logger.info('move_category: swapping down cat_id=%s cur_pos=%s next_id=%s next_pos=%s', cur.id, cur_pos, nxt.id, next_pos)
                await sess.exec(sqlalchemy_update(Category).where(Category.id == nxt.id).values(position=cur_pos))
                await sess.exec(sqlalchemy_update(Category).where(Category.id == cur.id).values(position=next_pos))
                logger.info('move_category: swap executed for cat_id=%s', cur.id)
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        # return a minimal success payload; client may call the API listing for full state
        return JSONResponse({'ok': True, 'id': cat_id, 'direction': direction})
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
    # Default to excluding completed when the parameter is not supplied (so UI default is respected)
    if 'exclude_completed' in request.query_params:
        exclude_completed = str(request.query_params.get('exclude_completed', '')).lower() in ('1','true','yes','on')
    else:
        exclude_completed = True
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
            # Build list results including priority and hashtags so the
            # no-JS search template can render inline priority-circle and tag chips.
            list_ids = [l.id for l in lists_by_id.values()]
            list_tags_map: dict[int, list[str]] = {}
            if list_ids:
                try:
                    qlt = (
                        select(ListHashtag.list_id, Hashtag.tag)
                        .join(Hashtag, Hashtag.id == ListHashtag.hashtag_id)
                        .where(ListHashtag.list_id.in_(list_ids))
                    )
                    for row in (await sess.exec(qlt)).all():
                        if isinstance(row, (tuple, list)) and len(row) >= 2:
                            lid, tag = row[0], row[1]
                        else:
                            try:
                                lid = row.list_id
                                tag = row.tag
                            except Exception:
                                continue
                        list_tags_map.setdefault(int(lid), []).append(tag)
                except Exception:
                    list_tags_map = {}

            results['lists'] = [
                {
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'priority': getattr(l, 'priority', None),
                    'tags': sorted(list_tags_map.get(int(l.id), [])) if list_tags_map else [],
                }
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
                qtodos = (
                    select(Todo)
                    .where(Todo.list_id.in_(vis_ids))
                    .where((Todo.text.ilike(like)) | (Todo.note.ilike(like)))
                    .where(Todo.search_ignored == False)
                )
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
                        .where(Todo.search_ignored == False)
                    )
                    for t in (await sess.exec(qth)).all():
                        todos_acc.setdefault(t.id, t)
                # optionally include all todos from lists that matched in the list search
                if include_list_todos and lists_by_id:
                    list_ids_match = list(lists_by_id.keys())
                    qall = select(Todo).where(Todo.list_id.in_(list_ids_match)).where(Todo.search_ignored == False)
                    for t in (await sess.exec(qall)).all():
                        todos_acc.setdefault(t.id, t)
                # include list name for display
                lm = {l.id: l.name for l in (await sess.scalars(select(ListState).where(ListState.id.in_(vis_ids)))).all()}
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
                # gather todo hashtags
                todo_ids = list(todos_acc.keys())
                todo_tags_map: dict[int, list[str]] = {}
                if todo_ids:
                    try:
                        qth = (
                            select(TodoHashtag.todo_id, Hashtag.tag)
                            .join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id)
                            .where(TodoHashtag.todo_id.in_(todo_ids))
                        )
                        for row in (await sess.exec(qth)).all():
                            if isinstance(row, (tuple, list)) and len(row) >= 2:
                                tid, tag = row[0], row[1]
                            else:
                                try:
                                    tid = row.todo_id
                                    tag = row.tag
                                except Exception:
                                    continue
                            todo_tags_map.setdefault(int(tid), []).append(tag)
                    except Exception:
                        todo_tags_map = {}

                results['todos'] = [
                    {
                        'id': t.id,
                        'text': t.text,
                        'note': t.note,
                        'list_id': t.list_id,
                        'list_name': lm.get(t.list_id),
                        'completed': (int(t.id) in completed_ids),
                        'priority': getattr(t, 'priority', None),
                        'tags': sorted(todo_tags_map.get(int(t.id), [])) if todo_tags_map else [],
                    }
                    for t in todos_acc.values() if not (exclude_completed and (int(t.id) in completed_ids))
                ]

                # Build a combined ordered list: lists first (in their current order), then todos
                combined: list[dict] = []
                idx = 0
                for l in results.get('lists', []):
                    entry = dict(type='list', id=l.get('id'), name=l.get('name'), completed=l.get('completed'), priority=l.get('priority'), tags=l.get('tags', []), orig_index=idx)
                    combined.append(entry)
                    idx += 1
                for t in results.get('todos', []):
                    entry = dict(type='todo', id=t.get('id'), text=t.get('text'), note=t.get('note'), list_id=t.get('list_id'), list_name=t.get('list_name'), completed=t.get('completed'), priority=t.get('priority'), tags=t.get('tags', []), orig_index=idx)
                    combined.append(entry)
                    idx += 1

                # Sort by priority: highest numeric first, lower next, then None last. Preserve original order for equal priorities.
                def priority_sort_key(item):
                    p = item.get('priority')
                    # highest first -> use negative; None -> place after all numbers
                    primary = (-int(p)) if (p is not None) else float('inf')
                    return (primary, item.get('orig_index', 0))

                combined.sort(key=priority_sort_key)
                results['combined'] = combined
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

    # Minimal SSR skeleton:
    # Do not compute occurrences server-side; let the client fetch and render
    # via /calendar/occurrences. We still compute month navigation values.
    occurrences_sorted: list[dict] = []

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
    new_list = await create_list(request, name=name, current_user=current_user)
    # create_list may return the created ListState or None; try to include id/name
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        payload = {'ok': True}
        try:
            if new_list is not None:
                payload.update({'id': getattr(new_list, 'id', None), 'name': getattr(new_list, 'name', None)})
        except Exception:
            pass
        return JSONResponse(payload)
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
        # return canonical stored name and the current list hashtags
        try:
            qh = select(Hashtag.tag).join(ListHashtag, ListHashtag.hashtag_id == Hashtag.id).where(ListHashtag.list_id == list_id)
            r = await sess.exec(qh)
            rows = r.all()
            list_tags: list[str] = []
            for row in rows:
                val = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(val, str) and val:
                    list_tags.append(val)
        except Exception:
            list_tags = []
        return {'id': list_id, 'name': name, 'hashtags': list_tags}
    return RedirectResponse(url='/html_no_js/', status_code=303)



@app.get('/html_no_js/login', response_class=HTMLResponse)
async def html_login_get(request: Request):
    client_tz = await get_session_timezone(request)
    try:
        csrf_assert(True, 'csrf_login_nojs_get', tz=client_tz)
    except Exception:
        pass
    return TEMPLATES.TemplateResponse(request, 'login.html', {"request": request, "client_tz": client_tz})


@app.post('/html_no_js/login')
async def html_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    from .auth import create_access_token, get_user_by_username, verify_password
    try:
        csrf_assert(True, 'csrf_login_nojs_begin', accept=(request.headers.get('Accept') or ''), tz=request.cookies.get('tz'))
    except Exception:
        pass
    user = await get_user_by_username(username)
    try:
        csrf_assert(bool(user), 'csrf_login_nojs_user_found', username=username)
    except Exception:
        pass
    ok = False
    if user:
        ok = await verify_password(password, user.password_hash)
    try:
        csrf_assert(ok, 'csrf_login_nojs_password_ok', username=username)
    except Exception:
        pass
    if not user or not ok:
        # re-render login with simple message (keeps no-js constraint simple)
        client_tz = await get_session_timezone(request)
        accept = (request.headers.get('Accept') or '')
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': False, 'error': 'invalid_credentials'})
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
    try:
        _record_issued_csrf(user.username, csrf, source='login_nojs')
        info = _csrf_token_info(csrf)
        csrf_assert(True, 'csrf_login_nojs_token_created', user=user.username, token_hash=info.get('hash'), remaining=info.get('remaining'))
        try:
            import datetime
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            csrf_assert(abs(int(info.get('exp') or 0) - (now_ts + int(CSRF_TOKEN_EXPIRE_SECONDS))) <= 5, 'csrf_login_nojs_expected_exp', exp=info.get('exp'), now_ts=now_ts, configured=CSRF_TOKEN_EXPIRE_SECONDS)
        except Exception:
            pass
    except Exception:
        pass
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        # Return tokens and csrf so AJAX clients may persist them as needed
        try:
            csrf_assert(True, 'csrf_login_nojs_path_json', user=user.username)
        except Exception:
            pass
        return JSONResponse({'ok': True, 'session_token': session_token, 'access_token': token, 'csrf_token': csrf})

    resp = RedirectResponse(url="/html_no_js/", status_code=303)
    resp.set_cookie('session_token', session_token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('access_token', token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    try:
        resp.delete_cookie('csrf_token', path='/')
        resp.delete_cookie('csrf_token', path='/html_no_js')
        csrf_assert(True, 'csrf_cookie_cleared', source='login_nojs', paths=['/', '/html_no_js'])
    except Exception:
        pass
    resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE, path='/')
    try:
        csrf_assert(True, 'csrf_cookie_set', source='login_nojs', path='/')
    except Exception:
        pass
    try:
        info_cookie = _csrf_token_info(csrf)
        csrf_assert(True, 'csrf_login_nojs_path_redirect', user=user.username, token_hash=info_cookie.get('hash'))
    except Exception:
        pass
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
    from .auth import create_access_token, get_user_by_username, verify_password
    try:
        csrf_assert(True, 'csrf_login_pwa_begin', accept=(request.headers.get('Accept') or ''))
    except Exception:
        pass
    user = await get_user_by_username(username)
    try:
        csrf_assert(bool(user), 'csrf_login_pwa_user_found', username=username)
    except Exception:
        pass
    ok = False
    if user:
        ok = await verify_password(password, user.password_hash)
    try:
        csrf_assert(ok, 'csrf_login_pwa_password_ok', username=username)
    except Exception:
        pass
    if not user or not ok:
        client_tz = await get_session_timezone(request)
        return TEMPLATES.TemplateResponse(request, 'login.html', {"request": request, "error": "Invalid credentials", "client_tz": client_tz})
    token = create_access_token({"sub": user.username})
    # create a server-side session token and set it in an HttpOnly cookie
    from .auth import create_session_for_user, create_csrf_token
    client_tz = request.cookies.get('tz')
    session_token = await create_session_for_user(user, session_timezone=client_tz)
    csrf = create_csrf_token(user.username)
    try:
        _record_issued_csrf(user.username, csrf, source='login_pwa')
        info = _csrf_token_info(csrf)
        csrf_assert(True, 'csrf_login_pwa_token_created', user=user.username, token_hash=info.get('hash'), remaining=info.get('remaining'))
        try:
            import datetime
            now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            csrf_assert(abs(int(info.get('exp') or 0) - (now_ts + int(CSRF_TOKEN_EXPIRE_SECONDS))) <= 5, 'csrf_login_pwa_expected_exp', exp=info.get('exp'), now_ts=now_ts, configured=CSRF_TOKEN_EXPIRE_SECONDS)
        except Exception:
            pass
    except Exception:
        pass
    # Redirect to the PWA index and set cookies on the response
    resp = RedirectResponse(url="/html_pwa/", status_code=303)
    resp.set_cookie('session_token', session_token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    resp.set_cookie('access_token', token, httponly=True, samesite='lax', secure=COOKIE_SECURE)
    try:
        resp.delete_cookie('csrf_token', path='/')
        resp.delete_cookie('csrf_token', path='/html_no_js')
        csrf_assert(True, 'csrf_cookie_cleared', source='login_pwa', paths=['/', '/html_no_js'])
    except Exception:
        pass
    resp.set_cookie('csrf_token', csrf, httponly=False, samesite='lax', secure=COOKIE_SECURE, path='/')
    try:
        csrf_assert(True, 'csrf_cookie_set', source='login_pwa', path='/')
    except Exception:
        pass
    try:
        info_cookie = _csrf_token_info(csrf)
        csrf_assert(True, 'csrf_login_pwa_path_redirect', user=user.username, token_hash=info_cookie.get('hash'))
    except Exception:
        pass
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
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'hide_icons': getattr(lst, 'hide_icons', None)})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/lists/{list_id}/lists_up_top')
async def html_set_list_lists_up_top(request: Request, list_id: int, lists_up_top: str = Form(None), current_user: User = Depends(require_login)):
    # CSRF and ownership
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id is not None and lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        if lists_up_top is not None:
            val = str(lists_up_top).lower() in ('1', 'true', 'yes', 'on')
            lst.lists_up_top = val
            sess.add(lst)
            await sess.commit()
            await sess.refresh(lst)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'lists_up_top': lst.lists_up_top})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/todos/{todo_id}/lists_up_top')
async def html_set_todo_lists_up_top(request: Request, todo_id: int, lists_up_top: str = Form(None), current_user: User = Depends(require_login)):
    # CSRF and ownership enforced via parent list
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        q = await sess.scalars(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if lst and lst.owner_id is not None and lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        if lists_up_top is not None:
            val = str(lists_up_top).lower() in ('1', 'true', 'yes', 'on')
            todo.lists_up_top = val
            sess.add(todo)
            await sess.commit()
            await sess.refresh(todo)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': todo.id, 'lists_up_top': todo.lists_up_top})
    ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'category_id': cid})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)





@app.get('/__debug_setcookie')
def __debug_setcookie(request: Request):
    """Debug helper to set a test cookie. Restricted to local requests.

    Avoid exposing cookie-setting behavior to the public internet.
    """
    if not _is_local_request(request):
        raise HTTPException(status_code=403, detail='forbidden')
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        # attempt to clear server-side session already performed above
        resp = JSONResponse({'ok': True, 'logged_out': True})
        # instruct client to clear cookies
        resp.delete_cookie('session_token', path='/', samesite='lax', secure=COOKIE_SECURE)
        resp.delete_cookie('access_token', path='/', samesite='lax', secure=COOKIE_SECURE)
        resp.delete_cookie('csrf_token', path='/', samesite='lax', secure=COOKIE_SECURE)
        return resp

    resp = TEMPLATES.TemplateResponse(request, 'logout.html', {"request": request, "client_tz": client_tz})
    # delete cookies with the same attributes used when setting them so
    # browsers will reliably remove them.
    resp.delete_cookie('session_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('access_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('csrf_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    return resp


@app.post('/html_tailwind/logout')
async def html_tailwind_logout(request: Request):
    # attempt to remove server-side session if present
    session_token = request.cookies.get('session_token')
    if session_token:
        from .auth import delete_session
        await delete_session(session_token)
    # respond with JSON and instruct client to remove cookies
    resp = JSONResponse({'ok': True, 'logged_out': True})
    resp.delete_cookie('session_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('access_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    resp.delete_cookie('csrf_token', path='/', samesite='lax', secure=COOKIE_SECURE)
    return resp


@app.get('/html_tailwind/whoami')
async def html_tailwind_whoami(request: Request):
    """Return current user info as JSON for the Tailwind client.

    Returns {'ok': True, 'user': {...}} when authenticated or
    {'ok': True, 'user': None} when anonymous.
    """
    try:
        from .auth import get_current_user as _gcu
        user = await _gcu(token=None, request=request)
    except Exception:
        user = None

    if not user:
        return JSONResponse({'ok': True, 'user': None})

    # sanitize fields
    data = {
        'id': getattr(user, 'id', None),
        'username': getattr(user, 'username', None),
        'email': getattr(user, 'email', None),
    }
    return JSONResponse({'ok': True, 'user': data})


@app.get("/html_no_js/lists/{list_id}", response_class=HTMLResponse)
async def html_view_list(request: Request, list_id: int, current_user: User = Depends(require_login)):
    # require login and ownership for HTML list view
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        if not lst:
            raise HTTPException(status_code=404, detail="list not found")
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # fetch completion types for this list in creation order (id ASC)
        qct = await sess.scalars(
            select(CompletionType)
            .where(CompletionType.list_id == list_id)
            .order_by(CompletionType.id.asc())
        )
        ctypes = qct.all()

        # load todos and completion states in batch
        # Order todos: priority (higher first, NULLs last), then newest created_at first
        try:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.priority.desc().nullslast(), Todo.created_at.desc()))
        except Exception:
            # Fallback if DB/driver doesn't support nullslast or priority desc expression
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == list_id).order_by(Todo.created_at.desc()))
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
                "priority": getattr(t, 'priority', None),
                "extra_completions": extra,
            })

        # Ensure completed todos do not use priority for ordering: treat priority as None when completed
        def _todo_display_sort_key(row):
            # if completed, ignore priority
            p = row.get('priority') if not row.get('completed') else None
            # primary: presence of priority (priorityed first), then priority value (higher first), then newest created_at
            # we invert priority to sort descending via tuple (has_priority, priority_value)
            has_p = 1 if p is not None else 0
            pr_val = p if p is not None else -999
            # return tuple such that sorting with reverse=True will place higher priorities first
            return (has_p, pr_val, row.get('created_at').timestamp() if row.get('created_at') else 0)

        # sort with reverse to get priority high-to-low and newest first for ties
        todo_rows.sort(key=_todo_display_sort_key, reverse=True)

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
            "list_id": lst.id,
            "lists_up_top": getattr(lst, 'lists_up_top', False),
            "priority": getattr(lst, 'priority', None),
            # expose parent todo owner for sublist toolbar/navigation
            "parent_todo_id": getattr(lst, 'parent_todo_id', None),
            # expose parent list owner for nested list navigation (not yet used in UI)
            "parent_list_id": getattr(lst, 'parent_list_id', None),
        }
        # Indicate if this list is one of the current user's collations and whether it's active
        try:
            uc_row = await sess.get(UserCollation, (current_user.id, int(list_id)))
            list_row["is_collation"] = bool(uc_row is not None)
            list_row["collation_active"] = bool(getattr(uc_row, 'active', True)) if uc_row is not None else False
        except Exception:
            list_row["is_collation"] = False
            list_row["collation_active"] = False
        # If this list is marked as a collation, include linked todos (list -> todo edges) in the rendered list
        try:
            if list_row.get("is_collation"):
                # collect linked todo ids from ItemLink
                q_linked = await sess.exec(
                    select(ItemLink.tgt_id)
                    .where(ItemLink.src_type == 'list')
                    .where(ItemLink.src_id == list_id)
                    .where(ItemLink.tgt_type == 'todo')
                )
                linked_ids_all = [int(v) for v in q_linked.all() if v is not None]
                if linked_ids_all:
                    existing_ids = {int(r['id']) for r in todo_rows}
                    new_ids = [tid for tid in linked_ids_all if tid not in existing_ids]
                else:
                    new_ids = []
                if new_ids:
                    # fetch linked todos ensuring user owns the underlying lists
                    q_lt = await sess.exec(
                        select(Todo.id, Todo.text, Todo.note, Todo.created_at, Todo.modified_at, Todo.priority, Todo.list_id)
                        .join(ListState, ListState.id == Todo.list_id)
                        .where(Todo.id.in_(new_ids))
                        .where(ListState.owner_id == current_user.id)
                    )
                    lrows = q_lt.all()
                    origin_ids = sorted({int(lid) for (_, _, _, _, _, _, lid) in lrows if lid is not None})
                    origin_names: dict[int, str] = {}
                    if origin_ids:
                        q_on = await sess.exec(select(ListState.id, ListState.name).where(ListState.id.in_(origin_ids)))
                        for lid, nm in q_on.all():
                            try:
                                origin_names[int(lid)] = nm
                            except Exception:
                                pass
                    # append linked rows (mark as linked; no extra completion types from current list)
                    for tid, text, note, created_at, modified_at, priority, origin_lid in lrows:
                        todo_rows.append({
                            "id": int(tid),
                            "text": text,
                            "note": note,
                            "created_at": created_at,
                            "modified_at": modified_at,
                            "completed": False,  # set accurately below via default-completion recompute
                            "pinned": False,
                            "priority": getattr(priority, 'real', priority) if priority is not None else None,
                            "extra_completions": [],
                            "is_linked": True,
                            "origin_list_id": int(origin_lid) if origin_lid is not None else None,
                            "origin_list_name": origin_names.get(int(origin_lid)) if origin_lid is not None else None,
                        })
                    # tags for linked todos only
                    if lrows:
                        new_ids_set = {int(tid) for tid, *_ in lrows}
                        if new_ids_set:
                            qth2 = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(list(new_ids_set)))
                            tres2 = await sess.exec(qth2)
                            tag_map2: dict[int, list[str]] = {}
                            for tid, tag in tres2.all():
                                try:
                                    tid_i = int(tid)
                                except Exception:
                                    continue
                                if isinstance(tag, str) and tag:
                                    tag_map2.setdefault(tid_i, []).append(tag)
                            for r in todo_rows:
                                if int(r.get('id')) in tag_map2 and not r.get('tags'):
                                    r['tags'] = tag_map2.get(int(r['id']), [])
                    # recompute default completion across all rows by using each todo's own default type
                    try:
                        all_ids = [int(r['id']) for r in todo_rows]
                        if all_ids:
                            qdef = await sess.exec(
                                select(TodoCompletion.todo_id, TodoCompletion.done)
                                .join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id)
                                .where(CompletionType.name == 'default')
                                .where(TodoCompletion.todo_id.in_(all_ids))
                            )
                            def_map = {}
                            for tid, done_val in qdef.all():
                                try:
                                    def_map[int(tid)] = bool(done_val)
                                except Exception:
                                    continue
                            for r in todo_rows:
                                try:
                                    r['completed'] = bool(def_map.get(int(r['id']), False))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    # resort to keep display order consistent
                    try:
                        todo_rows.sort(key=_todo_display_sort_key, reverse=True)
                    except Exception:
                        pass
        except Exception:
            # never break list view on aggregation problems
            logger.exception('Failed to aggregate linked todos for collation list %s', list_id)
        # If this list is owned by a todo, fetch the todo text for UI label
        if getattr(lst, 'parent_todo_id', None):
            try:
                qpt = await sess.exec(select(Todo.text).where(Todo.id == lst.parent_todo_id))
                row = qpt.first()
                todo_text = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(todo_text, str):
                    list_row["parent_todo_text"] = todo_text
            except Exception:
                list_row["parent_todo_text"] = None
        # If this list is owned by another list, fetch the parent list name for UI label
        if getattr(lst, 'parent_list_id', None):
            try:
                qpl = await sess.exec(select(ListState.name).where(ListState.id == lst.parent_list_id))
                row = qpl.first()
                parent_list_name = row[0] if isinstance(row, (tuple, list)) else row
                if isinstance(parent_list_name, str):
                    list_row["parent_list_name"] = parent_list_name
            except Exception:
                list_row["parent_list_name"] = None
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
            qcat = select(Category).where(Category.owner_id == current_user.id).order_by(Category.position.asc())
            cres = await sess.exec(qcat)
            categories = [{'id': c.id, 'name': c.name, 'position': c.position} for c in cres.all()]
        except Exception:
            categories = []
    # Fetch sublists owned by this list (list->list nesting). Use explicit sibling position when set,
        # else fall back to created_at ASC. Enrich with hashtags for display.
        sublists = []
        try:
            qsubs = select(ListState).where(ListState.parent_list_id == list_id).where(ListState.owner_id == current_user.id)
            rsubs = await sess.exec(qsubs)
            rows = rsubs.all()
            def _sort_key(l):
                pos = getattr(l, 'parent_list_position', None)
                created = getattr(l, 'created_at', None)
                return (0 if pos is not None else 1, pos if pos is not None else 0, created or now_utc())
            rows.sort(key=_sort_key)
            sub_ids = [l.id for l in rows if l.id is not None]
            tag_map: dict[int, list[str]] = {}
            if sub_ids:
                qlh = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(sub_ids))
                rlh = await sess.exec(qlh)
                for lid, tag in rlh.all():
                    tag_map.setdefault(lid, []).append(tag)
            for l in rows:
                sublists.append({
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'created_at': getattr(l, 'created_at', None),
                    'modified_at': getattr(l, 'modified_at', None),
                    'hashtags': tag_map.get(l.id, []),
                    'parent_list_position': getattr(l, 'parent_list_position', None),
                    # placeholder for any higher-priority uncompleted todo in this sublist
                    'override_priority': None,
                    # include the sublist's own priority if present on the ORM object
                    'priority': getattr(l, 'priority', None),
                })
            # Determine highest uncompleted todo priority per sublist (if any)
            try:
                if sub_ids:
                    todo_q = await sess.scalars(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(sub_ids)).where(Todo.priority != None))
                    # use a distinct variable name so we don't clobber the main `todo_rows`
                    todo_id_rows = todo_q.all()
                    todo_map: dict[int, list[tuple[int,int]]] = {}
                    todo_ids = []
                    for tid, lid, pri in todo_id_rows:
                        todo_map.setdefault(lid, []).append((tid, pri))
                        todo_ids.append(tid)
                    completed_ids = set()
                    if todo_ids:
                        try:
                            qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                            cres = await sess.exec(qcomp)
                            completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                        except Exception:
                            completed_ids = set()
                    # diagnostic logging to help debug missing override priorities
                    try:
                        logger.info('todo override diagnostic: todo_id_rows=%s', todo_id_rows)
                        logger.info('todo override diagnostic: todo_map keys=%s', list(todo_map.keys()))
                        logger.info('todo override diagnostic: completed_ids=%s', completed_ids)
                    except Exception:
                        pass
                    # compute highest uncompleted priority per sublist
                    for sub in sublists:
                        lid = sub.get('id')
                        candidates = todo_map.get(lid, [])
                        max_p = None
                        for tid, pri in candidates:
                            if tid in completed_ids:
                                continue
                            try:
                                if pri is None:
                                    continue
                                pv = int(pri)
                            except Exception:
                                continue
                            if max_p is None or pv > max_p:
                                max_p = pv
                        if max_p is not None:
                            sub['override_priority'] = max_p
            except Exception:
                # failure computing overrides should not break list rendering
                pass
        except Exception:
            sublists = []
    # Fetch outgoing links from this list (to todos or lists), order by position then created_at
        links: list[dict] = []
        try:
            qlnk = select(ItemLink).where(ItemLink.src_type == 'list').where(ItemLink.src_id == list_id).order_by(ItemLink.position.asc().nullslast(), ItemLink.created_at.asc())
            rlnk = await sess.exec(qlnk)
            rows = rlnk.all()
            # Preload names/texts for targets in batch
            todo_targets = [r.tgt_id for r in rows if r.tgt_type == 'todo']
            list_targets = [r.tgt_id for r in rows if r.tgt_type == 'list']
            todo_map: dict[int, dict] = {}
            list_map: dict[int, dict] = {}
            if todo_targets:
                # preload todo id, text and list_id so we can determine completion state
                qtt = await sess.exec(select(Todo.id, Todo.text, Todo.list_id).where(Todo.id.in_(todo_targets)))
                for tid, txt, lid in qtt.all():
                    todo_map[int(tid)] = {'id': int(tid), 'text': txt, 'list_id': int(lid) if lid is not None else None}
            if list_targets:
                qll = await sess.exec(select(ListState.id, ListState.name).where(ListState.id.in_(list_targets)))
                for lid, name in qll.all():
                    list_map[int(lid)] = {'id': int(lid), 'name': name}
            # Preload hashtags for targets
            tags_map_todo: dict[int, list[str]] = {}
            tags_map_list: dict[int, list[str]] = {}
            if todo_targets:
                qth = await sess.exec(select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_targets)))
                for tid, tag in qth.all():
                    try:
                        tid_i = int(tid)
                    except Exception:
                        continue
                    if isinstance(tag, str) and tag:
                        tags_map_todo.setdefault(tid_i, []).append(tag)
            if list_targets:
                qlh = await sess.exec(select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(list_targets)))
                for lid, tag in qlh.all():
                    try:
                        lid_i = int(lid)
                    except Exception:
                        continue
                    if isinstance(tag, str) and tag:
                        tags_map_list.setdefault(lid_i, []).append(tag)
            for r in rows:
                d = {'id': r.id, 'tgt_type': r.tgt_type, 'tgt_id': r.tgt_id, 'label': r.label, 'position': r.position}
                if r.tgt_type == 'todo':
                    t = todo_map.get(int(r.tgt_id))
                    if t:
                        d['title'] = t.get('text')
                        d['href'] = f"/html_no_js/todos/{t['id']}"
                        d['tags'] = tags_map_todo.get(int(r.tgt_id), [])
                elif r.tgt_type == 'list':
                    l = list_map.get(int(r.tgt_id))
                    if l:
                        d['title'] = l.get('name')
                        d['href'] = f"/html_no_js/lists/{l['id']}"
                        d['tags'] = tags_map_list.get(int(r.tgt_id), [])
                links.append(d)
        except Exception:
            links = []
        # Active collations for this user and per-todo linkage map
        active_collations: list[dict] = []
        todo_collation_linked: dict[int, set[int]] = {}
        try:
            # gather this user's active collations
            q_uc = await sess.exec(select(UserCollation).where(UserCollation.user_id == current_user.id).where(UserCollation.active == True))
            uc_rows = q_uc.all()
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('collation-debug(list:%s,user:%s): uc_rows=%s', list_id, getattr(current_user, 'id', None), [int(getattr(r, 'list_id', 0) or 0) for r in uc_rows])
                except Exception:
                    pass
            col_ids = [int(r.list_id) for r in uc_rows]
            names: dict[int, str] = {}
            if col_ids:
                q_names = await sess.exec(
                    select(ListState.id, ListState.name)
                    .where(ListState.id.in_(col_ids))
                    .where(ListState.owner_id == current_user.id)
                )
                for lid, name in q_names.all():
                    try:
                        names[int(lid)] = name
                    except Exception:
                        continue
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('collation-debug(list:%s,user:%s): names.keys=%s', list_id, getattr(current_user, 'id', None), sorted(list(names.keys())))
                except Exception:
                    pass
            # Exclude collations that are currently in the user's Trash
            trashed: set[int] = set()
            if col_ids:
                trash_id = None
                try:
                    q_trash = await sess.scalars(select(ListState.id).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
                    trash_id = q_trash.first()
                except Exception:
                    trash_id = None
                if trash_id is not None:
                    q_tr = await sess.scalars(
                        select(ListState.id)
                        .where(ListState.id.in_(col_ids))
                        .where(ListState.parent_list_id == trash_id)
                    )
                    trashed = set(int(v) for v in q_tr.all())
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('collation-debug(list:%s,user:%s): trashed_ids=%s', list_id, getattr(current_user, 'id', None), sorted(list(trashed)))
                except Exception:
                    pass
            # Build final active collations list (only existing, owned, and not trashed)
            active_collations = [
                { 'list_id': int(r.list_id), 'name': names.get(int(r.list_id)) }
                for r in uc_rows
                if (int(r.list_id) in names and int(r.list_id) not in trashed)
            ]
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('collation-debug(list:%s,user:%s): active_collations=%s', list_id, getattr(current_user, 'id', None), [int(c.get('list_id')) for c in active_collations])
                except Exception:
                    pass
            # Per-todo linked map: which todos include each active collation
            # Recompute todo_ids from current todo_rows (may include aggregated linked todos)
            try:
                todo_ids = [int(r['id']) for r in todo_rows]
            except Exception:
                todo_ids = []
            if todo_ids and active_collations:
                ac_ids = [int(c['list_id']) for c in active_collations]
                q_links = await sess.exec(
                    select(ItemLink.src_id, ItemLink.tgt_id)
                    .where(ItemLink.src_type == 'list')
                    .where(ItemLink.tgt_type == 'todo')
                    .where(ItemLink.tgt_id.in_(todo_ids))
                    .where(ItemLink.src_id.in_(ac_ids))
                )
                for src_id, tgt_id in q_links.all():
                    try:
                        tid = int(tgt_id); lid = int(src_id)
                    except Exception:
                        continue
                    s = todo_collation_linked.get(tid)
                    if s is None:
                        s = set()
                        todo_collation_linked[tid] = s
                    s.add(lid)
            if ENABLE_VERBOSE_DEBUG:
                try:
                    logger.info('collation-debug(list:%s,user:%s): todo_ids=%s linked_counts=%s', list_id, getattr(current_user, 'id', None), todo_ids, {int(k): len(v) for k, v in todo_collation_linked.items()})
                except Exception:
                    pass
        except Exception:
            # fall back silently if collation computation fails
            active_collations = []
            todo_collation_linked = {}
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
    # timezone for template rendering
    client_tz = await get_session_timezone(request)
    # Seed per-request fn:link label cache with this list and included todos to improve inline rendering labels
    try:
        cache = _fn_link_label_cache.get()
        if not isinstance(cache, dict):
            cache = {}
        if list_row and list_row.get('id') and list_row.get('name'):
            cache[f"list:{int(list_row['id'])}"] = list_row['name']
        for t in todo_rows:
            try:
                cache[f"todo:{int(t['id'])}"] = t.get('text')
            except Exception:
                pass
        _fn_link_label_cache.set(cache)
    except Exception:
        pass
    # Load per-user list prefs (completed_after)
    completed_after_pref = False
    try:
        async with async_session() as _psess:
            from .models import UserListPrefs as _ULP
            row = await _psess.get(_ULP, (current_user.id, int(list_id)))
            if row is not None:
                completed_after_pref = bool(getattr(row, 'completed_after', False))
    except Exception:
        completed_after_pref = False
    return TEMPLATES.TemplateResponse(
        request,
        "list.html",
        {
            "request": request,
            "list": list_row,
            "todos": todo_rows,
            "csrf_token": csrf_token,
            "client_tz": client_tz,
            "completion_types": completion_types,
            "all_hashtags": all_hashtags,
            "categories": categories,
            "sublists": sublists,
            "links": links,
            "active_collations": active_collations,
            "todo_collation_linked": {int(k): list(v) for k, v in todo_collation_linked.items()},
            "completed_after": completed_after_pref,
        },
    )


# ===== Tiny lookup endpoint for names/titles (used by Note combobox) =====
@app.get('/api/lookup/names')
async def api_lookup_names(request: Request, current_user: User = Depends(require_login)):
    """Return minimal titles for given todo/list IDs that belong to the current user.

    Query params:
    - todos=1,2,3
    - lists=4,5

    Response: { ok: true, todos: {"1": "todo text"}, lists: {"4": "list name"} }
    """
    # Parse query params into integer ID lists
    def _parse_ids(val: str | None) -> list[int]:
        out: list[int] = []
        if not val:
            return out
        for tok in str(val).split(','):
            tok = tok.strip()
            if not tok:
                continue
            try:
                nid = int(tok)
                if nid > 0:
                    out.append(nid)
            except Exception:
                continue
        return list(dict.fromkeys(out))  # dedupe preserving order

    todo_ids = _parse_ids(request.query_params.get('todos'))
    list_ids = _parse_ids(request.query_params.get('lists'))

    todos_map: dict[str, str] = {}
    lists_map: dict[str, str] = {}

    if not todo_ids and not list_ids:
        return JSONResponse({'ok': True, 'todos': todos_map, 'lists': lists_map})

    try:
        async with async_session() as sess:
            if todo_ids:
                # Only return todos in lists owned by the current user
                q = (
                    select(Todo.id, Todo.text)
                    .join(ListState, ListState.id == Todo.list_id)
                    .where(Todo.id.in_(todo_ids))
                    .where(ListState.owner_id == current_user.id)
                )
                res = await sess.exec(q)
                for tid, txt in res.all():
                    if tid is None:
                        continue
                    val = txt if isinstance(txt, str) else ''
                    todos_map[str(int(tid))] = val
            if list_ids:
                ql = select(ListState.id, ListState.name).where(ListState.id.in_(list_ids)).where(ListState.owner_id == current_user.id)
                r2 = await sess.exec(ql)
                for lid, name in r2.all():
                    if lid is None:
                        continue
                    val = name if isinstance(name, str) else ''
                    lists_map[str(int(lid))] = val
    except Exception:
        # best-effort; do not fail callers if a transient DB error occurs
        pass

    return JSONResponse({'ok': True, 'todos': todos_map, 'lists': lists_map})


@app.get('/html_no_js/hashtags', response_class=HTMLResponse)
async def html_no_js_hashtags(request: Request, current_user: User = Depends(require_login)):
    """Unlinked page to list all Hashtag rows."""
    async with async_session() as sess:
        # Only show hashtags that are associated with lists/todos visible to the current user.
        # Visible lists: owner == current_user.id
        # Visible todos: todos whose ListState.owner_id is current_user.id or NULL (public)
        try:
            # Build an EXISTS-based query: select Hashtag rows where either
            # a ListHashtag exists linking to a ListState owned by current_user
            # OR a TodoHashtag exists linking to a Todo whose ListState is owned
            # by current_user or is public (owner_id IS NULL).
            from sqlalchemy import exists, and_, or_
            lh_exists = exists().where(and_(ListHashtag.hashtag_id == Hashtag.id, ListHashtag.list_id == ListState.id, ListState.owner_id == current_user.id))
            th_exists = exists().where(and_(TodoHashtag.hashtag_id == Hashtag.id, TodoHashtag.todo_id == Todo.id, Todo.list_id == ListState.id, or_(ListState.owner_id == current_user.id, ListState.owner_id == None)))
            q = select(Hashtag).where(or_(lh_exists, th_exists)).order_by(Hashtag.tag.asc())
            res = await sess.exec(q)
            # some SQLModel/SQLAlchemy versions return a ScalarResult without
            # a .scalars() helper; use .all() which works across versions.
            hashtags = res.all()
        except Exception:
            logger.exception('failed to load hashtags for user id=%s', getattr(current_user, 'id', None))
            hashtags = []
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    debug_flag = str(request.query_params.get('debug','')).lower() in ('1','true','yes')
    return TEMPLATES.TemplateResponse(request, 'hashtags.html', {'request': request, 'hashtags': hashtags, 'csrf_token': csrf_token, 'current_user': current_user, 'debug': debug_flag})


@app.post('/html_no_js/hashtags/delete')
async def html_no_js_hashtags_delete(request: Request, current_user: User = Depends(require_login)):
    """Delete one or more hashtags (admin only). Expects form field `tags` as comma-separated tag values (e.g. #tag1,#tag2)."""
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # require admin to perform global deletes, except when the current user
    # owns all associated lists/todos for the requested tags (in which case
    # allow the owner to delete their own tags).
    tags_raw = form.get('tags', '') or ''
    # tags may be comma-separated; allow whitespace
    tags_list = [t.strip() for t in tags_raw.split(',') if t and t.strip()]
    if not tags_list:
        # nothing to do
        ref = request.headers.get('Referer', '/html_no_js/hashtags')
        return RedirectResponse(url=ref, status_code=303)
    # normalize tags using normalize_hashtag where appropriate; tolerate tags already normalized
    norm_tags: list[str] = []
    for t in tags_list:
        try:
            nt = normalize_hashtag(t)
        except Exception:
            # ignore invalid tokens
            continue
        norm_tags.append(nt)
    if not norm_tags:
        ref = request.headers.get('Referer', '/html_no_js/hashtags')
        return RedirectResponse(url=ref, status_code=303)
    # perform deletion: remove association rows then hashtag rows
    from sqlalchemy import delete as sa_delete
    async with async_session() as sess:
        try:
            # find matching hashtag rows
            qh_res = await sess.exec(select(Hashtag).where(Hashtag.tag.in_(norm_tags)))
            hs = qh_res.all()
            ids = [int(h.id) for h in hs]
            if not ids:
                # nothing matched
                ref = request.headers.get('Referer', '/html_no_js/hashtags')
                return RedirectResponse(url=ref, status_code=303)

            # Determine owners of associated lists and todos for these hashtag ids
            q_list_assoc = select(ListState.owner_id).join(ListHashtag, ListHashtag.list_id == ListState.id).where(ListHashtag.hashtag_id.in_(ids))
            la_res = await sess.exec(q_list_assoc)
            # la_res.all() returns list of rows which may be simple values; normalize to set
            list_owner_ids = {r for r in la_res.all() if r is not None}
            q_todo_assoc = select(ListState.owner_id).join(Todo, Todo.list_id == ListState.id).join(TodoHashtag, TodoHashtag.todo_id == Todo.id).where(TodoHashtag.hashtag_id.in_(ids))
            ta_res = await sess.exec(q_todo_assoc)
            todo_owner_ids = {r for r in ta_res.all() if r is not None}

            assoc_owner_ids = list_owner_ids.union(todo_owner_ids)
            # If there are no associated owner ids (only associations to lists with NULL owner), treat as public and require admin
            allow_owner_delete = False
            if assoc_owner_ids:
                # allow non-admin if and only if the only owner id present is the current user
                if assoc_owner_ids == {current_user.id}:
                    allow_owner_delete = True

            if not getattr(current_user, 'is_admin', False) and not allow_owner_delete:
                raise HTTPException(status_code=403, detail='admin required')

            # delete associations and hashtags
            if ids:
                await sess.exec(sa_delete(ListHashtag).where(ListHashtag.hashtag_id.in_(ids)))
                await sess.exec(sa_delete(TodoHashtag).where(TodoHashtag.hashtag_id.in_(ids)))
                await sess.exec(sa_delete(Hashtag).where(Hashtag.id.in_(ids)))
                await sess.commit()
        except Exception:
            await sess.rollback()
            raise
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': norm_tags})
    ref = request.headers.get('Referer', '/html_no_js/hashtags')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/lists/{list_id}/priority')
async def html_update_list_priority(request: Request, list_id: int, priority: str = Form(None), current_user: User = Depends(require_login)):
    """Update the optional priority for a list. Accepts values 'none' or '' to clear, or '1'..'10'."""
    # CSRF check
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # normalize input
    val: int | None = None
    if priority is not None and str(priority).strip() != '' and str(priority).lower() != 'none':
        try:
            n = int(priority)
            if n < 1 or n > 10:
                raise ValueError('priority out of range')
            val = n
        except Exception:
            # treat invalid input as clearing priority
            val = None
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        lst.priority = val
        lst.modified_at = now_utc()
        sess.add(lst)
        await sess.commit()
    # If the client asked for JSON, return minimal JSON; otherwise redirect back to the list page
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'priority': val})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/lists/{list_id}/completed_after')
async def html_set_list_completed_after(request: Request, list_id: int, completed_after: str = Form(None), current_user: User = Depends(require_login)):
    """Persist the 'completed after' toggle per-user for a specific list.

    Accepts completed_after as truthy string ('1','true','yes','on').
    CSRF protected; ensures the list is owned by the user.
    """
    # CSRF and ownership
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    val = False
    if completed_after is not None:
        val = str(completed_after).lower() in ('1','true','yes','on')
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # Upsert prefs row
        from .models import UserListPrefs
        row = await sess.get(UserListPrefs, (current_user.id, int(list_id)))
        if row is None:
            row = UserListPrefs(user_id=current_user.id, list_id=int(list_id), completed_after=val)
        else:
            row.completed_after = val
        sess.add(row)
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'completed_after': val})
    ref = request.headers.get('Referer', f'/html_no_js/lists/{list_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/todos/{todo_id}/priority')
async def html_update_todo_priority(request: Request, todo_id: int, priority: str = Form(None), current_user: User = Depends(require_login)):
    """Update the optional priority for a todo. Accepts values '' or 'none' or '1'..'10'."""
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    val: int | None = None
    if priority is not None and str(priority).strip() != '' and str(priority).lower() != 'none':
        try:
            n = int(priority)
            if n < 1 or n > 10:
                raise ValueError('priority out of range')
            val = n
        except Exception:
            val = None
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # ownership via parent list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        todo.priority = val
        todo.modified_at = now_utc()
        sess.add(todo)
        await sess.commit()
    # If AJAX client requested JSON, return the updated todo info; otherwise redirect
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': todo_id, 'priority': val})
    ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
    return RedirectResponse(url=ref, status_code=303)


@app.post('/html_no_js/todos/{todo_id}/calendar_ignored')
async def html_set_todo_calendar_ignored(request: Request, todo_id: int, calendar_ignored: str = Form(None), current_user: User = Depends(require_login)):
    """Toggle or set the per-todo calendar_ignored flag. Accepts truthy strings for enable.

    Returns JSON when Accept includes application/json; otherwise redirects back to the todo page.
    """
    # CSRF and ownership via parent list
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    val = False
    if calendar_ignored is not None:
        val = str(calendar_ignored).lower() in ('1','true','yes','on')
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
        todo.calendar_ignored = val
        todo.modified_at = now_utc()
        sess.add(todo)
        await sess.commit()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': todo_id, 'calendar_ignored': val})
    ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
    return RedirectResponse(url=ref, status_code=303)


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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': list_id, 'completed': lst.completed})
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)



@app.get('/html_no_js/recent', response_class=HTMLResponse)
async def html_recent_lists(request: Request, current_user: User = Depends(require_login)):
    """Render recently visited lists and todos for the current user."""
    # recent page handler
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
            q = select(ListState, RecentListVisit.visited_at).join(RecentListVisit, RecentListVisit.list_id == ListState.id).where(RecentListVisit.user_id == current_user.id).where(ListState.parent_todo_id == None)
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
        recent_lists = results

    # Build recently visited todos, mirroring lists behavior
        try:
            top_n_t = int(os.getenv('RECENT_TODOS_TOP_N', str(top_n)))
        except Exception:
            top_n_t = top_n
        # First: top positioned todos
        t_top_q = select(RecentTodoVisit).where(RecentTodoVisit.user_id == current_user.id).where(RecentTodoVisit.position != None).order_by(RecentTodoVisit.position.asc()).limit(top_n_t)
        t_top_res = await sess.exec(t_top_q)
        t_top_rows = t_top_res.all()
        t_top_ids = [r.todo_id for r in t_top_rows]
        todo_results: list[dict] = []
        todo_ids: list[int] = []
        t_tags_map: dict[int, list[str]] = {}
        # load Todo rows for top preserving order; ensure user can view via parent list
        if t_top_ids:
            qtodos = select(Todo, ListState).join(ListState, ListState.id == Todo.list_id).where(Todo.id.in_(t_top_ids)).where(or_(ListState.owner_id == current_user.id, ListState.owner_id == None))
            t_res = await sess.exec(qtodos)
            # map id -> (todo, list)
            tmap = {t.id: (t, l) for t, l in t_res.all()}
            for r in t_top_rows:
                row = tmap.get(r.todo_id)
                if not row:
                    continue
                t, l = row
                todo_results.append({
                    'id': t.id,
                    'text': t.text,
                    'list_id': t.list_id,
                    'list_name': getattr(l, 'name', None),
                    'visited_at': r.visited_at,
                    'position': r.position,
                    'hashtags': [],
                })
                todo_ids.append(int(t.id))
        # Remaining by visited_at desc
        t_remaining = max(0, 25 - len(todo_results))
        if t_remaining > 0:
            tq = select(Todo, RecentTodoVisit, ListState).join(RecentTodoVisit, RecentTodoVisit.todo_id == Todo.id).join(ListState, ListState.id == Todo.list_id).where(RecentTodoVisit.user_id == current_user.id).where(or_(ListState.owner_id == current_user.id, ListState.owner_id == None))
            if t_top_ids:
                tq = tq.where(RecentTodoVisit.todo_id.notin_(t_top_ids))
            tq = tq.order_by(RecentTodoVisit.visited_at.desc()).limit(t_remaining)
            tres = await sess.exec(tq)
            for t, rvisit, l in tres.all():
                todo_results.append({
                    'id': t.id,
                    'text': t.text,
                    'list_id': t.list_id,
                    'list_name': getattr(l, 'name', None),
                    'visited_at': rvisit.visited_at,
                    'position': None,
                    'hashtags': [],
                })
                todo_ids.append(int(t.id))
    # Fetch hashtags for todos
        if todo_ids:
            try:
                qttags = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_ids))
                ttres = await sess.exec(qttags)
                for tid, tag in ttres.all():
                    try:
                        t_tags_map.setdefault(int(tid), []).append(tag)
                    except Exception:
                        continue
            except Exception:
                logger.exception('failed to fetch todo hashtags for recent todos')
        for item in todo_results:
            try:
                item['hashtags'] = t_tags_map.get(int(item['id']), [])
            except Exception:
                pass
    # Compute completion status for recent todos using each list's default completion type (per-list),
        # Compute completion status for recent todos strictly by the list's default completion type.
        # If a list has no default type, its todos are not considered completed in this view.
        try:
            completed_ids: set[int] = set()
            if todo_ids:
                qcomp = (
                    select(TodoCompletion.todo_id)
                    .join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id)
                    .where(TodoCompletion.todo_id.in_(todo_ids))
                    .where(CompletionType.name == 'default')
                    .where(TodoCompletion.done == True)
                )
                for tid_done in (await sess.exec(qcomp)).all():
                    try:
                        completed_ids.add(int(tid_done))
                    except Exception:
                        continue
                # Note: Do NOT include non-default completion types for lists that have a default.
                # For lists without a default, we already handled completion via qdone_any above.
            for item in todo_results:
                try:
                    item['completed'] = int(item['id']) in completed_ids
                except Exception:
                    item['completed'] = False
        except Exception:
            # If completion computation fails, default to not completed
            for item in todo_results:
                item['completed'] = False
        recent_todos = todo_results

    return TEMPLATES.TemplateResponse(request, 'recent.html', {"request": request, "recent": recent_lists, "recent_todos": recent_todos, "client_tz": await get_session_timezone(request)})


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
    ct = await create_completion_type_endpoint(list_id=list_id, name=name.strip(), current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        payload = {'ok': True}
        try:
            if ct is not None:
                payload.update({'id': getattr(ct, 'id', None), 'name': getattr(ct, 'name', None)})
        except Exception:
            pass
        return JSONResponse(payload)
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'list_id': list_id, 'removed': name.strip()})
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
        qct = await sess.scalars(select(CompletionType).where(CompletionType.id == completion_type_id).where(CompletionType.list_id == lst.id))
        ctype = qct.first()
        if not ctype:
            raise HTTPException(status_code=404, detail='completion type not found')
        list_id_val = int(lst.id)
        ctype_name = ctype.name
    # Toggle via API-level logic
    val = True if str(done).lower() in ('1','true','yes') else False
    await _complete_todo_impl(todo_id=todo_id, completion_type=ctype_name, done=val, current_user=current_user)
    anchor = form.get('anchor') or f'todo-{todo_id}'
    url = f'/html_no_js/lists/{list_id_val}#{anchor}'
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'todo_id': todo_id, 'completion_type': ctype_name, 'done': val})
    return RedirectResponse(url=url, status_code=303)

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
    new_todo = await _create_todo_internal(text=text, note=None, list_id=list_id, priority=None, current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        payload = {'ok': True}
        try:
            # _create_todo_internal returns a serialized dict
            if isinstance(new_todo, dict):
                payload.update({k: new_todo.get(k) for k in ('id', 'text', 'list_id')})
        except Exception:
            pass
        return JSONResponse(payload)
    return RedirectResponse(url=f"/html_no_js/lists/{list_id}", status_code=303)


@app.post("/html_no_js/todos/{todo_id}/complete")
async def html_toggle_complete(request: Request, todo_id: int, done: str = Form(...), current_user: User = Depends(require_login)):
    # convert string form value to bool
    val = True if done.lower() in ("1", "true", "yes") else False
    # find the todo's list so we can redirect back to it after marking
    async with async_session() as sess:
        q = await sess.scalars(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
    # require login for completing todos; call internal impl with authenticated user
    await _complete_todo_impl(todo_id=todo_id, done=val, current_user=current_user)
    # if the form included an anchor field, use it as a fragment
    form = await request.form()
    anchor = form.get('anchor')
    accept = (request.headers.get('Accept') or '')
    if todo and todo.list_id:
        url = f"/html_no_js/lists/{todo.list_id}"
        if anchor:
            url = f"{url}#{anchor}"
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'todo_id': todo_id, 'done': val, 'list_id': todo.list_id})
        return RedirectResponse(url=url, status_code=303)
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'todo_id': todo_id, 'done': val})
    return RedirectResponse(url="/html_no_js/", status_code=303)


@app.get("/html_no_js/todos/{todo_id}/complete")
async def html_toggle_complete_get(request: Request, todo_id: int, done: str, current_user: User = Depends(require_login)):
    # Accept 'done' as query param string and perform the same toggle as the POST handler.
    val = True if str(done).lower() in ("1", "true", "yes") else False
    async with async_session() as sess:
        q = await sess.scalars(select(Todo).where(Todo.id == todo_id))
        todo = q.first()
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # enforce ownership via parent list before toggling completion
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if lst and lst.owner_id not in (None, current_user.id):
            raise HTTPException(status_code=403, detail='forbidden')
    await _complete_todo_impl(todo_id=todo_id, done=val, current_user=current_user)
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

    # attempt deletion as a soft-delete for HTML flows: move into per-user Trash list.
    try:
        async with async_session() as sess:
            todo_row = await sess.get(Todo, todo_id)
            if not todo_row:
                # prefer explicit list_id from the form when available
                if list_id:
                    return RedirectResponse(url=f"/html_no_js/lists/{list_id}", status_code=303)
                ref = request.headers.get('Referer', '/html_no_js/')
                return RedirectResponse(url=ref, status_code=303)

            # Find or create the user's Trash list (only for authenticated users)
            if cu:
                q = await sess.scalars(select(ListState).where(ListState.owner_id == cu.id).where(ListState.name == 'Trash'))
                trash = q.first()
                if not trash:
                    trash = ListState(name='Trash', owner_id=cu.id)
                    sess.add(trash)
                    await sess.commit()
                    await sess.refresh(trash)

                # If already in trash, perform permanent delete using API-style delete
                if getattr(todo_row, 'list_id', None) == trash.id:
                    # commit session to ensure visibility
                    await sess.commit()
                    await delete_todo(todo_id=todo_id, current_user=cu)
                else:
                    # create TrashMeta and move todo into trash
                    tm = TrashMeta(todo_id=todo_id, original_list_id=getattr(todo_row, 'list_id', None))
                    sess.add(tm)
                    todo_row.list_id = trash.id
                    todo_row.modified_at = now_utc()
                    sess.add(todo_row)
                    try:
                        await _touch_list_modified(sess, tm.original_list_id)
                        await _touch_list_modified(sess, trash.id)
                    except Exception:
                        pass
                    await sess.commit()
            else:
                # anonymous: fallback to permanent delete
                await delete_todo(todo_id=todo_id, current_user=cu)
    except HTTPException as e:
        if e.status_code == 404:
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
    accept = (request.headers.get('Accept') or '')
    if list_id:
        list_url = f"/html_no_js/lists/{list_id}"
        if anchor and ref_path.startswith('/html_no_js/lists'):
            list_url = f"{list_url}#{anchor}"
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'deleted': todo_id, 'list_id': list_id})
        return RedirectResponse(url=list_url, status_code=303)

    # If no list_id is available, preserve the referer but if it points to a
    # lists page and an anchor was supplied, include the fragment.
    if anchor and ref_path.startswith('/html_no_js/lists'):
        ref_nohash = ref.split('#')[0]
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'deleted': todo_id, 'ref': f"{ref_nohash}#{anchor}"})
        return RedirectResponse(url=f"{ref_nohash}#{anchor}", status_code=303)

    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': todo_id, 'ref': ref})
    return RedirectResponse(url=ref, status_code=303)


@app.get('/html_no_js/trash', response_class=HTMLResponse)
async def html_view_trash(request: Request, current_user: User = Depends(require_login)):
    # List todos in the current user's Trash list
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
        trash = q.first()
        todos = []
        lists = []
        if trash:
            q2 = await sess.scalars(select(Todo).where(Todo.list_id == trash.id).order_by(Todo.modified_at.desc()))
            todos = q2.all()
            # also include lists that were moved under the Trash list (parent_list_id == trash.id)
            ql = await sess.exec(select(ListState).where(ListState.parent_list_id == trash.id).order_by(ListState.modified_at.desc()))
            lists = ql.all()
        # render a simple trash page
        csrf = None
        try:
            from .auth import create_csrf_token
            csrf = create_csrf_token(current_user.username)
        except Exception:
            csrf = None
        return TEMPLATES.TemplateResponse('trash.html', {'request': request, 'todos': todos, 'lists': lists, 'csrf_token': csrf})


@app.post('/html_no_js/trash/{todo_id}/restore')
async def html_restore_trash(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # ensure ownership
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # find TrashMeta
        q = await sess.scalars(select(TrashMeta).where(TrashMeta.todo_id == todo_id))
        tm = q.first()
        if not tm:
            # nothing to restore, redirect back
                ref = request.headers.get('Referer', '/html_no_js/trash')
                return _redirect_or_json(request, ref)
        original = tm.original_list_id
        if original is None:
            # if original missing, leave in place
            return _redirect_or_json(request, '/html_no_js/trash')
        # move back
        todo.list_id = original
        todo.modified_at = now_utc()
        sess.add(todo)
        # remove TrashMeta
        await sess.exec(sqlalchemy_delete(TrashMeta).where(TrashMeta.todo_id == todo_id))
        try:
            await _touch_list_modified(sess, original)
            await _touch_list_modified(sess, getattr(lst, 'id', None))
        except Exception:
            pass
        await sess.commit()
    return _redirect_or_json(request, f'/html_no_js/lists/{original}')



@app.post('/html_no_js/trash/lists/{list_id}/restore')
async def html_restore_list_trash(request: Request, list_id: int):
    # resolve current_user from cookies/session like other HTML handlers
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except Exception:
        current_user = None
    if not current_user:
        return _redirect_or_json(request, '/html_no_js/login')
    # CSRF
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    logger.info('html_restore_list_trash called list_id=%s user=%s', list_id, getattr(current_user, 'id', None))
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        logger.info('html_restore_list_trash lookup lst=%s', bool(lst))
        if not lst:
            # If the list can't be found, redirect back to trash page.
            ref = request.headers.get('Referer', '/html_no_js/trash')
            return _redirect_or_json(request, ref)
        if lst.parent_list_id is None or lst.owner_id != current_user.id:
            # not a trashed list owned by user
            raise HTTPException(status_code=403, detail='forbidden')
        q = await sess.scalars(select(ListTrashMeta).where(ListTrashMeta.list_id == list_id))
        meta = q.first()
        if not meta:
            # nothing to restore
            ref = request.headers.get('Referer', '/html_no_js/trash')
            return _redirect_or_json(request, ref)
        original_parent = meta.original_parent_list_id
        original_owner = meta.original_owner_id
        # restore owner if needed
        if original_owner is not None:
            lst.owner_id = original_owner
        # restore parent pointer
        lst.parent_list_id = original_parent
        lst.modified_at = now_utc()
        sess.add(lst)
        # remove meta
        await sess.exec(sqlalchemy_delete(ListTrashMeta).where(ListTrashMeta.list_id == list_id))
        try:
            await _touch_list_modified(sess, original_parent)
            await _touch_list_modified(sess, lst.id)
        except Exception:
            pass
        await sess.commit()
        # redirect to restored list page
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'restored': list_id})
    return _redirect_or_json(request, f'/html_no_js/lists/{lst.id}')


@app.post('/html_no_js/trash/lists/{list_id}/delete')
async def html_permanent_delete_list_trash(request: Request, list_id: int):
    # resolve current_user from cookies/session like other HTML handlers
    from .auth import get_current_user as _gcu
    try:
        current_user = await _gcu(token=None, request=request)
    except Exception:
        current_user = None
    if not current_user:
        return _redirect_or_json(request, '/html_no_js/login')
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    logger.info('html_permanent_delete_list_trash called list_id=%s user=%s', list_id, getattr(current_user, 'id', None))
    # Perform permanent deletion inline (same behavior as delete_list) to
    # avoid subtle dependency/visibility issues when called from another
    # request handler.
    async with async_session() as sess:
        q = await sess.scalars(select(ListState).where(ListState.id == list_id))
        lst = q.first()
        logger.info('html_permanent_delete_list_trash lookup lst=%s', bool(lst))
        if not lst:
            ref = request.headers.get('Referer', '/html_no_js/trash')
            return _redirect_or_json(request, ref)
        # enforce ownership
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')

        # capture todos that belong to this list
        qtodos = await sess.exec(select(Todo.id).where(Todo.list_id == list_id))
        todo_ids = [t for t in qtodos.all()]

        # detach any child sublists owned by this list
        try:
            await sess.exec(sqlalchemy_update(ListState).where(ListState.parent_list_id == list_id).values(parent_list_id=None, parent_list_position=None))
            await sess.commit()
        except Exception:
            await sess.rollback()
        # remove list-level artifacts
        await sess.exec(sqlalchemy_delete(CompletionType).where(CompletionType.list_id == list_id))
        await sess.exec(sqlalchemy_delete(ListHashtag).where(ListHashtag.list_id == list_id))
        # cleanup any trash metadata for this list (if present)
        try:
            await sess.exec(sqlalchemy_delete(ListTrashMeta).where(ListTrashMeta.list_id == list_id))
        except Exception:
            pass
        # remove collation registration rows for this list
        try:
            await sess.exec(sqlalchemy_delete(UserCollation).where(UserCollation.list_id == list_id))
        except Exception:
            pass
        # remove ItemLink edges where this list is the source or the target
        try:
            await sess.exec(sqlalchemy_delete(ItemLink).where(ItemLink.src_type == 'list').where(ItemLink.src_id == list_id))
            await sess.exec(sqlalchemy_delete(ItemLink).where(ItemLink.tgt_type == 'list').where(ItemLink.tgt_id == list_id))
        except Exception:
            pass
        # delete the list row
        await sess.exec(sqlalchemy_delete(ListState).where(ListState.id == list_id))
        await sess.commit()

        # record tombstone for the list
        try:
            ts_list = Tombstone(item_type='list', item_id=list_id)
            sess.add(ts_list)
            await sess.commit()
        except Exception:
            try:
                await sess.rollback()
            except Exception:
                pass

        # record tombstones and delete todos
        if todo_ids:
            for tid in todo_ids:
                ts = Tombstone(item_type='todo', item_id=tid)
                sess.add(ts)
            await sess.commit()
            await sess.exec(sqlalchemy_delete(TodoCompletion).where(TodoCompletion.todo_id.in_(todo_ids)))
            await sess.exec(sqlalchemy_delete(TodoHashtag).where(TodoHashtag.todo_id.in_(todo_ids)))
            await sess.exec(sqlalchemy_delete(Todo).where(Todo.id.in_(todo_ids)))
            await sess.commit()
    ref = request.headers.get('Referer', '/html_no_js/trash')
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'deleted': list_id})
    return _redirect_or_json(request, ref)

# Diagnostic: list registered routes that include 'trash' for debugging tests
try:
    trash_routes = [r.path for r in app.routes if hasattr(r, 'path') and 'trash' in r.path]
    logger.info('registered trash routes: %s', trash_routes)
except Exception:
    pass


@app.post('/html_no_js/trash/{todo_id}/delete')
async def html_permanent_delete_trash(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Use delete_todo which will permanently delete if todo is already in trash
    await delete_todo(todo_id=todo_id, current_user=current_user)
    ref = request.headers.get('Referer', '/html_no_js/trash')
    return _redirect_or_json(request, ref)



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
            "priority": getattr(todo, 'priority', None),
            # reflect calendar ignore flag for template checkbox state
            "calendar_ignored": getattr(todo, 'calendar_ignored', False),
            # persist UI preference so template can render checkbox state
            "lists_up_top": getattr(todo, 'lists_up_top', False),
            # persist Sort Links preference so template can render checkbox state
            "sort_links": getattr(todo, 'sort_links', False),
        }
        list_row = None
        if lst:
            list_row = {"id": lst.id, "name": lst.name, "completed": lst.completed, "lists_up_top": getattr(lst, 'lists_up_top', False)}
        # Fetch sublists owned by this todo. Use explicit sibling position when set,
        # else fall back to created_at ASC (older first). We'll also enrich with hashtags.
        sublists = []
        sub_ids = []
        try:
            # First select all sublists for this todo
            qsubs = select(ListState).where(ListState.parent_todo_id == todo_id)
            rsubs = await sess.exec(qsubs)
            rows = rsubs.all()
            # sort in-memory: by (position is not None, position) then created_at
            def _sort_key(l):
                pos = getattr(l, 'parent_todo_position', None)
                created = getattr(l, 'created_at', None)
                # Place items with a valid position before those with None
                return (0 if pos is not None else 1, pos if pos is not None else 0, created or now_utc())
            rows.sort(key=_sort_key)
            # collect ids for a hashtag join
            sub_ids = [l.id for l in rows if l.id is not None]
            tag_map: dict[int, list[str]] = {}
            if sub_ids:
                qlh = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(sub_ids))
                rlh = await sess.exec(qlh)
                for lid, tag in rlh.all():
                    tag_map.setdefault(lid, []).append(tag)
            for l in rows:
                sublists.append({
                    'id': l.id,
                    'name': l.name,
                    'completed': getattr(l, 'completed', False),
                    'created_at': getattr(l, 'created_at', None),
                    'modified_at': getattr(l, 'modified_at', None),
                    'hashtags': tag_map.get(l.id, []),
                    'parent_todo_position': getattr(l, 'parent_todo_position', None),
                    # placeholder for any higher-priority uncompleted todo in this sublist
                    'override_priority': None,
                    # include the sublist's own priority if present on the ORM object
                    'priority': getattr(l, 'priority', None),
                    # provide parent_list_position alias for templates that expect it
                    'parent_list_position': getattr(l, 'parent_todo_position', None),
                })
        except Exception:
            sublists = []
    # Determine highest uncompleted todo priority per sublist (if any)
        try:
            if sub_ids:
                todo_q = await sess.scalars(select(Todo.id, Todo.list_id, Todo.priority).where(Todo.list_id.in_(sub_ids)).where(Todo.priority != None))
                todo_id_rows = todo_q.all()
                todo_map: dict[int, list[tuple[int,int]]] = {}
                todo_ids = []
                for tid, lid, pri in todo_id_rows:
                    todo_map.setdefault(lid, []).append((tid, pri))
                    todo_ids.append(tid)
                completed_ids = set()
                if todo_ids:
                    try:
                        qcomp = select(TodoCompletion.todo_id).join(CompletionType, CompletionType.id == TodoCompletion.completion_type_id).where(TodoCompletion.todo_id.in_(todo_ids)).where(CompletionType.name == 'default').where(TodoCompletion.done == True)
                        cres = await sess.exec(qcomp)
                        completed_ids = set(r[0] if isinstance(r, tuple) else r for r in cres.all())
                    except Exception:
                        completed_ids = set()
                # diagnostic logging to help debug missing override priorities
                try:
                    logger.info('todo override diagnostic: todo_id_rows=%s', todo_id_rows)
                    logger.info('todo override diagnostic: todo_map keys=%s', list(todo_map.keys()))
                    logger.info('todo override diagnostic: completed_ids=%s', completed_ids)
                except Exception:
                    pass
                # compute highest uncompleted priority per sublist
                for sub in sublists:
                    lid = sub.get('id')
                    candidates = todo_map.get(lid, [])
                    max_p = None
                    for tid, pri in candidates:
                        if tid in completed_ids:
                            continue
                        try:
                            if pri is None:
                                continue
                            pv = int(pri)
                        except Exception:
                            continue
                        if max_p is None or pv > max_p:
                            max_p = pv
                    if max_p is not None:
                        sub['override_priority'] = max_p
        except Exception:
            # failure computing overrides should not break todo rendering
            pass
        # Fetch outgoing links from this todo
        links: list[dict] = []
        try:
            qlnk = select(ItemLink).where(ItemLink.src_type == 'todo').where(ItemLink.src_id == todo_id).order_by(ItemLink.position.asc().nullslast(), ItemLink.created_at.asc())
            rlnk = await sess.exec(qlnk)
            rows = rlnk.all()
            todo_targets = [r.tgt_id for r in rows if r.tgt_type == 'todo']
            list_targets = [r.tgt_id for r in rows if r.tgt_type == 'list']
            todo_map: dict[int, dict] = {}
            list_map: dict[int, dict] = {}
            # Preload target titles
            if todo_targets:
                qtt = await sess.exec(select(Todo.id, Todo.text).where(Todo.id.in_(todo_targets)))
                for tid, txt in qtt.all():
                    todo_map[int(tid)] = {'id': int(tid), 'text': txt}
            if list_targets:
                qll = await sess.exec(select(ListState.id, ListState.name).where(ListState.id.in_(list_targets)))
                for lid, name in qll.all():
                    list_map[int(lid)] = {'id': int(lid), 'name': name}
            # Preload hashtags for targets
            tags_map_todo: dict[int, list[str]] = {}
            tags_map_list: dict[int, list[str]] = {}
            if todo_targets:
                qth = select(TodoHashtag.todo_id, Hashtag.tag).join(Hashtag, Hashtag.id == TodoHashtag.hashtag_id).where(TodoHashtag.todo_id.in_(todo_targets))
                rth = await sess.exec(qth)
                for tid, tag in rth.all():
                    try:
                        tid_i = int(tid)
                    except Exception:
                        continue
                    if isinstance(tag, str) and tag:
                        tags_map_todo.setdefault(tid_i, []).append(tag)
            # Determine completion status for todo targets using default completion types
            todo_completed_ids: set[int] = set()
            try:
                if todo_map:
                    todo_list_ids = list({v.get('list_id') for v in todo_map.values() if v.get('list_id') is not None})
                    default_ct_ids: dict[int, int] = {}
                    if todo_list_ids:
                        qct = select(CompletionType).where(CompletionType.list_id.in_(todo_list_ids)).where(CompletionType.name == 'default')
                        for ct in (await sess.exec(qct)).all():
                            default_ct_ids[int(ct.list_id)] = int(ct.id)
                    if default_ct_ids:
                        qdone = select(TodoCompletion.todo_id).where(TodoCompletion.todo_id.in_(todo_targets)).where(TodoCompletion.completion_type_id.in_(list(default_ct_ids.values()))).where(TodoCompletion.done == True)
                        for (tid_done,) in (await sess.exec(qdone)).all():
                            try:
                                todo_completed_ids.add(int(tid_done))
                            except Exception:
                                continue
            except Exception:
                todo_completed_ids = set()
            if list_targets:
                qlh = select(ListHashtag.list_id, Hashtag.tag).join(Hashtag, Hashtag.id == ListHashtag.hashtag_id).where(ListHashtag.list_id.in_(list_targets))
                rlh = await sess.exec(qlh)
                for lid, tag in rlh.all():
                    try:
                        lid_i = int(lid)
                    except Exception:
                        continue
                    if isinstance(tag, str) and tag:
                        tags_map_list.setdefault(lid_i, []).append(tag)
            for r in rows:
                d = {'id': r.id, 'tgt_type': r.tgt_type, 'tgt_id': r.tgt_id, 'label': r.label, 'position': r.position}
                if r.tgt_type == 'todo':
                    t = todo_map.get(int(r.tgt_id))
                    if t:
                        d['title'] = t.get('text')
                        d['href'] = f"/html_no_js/todos/{t['id']}"
                        d['tags'] = tags_map_todo.get(int(r.tgt_id), [])
                        d['completed'] = (int(r.tgt_id) in todo_completed_ids)
                elif r.tgt_type == 'list':
                    l = list_map.get(int(r.tgt_id))
                    if l:
                        d['title'] = l.get('name')
                        d['href'] = f"/html_no_js/lists/{l['id']}"
                        d['tags'] = tags_map_list.get(int(r.tgt_id), [])
                links.append(d)
        except Exception:
            links = []
    csrf_token = None
    from .auth import create_csrf_token
    csrf_token = create_csrf_token(current_user.username)
    client_tz = await get_session_timezone(request)
    # debug: log sublists passed to template for easier diagnosis (temporary)
    try:
        logger.info('rendering todo %s sublists: %s', todo_id, sublists)
    except Exception:
        pass
    # Seed per-request fn:link label cache with this todo and its list for better inline rendering
    try:
        cache = _fn_link_label_cache.get()
        if not isinstance(cache, dict):
            cache = {}
        if todo_row and getattr(todo_row, 'id', None) and getattr(todo_row, 'text', None):
            cache[f"todo:{int(todo_row.id)}"] = todo_row.text
        if list_row and getattr(list_row, 'id', None) and getattr(list_row, 'name', None):
            cache[f"list:{int(list_row.id)}"] = list_row.name
        _fn_link_label_cache.set(cache)
    except Exception:
        pass
    # If this todo requests sorting of inline fn:link tokens, pre-process the note
    try:
        if todo_row.get('sort_links') and todo_row.get('note'):
            raw_note = str(todo_row.get('note') or '')
            # find all fn:link tags and their spans
            link_tag_re = re.compile(r"(\{\{\s*fn:link[^{\}]*\}\})")
            parts = link_tag_re.split(raw_note)
            # Collect link tokens with their original index in parts
            link_indices = []
            for idx, part in enumerate(parts):
                if link_tag_re.fullmatch(part):
                    link_indices.append((idx, part))
            if len(link_indices) > 1:
                # Resolve all targets and priorities in batch: parse each token to extract target=todo:ID or todo=ID
                targets = []  # tuples (idx, kind, id, original_token)
                for idx, token in link_indices:
                    # crude parse to extract target id
                    m = re.search(r"target\s*=\s*['\"]?(todo|list)[:]?([0-9]+)['\"]?", token)
                    if not m:
                        m = re.search(r"todo\s*=\s*['\"]?([0-9]+)['\"]?", token)
                        if m:
                            kind = 'todo'
                            tid = int(m.group(1))
                        else:
                            kind = None
                            tid = None
                    else:
                        kind = m.group(1)
                        tid = int(m.group(2))
                    if kind and tid:
                        targets.append((idx, kind, int(tid), token))
                # Batch fetch priorities for todos/lists
                todo_ids = [t[2] for t in targets if t[1] == 'todo']
                list_ids = [t[2] for t in targets if t[1] == 'list']
                pr_map = {}
                if todo_ids:
                    try:
                        q = await sess.exec(select(Todo.id, Todo.priority).where(Todo.id.in_(todo_ids)))
                        for tid, pr in q.all():
                            try:
                                pr_map[f"todo:{int(tid)}"] = int(pr) if pr is not None else None
                            except Exception:
                                pr_map[f"todo:{int(tid)}"] = None
                    except Exception:
                        pass
                if list_ids:
                    try:
                        q = await sess.exec(select(ListState.id, ListState.priority).where(ListState.id.in_(list_ids)))
                        for lid, pr in q.all():
                            try:
                                pr_map[f"list:{int(lid)}"] = int(pr) if pr is not None else None
                            except Exception:
                                pr_map[f"list:{int(lid)}"] = None
                    except Exception:
                        pass
                # Build a list of (priority, original_order, idx, token)
                enriched = []
                for order, (idx, kind, tid, token) in enumerate(targets):
                    key = f"{kind}:{tid}"
                    pr = pr_map.get(key)
                    # Use -inf for None so they sort last
                    sort_pr = pr if pr is not None else -9999
                    enriched.append((sort_pr, order, idx, token))
                # Sort by priority desc, then original order asc
                enriched.sort(key=lambda x: (-x[0], x[1]))
                # Replace the parts at the token indices in original order of indices with the sorted tokens
                sorted_tokens = [e[3] for e in enriched]
                for i, (orig_idx, _) in enumerate(link_indices):
                    parts[orig_idx] = sorted_tokens[i]
                # Reconstruct note
                todo_row['note'] = ''.join(parts)
    except Exception:
        # On any failure, leave note unchanged
        pass
    # debug: log outgoing links structure for this todo (temporary)
    try:
        logger.info('TODO_LINKS id=%s links=%s', todo_id, json.dumps(links, default=str, ensure_ascii=False))
    except Exception:
        try:
            logger.info('TODO_LINKS id=%s links=%s', todo_id, str(links))
        except Exception:
            pass
    # Active collations for this user and whether this todo is linked to each
    active_collations: list[dict] = []
    try:
        async with async_session() as sess2:
            q = await sess2.exec(select(UserCollation).where(UserCollation.user_id == current_user.id).where(UserCollation.active == True))
            rows = q.all()
            ids = [r.list_id for r in rows]
            names = {}
            if ids:
                r2 = await sess2.exec(select(ListState.id, ListState.name).where(ListState.id.in_(ids)).where(ListState.owner_id == current_user.id))
                for lid, name in r2.all():
                    names[int(lid)] = name
            # Exclude any lists that are currently in Trash (parented to user's Trash list)
            trashed: set[int] = set()
            if ids:
                trash_id = None
                try:
                    trq = await sess2.scalars(select(ListState.id).where(ListState.owner_id == current_user.id).where(ListState.name == 'Trash'))
                    trash_id = trq.first()
                except Exception:
                    trash_id = None
                if trash_id is not None:
                    tq = await sess2.scalars(select(ListState.id).where(ListState.id.in_(ids)).where(ListState.parent_list_id == trash_id))
                    trashed = set(int(v) for v in tq.all())
            linked_map = {}
            if ids:
                r3 = await sess2.scalars(
                    select(ItemLink.src_id)
                    .where(ItemLink.src_type == 'list')
                    .where(ItemLink.tgt_type == 'todo')
                    .where(ItemLink.tgt_id == todo_id)
                    .where(ItemLink.src_id.in_(ids))
                )
                for sid in r3.all():
                    try:
                        linked_map[int(sid)] = True
                    except Exception:
                        pass
            # Include only lists that exist for this user (in names) and are not trashed
            active_collations = [
                {
                    'list_id': int(r.list_id),
                    'name': names.get(int(r.list_id)),
                    'linked': bool(linked_map.get(int(r.list_id), False)),
                }
                for r in rows if (int(r.list_id) in names and int(r.list_id) not in trashed)
            ]
    except Exception:
        # If anything fails here, log and fall back to empty so the page still renders
        try:
            logger.exception('Failed building active_collations for todo_id=%s user_id=%s', todo_id, getattr(current_user, 'id', None))
        except Exception:
            pass
        active_collations = []

    # Best-effort: record this todo visit for the current user so it appears on the recent page
    try:
        await record_todo_visit(todo_id=todo_id, current_user=current_user)
    except Exception:
        try:
            logger.exception('failed to record todo visit for todo %s', todo_id)
        except Exception:
            pass

    # pass plain dicts (with datetime objects preserved) to avoid lazy DB loads
    return TEMPLATES.TemplateResponse(request, 'todo.html', {"request": request, "todo": todo_row, "completed": completed, "list": list_row, "csrf_token": csrf_token, "client_tz": client_tz, "tags": todo_tags, "all_hashtags": all_hashtags, 'sublists': sublists, 'links': links, 'active_collations': active_collations})


# ===== Links: add/remove for list and todo (no-JS HTML and JSON) =====
class AddLinkPayload(BaseModel):
    tgt_type: str
    tgt_id: int
    label: Optional[str] = None
    position: Optional[int] = None


async def _verify_owner_for_src(sess, *, src_type: str, src_id: int, current_user: User) -> int:
    if src_type == 'list':
        lst = await sess.get(ListState, src_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        return lst.owner_id
    elif src_type == 'todo':
        td = await sess.get(Todo, src_id)
        if not td:
            raise HTTPException(status_code=404, detail='todo not found')
        ql = await sess.exec(select(ListState).where(ListState.id == td.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        return lst.owner_id
    else:
        raise HTTPException(status_code=400, detail='invalid src_type')


async def _validate_target_exists(sess, *, tgt_type: str, tgt_id: int) -> None:
    if tgt_type == 'list':
        if not (await sess.get(ListState, tgt_id)):
            raise HTTPException(status_code=404, detail='target list not found')
    elif tgt_type == 'todo':
        if not (await sess.get(Todo, tgt_id)):
            raise HTTPException(status_code=404, detail='target todo not found')
    else:
        raise HTTPException(status_code=400, detail='invalid tgt_type')


async def _add_link_core(sess, *, src_type: str, src_id: int, payload: AddLinkPayload, current_user: User) -> dict:
    owner_id = await _verify_owner_for_src(sess, src_type=src_type, src_id=src_id, current_user=current_user)
    await _validate_target_exists(sess, tgt_type=payload.tgt_type, tgt_id=payload.tgt_id)
    # compute default position if not provided
    pos = payload.position
    if pos is None:
        q = await sess.exec(select(ItemLink.position).where(ItemLink.src_type == src_type).where(ItemLink.src_id == src_id))
        vals = [v[0] if isinstance(v, (tuple, list)) else v for v in q.fetchall()]
        try:
            pos = (max([vv for vv in vals if vv is not None]) + 1) if vals else 0
        except Exception:
            pos = 0
    link = ItemLink(src_type=src_type, src_id=src_id, tgt_type=payload.tgt_type, tgt_id=payload.tgt_id, label=(payload.label or None), position=pos, owner_id=owner_id)
    sess.add(link)
    try:
        await sess.commit()
    except IntegrityError:
        await sess.rollback()
        # already exists: fetch existing
        q = await sess.exec(select(ItemLink).where(ItemLink.src_type == src_type, ItemLink.src_id == src_id, ItemLink.tgt_type == payload.tgt_type, ItemLink.tgt_id == payload.tgt_id))
        link = q.first()
        if not link:
            raise HTTPException(status_code=409, detail='link exists')
    await sess.refresh(link)
    return {'ok': True, 'id': link.id, 'label': link.label, 'position': link.position}


async def _remove_link_core(sess, *, src_type: str, src_id: int, link_id: int, current_user: User) -> dict:
    await _verify_owner_for_src(sess, src_type=src_type, src_id=src_id, current_user=current_user)
    link = await sess.get(ItemLink, link_id)
    if not link or link.src_type != src_type or link.src_id != src_id:
        raise HTTPException(status_code=404, detail='link not found')
    await sess.delete(link)
    try:
        await sess.commit()
    except Exception:
        await sess.rollback()
    return {'ok': True, 'deleted': link_id}


@app.post('/html_no_js/lists/{list_id}/links')
async def html_add_list_link(request: Request, list_id: int, tgt_type: str = Form(...), tgt_id: int = Form(...), label: str = Form(None), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    payload = AddLinkPayload(tgt_type=tgt_type, tgt_id=int(tgt_id), label=label)
    async with async_session() as sess:
        res = await _add_link_core(sess, src_type='list', src_id=list_id, payload=payload, current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


@app.post('/html_no_js/lists/{list_id}/links/{link_id}/delete')
async def html_remove_list_link(request: Request, list_id: int, link_id: int, current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        res = await _remove_link_core(sess, src_type='list', src_id=list_id, link_id=link_id, current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


@app.post('/html_no_js/todos/{todo_id}/links')
async def html_add_todo_link(request: Request, todo_id: int, tgt_type: str = Form(...), tgt_id: int = Form(...), label: str = Form(None), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    payload = AddLinkPayload(tgt_type=tgt_type, tgt_id=int(tgt_id), label=label)
    async with async_session() as sess:
        res = await _add_link_core(sess, src_type='todo', src_id=todo_id, payload=payload, current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/todos/{todo_id}', status_code=303)


@app.post('/html_no_js/todos/{todo_id}/links/{link_id}/delete')
async def html_remove_todo_link(request: Request, todo_id: int, link_id: int, current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        res = await _remove_link_core(sess, src_type='todo', src_id=todo_id, link_id=link_id, current_user=current_user)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/todos/{todo_id}', status_code=303)


@app.post('/html_no_js/todos/{todo_id}/sublists/create')
async def html_create_sublist(request: Request, todo_id: int, name: str = Form(...), current_user: User = Depends(require_login)):
    # require CSRF and validate ownership via parent list
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        todo = await sess.get(Todo, todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail='todo not found')
        # check ownership via parent list
        ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
        lst = ql.first()
        if not lst or lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        # normalize name: strip leading ws and remove inline hashtags
        norm_name = remove_hashtags_from_text((name or '').lstrip())
        if not norm_name:
            raise HTTPException(status_code=400, detail='name is required')
        # Determine next position among siblings (append to end)
        try:
            qmax = await sess.exec(select(ListState.parent_todo_position).where(ListState.parent_todo_id == todo_id))
            positions = [p[0] if isinstance(p, (tuple, list)) else p for p in qmax.fetchall()]
            next_pos = (max([pp for pp in positions if pp is not None]) + 1) if positions else 0
        except Exception:
            next_pos = 0
        sub = ListState(name=norm_name, owner_id=current_user.id, parent_todo_id=todo_id, parent_todo_position=next_pos)
        sess.add(sub)
        await sess.commit()
        await sess.refresh(sub)
        # default completion type for the new sublist
        try:
            dc = CompletionType(name='default', list_id=sub.id)
            sess.add(dc)
            await sess.commit()
        except Exception:
            await sess.rollback()
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'id': sub.id, 'name': sub.name, 'parent_todo_id': todo_id})
    return _redirect_or_json(request, f'/html_no_js/todos/{todo_id}')


async def _normalize_sublist_positions(sess, parent_todo_id: int):
    """Ensure sibling positions are contiguous starting at 0 for a parent's sublists."""
    q = await sess.scalars(select(ListState).where(ListState.parent_todo_id == parent_todo_id))
    rows = q.all()
    # order by current position (None last), then created_at
    def _key(l):
        pos = getattr(l, 'parent_todo_position', None)
        cr = getattr(l, 'created_at', None) or now_utc()
        return (0 if pos is not None else 1, pos if pos is not None else 0, cr)
    rows.sort(key=_key)
    changed = False
    for idx, l in enumerate(rows):
        if getattr(l, 'parent_todo_position', None) != idx:
            l.parent_todo_position = idx
            sess.add(l)
            changed = True
    if changed:
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()


class MoveSublistRequest(BaseModel):
    direction: str


async def _move_sublist_core(sess, *, current_user: User, todo_id: int, list_id: int, direction: str) -> dict:
    """Core logic to move a sublist up or down within its parent's ordering.
    Requires ownership via the parent todo's list.
    Returns {ok: bool, moved: bool}.
    """
    if direction not in ('up', 'down'):
        raise HTTPException(status_code=400, detail='invalid direction')
    # validate parent todo and ownership via its list
    todo = await sess.get(Todo, todo_id)
    if not todo:
        raise HTTPException(status_code=404, detail='todo not found')
    ql = await sess.exec(select(ListState).where(ListState.id == todo.list_id))
    lst = ql.first()
    if not lst or lst.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='forbidden')
    # validate sublist
    sub = await sess.get(ListState, list_id)
    if not sub or getattr(sub, 'parent_todo_id', None) != todo_id:
        raise HTTPException(status_code=404, detail='sublist not found')
    # get siblings and positions
    q = await sess.scalars(select(ListState).where(ListState.parent_todo_id == todo_id))
    sibs = q.all()
    # ensure positions are normalized first
    await _normalize_sublist_positions(sess, todo_id)
    await sess.refresh(sub)
    # find neighbor to swap with
    cur_pos = getattr(sub, 'parent_todo_position', None)
    if cur_pos is None:
        # assign to end
        try:
            maxp = max([getattr(s, 'parent_todo_position', -1) or -1 for s in sibs])
        except Exception:
            maxp = -1
        sub.parent_todo_position = maxp + 1
        sess.add(sub)
        await sess.commit()
        cur_pos = sub.parent_todo_position
    if direction == 'up' and cur_pos > 0:
        target_pos = cur_pos - 1
    elif direction == 'down':
        try:
            maxp = max([getattr(s, 'parent_todo_position', -1) or -1 for s in sibs])
        except Exception:
            maxp = -1
        if cur_pos < maxp:
            target_pos = cur_pos + 1
        else:
            target_pos = None
    else:
        target_pos = None
    if target_pos is None:
        await _normalize_sublist_positions(sess, todo_id)
        return {'ok': True, 'moved': False}
    # find sibling currently at target_pos
    other = None
    for s in sibs:
        if getattr(s, 'parent_todo_position', None) == target_pos:
            other = s
            break
    # swap positions
    sub.parent_todo_position, target_pos_val = target_pos, cur_pos
    sess.add(sub)
    if other:
        other.parent_todo_position = target_pos_val
        sess.add(other)
    await sess.commit()
    await _normalize_sublist_positions(sess, todo_id)
    return {'ok': True, 'moved': True}


@app.post('/api/todos/{todo_id}/sublists/{list_id}/move')
async def api_move_sublist(request: Request, todo_id: int, list_id: int, payload: MoveSublistRequest, current_user: User = Depends(require_login)):
    """Move a sublist up or down within its parent's ordering.
    Accepts JSON {direction: 'up'|'down'}. Requires ownership.
    """
    # Allow bearer-token API without CSRF; require CSRF for cookie browser
    auth_hdr = request.headers.get('authorization')
    if not auth_hdr:
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = body.get('_csrf') or request.cookies.get('csrf_token')
        from .auth import verify_csrf_token
        if not token or not verify_csrf_token(token, current_user.username):
            raise HTTPException(status_code=403, detail='invalid csrf token')
    direction = payload.direction if payload and getattr(payload, 'direction', None) else None
    async with async_session() as sess:
        return await _move_sublist_core(sess, current_user=current_user, todo_id=todo_id, list_id=list_id, direction=direction)


@app.post('/html_no_js/todos/{todo_id}/sublists/{list_id}/move')
async def html_move_sublist(request: Request, todo_id: int, list_id: int, direction: str = Form(...), current_user: User = Depends(require_login)):
    # CSRF + ownership; reuse API logic
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # Perform move directly to avoid double-reading the request body/CSRF checks
    async with async_session() as sess:
        res = await _move_sublist_core(sess, current_user=current_user, todo_id=todo_id, list_id=list_id, direction=direction)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/todos/{todo_id}', status_code=303)


# ===== List -> List sublists (nested lists) =====
async def _normalize_list_sublists_positions(sess, parent_list_id: int):
    q = await sess.scalars(select(ListState).where(ListState.parent_list_id == parent_list_id))
    rows = q.all()
    def _key(l):
        pos = getattr(l, 'parent_list_position', None)
        cr = getattr(l, 'created_at', None) or now_utc()
        return (0 if pos is not None else 1, pos if pos is not None else 0, cr)
    rows.sort(key=_key)
    changed = False
    for idx, l in enumerate(rows):
        if getattr(l, 'parent_list_position', None) != idx:
            l.parent_list_position = idx
            sess.add(l)
            changed = True
    if changed:
        try:
            await sess.commit()
        except Exception:
            await sess.rollback()


@app.post('/html_no_js/lists/{list_id}/sublists/create')
async def html_create_list_sublist(request: Request, list_id: int, name: str = Form(...), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        lst = await sess.get(ListState, list_id)
        if not lst:
            raise HTTPException(status_code=404, detail='list not found')
        if lst.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='forbidden')
        norm_name = remove_hashtags_from_text((name or '').lstrip())
        if not norm_name:
            raise HTTPException(status_code=400, detail='name is required')
        try:
            qmax = await sess.exec(select(ListState.parent_list_position).where(ListState.parent_list_id == list_id))
            positions = [p[0] if isinstance(p, (tuple, list)) else p for p in qmax.fetchall()]
            next_pos = (max([pp for pp in positions if pp is not None]) + 1) if positions else 0
        except Exception:
            next_pos = 0
        sub = ListState(name=norm_name, owner_id=current_user.id, parent_list_id=list_id, parent_list_position=next_pos)
        sess.add(sub)
        await sess.commit()
        await sess.refresh(sub)
        try:
            dc = CompletionType(name='default', list_id=sub.id)
            sess.add(dc)
            await sess.commit()
        except Exception:
            await sess.rollback()
        accept = (request.headers.get('Accept') or '')
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'id': sub.id, 'name': sub.name, 'parent_list_id': list_id})
        return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


class MoveListSublistRequest(BaseModel):
    direction: str


async def _move_list_sublist_core(sess, *, current_user: User, list_id: int, sub_id: int, direction: str) -> dict:
    if direction not in ('up', 'down'):
        raise HTTPException(status_code=400, detail='invalid direction')
    lst = await sess.get(ListState, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail='list not found')
    if lst.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='forbidden')
    sub = await sess.get(ListState, sub_id)
    if not sub or getattr(sub, 'parent_list_id', None) != list_id:
        raise HTTPException(status_code=404, detail='sublist not found')
    q = await sess.scalars(select(ListState).where(ListState.parent_list_id == list_id))
    sibs = q.all()
    await _normalize_list_sublists_positions(sess, list_id)
    await sess.refresh(sub)
    cur_pos = getattr(sub, 'parent_list_position', None)
    if cur_pos is None:
        try:
            maxp = max([getattr(s, 'parent_list_position', -1) or -1 for s in sibs])
        except Exception:
            maxp = -1
        sub.parent_list_position = maxp + 1
        sess.add(sub)
        await sess.commit()
        cur_pos = sub.parent_list_position
    if direction == 'up' and cur_pos > 0:
        target_pos = cur_pos - 1
    elif direction == 'down':
        try:
            maxp = max([getattr(s, 'parent_list_position', -1) or -1 for s in sibs])
        except Exception:
            maxp = -1
        target_pos = cur_pos + 1 if cur_pos < maxp else None
    else:
        target_pos = None
    if target_pos is None:
        await _normalize_list_sublists_positions(sess, list_id)
        return {'ok': True, 'moved': False}
    other = None
    for s in sibs:
        if getattr(s, 'parent_list_position', None) == target_pos:
            other = s
            break
    sub.parent_list_position, target_pos_val = target_pos, cur_pos
    sess.add(sub)
    if other:
        other.parent_list_position = target_pos_val
        sess.add(other)
    await sess.commit()
    await _normalize_list_sublists_positions(sess, list_id)
    return {'ok': True, 'moved': True}


@app.post('/html_no_js/lists/{list_id}/sublists/{sub_id}/move')
async def html_move_list_sublist(request: Request, list_id: int, sub_id: int, direction: str = Form(...), current_user: User = Depends(require_login)):
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    async with async_session() as sess:
        res = await _move_list_sublist_core(sess, current_user=current_user, list_id=list_id, sub_id=sub_id, direction=direction)
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse(res)
    return RedirectResponse(url=f'/html_no_js/lists/{list_id}', status_code=303)


@app.post('/html_no_js/todos/{todo_id}/edit')
async def html_edit_todo(request: Request, todo_id: int, text: str = Form(...), note: str = Form(None), current_user: User = Depends(require_login)):
    # require CSRF for authenticated users (always logged in here)
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    # perform update and return either a redirect (normal browsers) or JSON (AJAX/fetch)
    # Build payload for internal updater; only include fields provided by the form
    payload = { 'text': text }
    if 'note' in form:
        payload['note'] = note
    # Persist Sort Links preference when provided by the form (hidden input set by client-side JS)
    if 'sort_links' in form:
        payload['sort_links'] = form.get('sort_links')
    result = await _update_todo_internal(todo_id, payload, current_user)
    accept = request.headers.get('accept', '')
    if 'application/json' in accept.lower():
        # return JSON result for AJAX autosave clients
        return result
    return RedirectResponse(url=f"/html_no_js/todos/{todo_id}", status_code=303)


@app.post('/html_no_js/todos/{todo_id}/sort_links')
async def html_set_sort_links(request: Request, todo_id: int, current_user: User = Depends(require_login)):
    """Minimal endpoint to persist the sort_links preference from a checkbox toggle.

    Accepts form POSTs with '_csrf' and 'sort_links' and forwards to the internal updater.
    Returns JSON when client requests application/json.
    """
    form = await request.form()
    token = form.get('_csrf')
    from .auth import verify_csrf_token
    if not token or not verify_csrf_token(token, current_user.username):
        raise HTTPException(status_code=403, detail='invalid csrf token')
    payload = {}
    if 'sort_links' in form:
        payload['sort_links'] = form.get('sort_links')
    # If no sort_links provided, treat as no-op
    if payload:
        result = await _update_todo_internal(todo_id, payload, current_user)
    else:
        result = {'ok': True}
    accept = request.headers.get('accept', '')
    if 'application/json' in accept.lower():
        return JSONResponse(result)
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
    accept = (request.headers.get('Accept') or '')
    if 'application/json' in accept.lower():
        return JSONResponse({'ok': True, 'removed': tag})
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
        accept = (request.headers.get('Accept') or '')
        if 'application/json' in accept.lower():
            return JSONResponse({'ok': True, 'tag': ntag, 'todo_id': todo_id})
        # redirect back
        ref = request.headers.get('Referer', f'/html_no_js/todos/{todo_id}')
        return RedirectResponse(url=ref, status_code=303)

