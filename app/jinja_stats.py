"""Lightweight Jinja in-memory cache stats per request.

Goals
- Count how many templates are looked up vs. how many were loaded from source
  (a proxy for compile events) using Jinja's built-in in-memory cache only.
- Expose simple percentages via response headers without changing behavior.

Usage
- Call install_jinja_cache_stats(app, [env1, env2, ...]) once at startup.
- Enable with env JINJA_CACHE_STATS=1 (the installer reads this flag).
"""
from __future__ import annotations

from typing import Iterable, Optional
from contextvars import ContextVar
from datetime import datetime, timezone
import os

try:
    from starlette.middleware.base import BaseHTTPMiddleware
except Exception:  # pragma: no cover - imported by FastAPI app only
    BaseHTTPMiddleware = object  # type: ignore


# Per-request counters stored in a ContextVar so concurrent requests are isolated.
_stats_var: ContextVar[Optional[dict]] = ContextVar("jinja_cache_stats", default=None)
# Track current request URL for logging
_req_url_var: ContextVar[Optional[str]] = ContextVar("jinja_req_url", default=None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _log_dir() -> str:
    try:
        path = os.path.join(os.getcwd(), 'debug_logs')
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        return os.getcwd()


def _log_path() -> str:
    # Write under debug_logs for easy discovery
    try:
        return os.path.join(_log_dir(), 'jinja_stats.txt')
    except Exception:
        return 'jinja_stats.txt'


def _append_log(line: str) -> None:
    try:
        with open(_log_path(), 'a', encoding='utf-8') as fh:
            fh.write(line.rstrip('\n') + '\n')
    except Exception:
        # Swallow logging errors
        pass


def _reset_stats() -> dict:
    data = {
        "get_calls": 0,          # Environment.get_template calls
        "load_calls": 0,         # loader.get_source calls (proxy for compile/miss)
        "unique_templates": set(),  # names seen in get_template
        "loaded_names": [],      # sequence of names passed to loader.get_source
    }
    _stats_var.set(data)
    return data


def _get_stats() -> dict:
    data = _stats_var.get()
    if data is None:
        data = _reset_stats()
    return data


def _patch_env(env) -> None:
    """Monkey patch a Jinja2 Environment to count get_template and loader.get_source.

    Safe, idempotent: calling multiple times won't double-wrap.
    """
    # Guard if already patched
    if getattr(env, "__cache_stats_patched__", False):
        return

    # Wrap Environment.get_template
    orig_get_template = env.get_template

    def get_template_patched(name, parent=None, globals=None):  # type: ignore[no-redef]
        st = _get_stats()
        st["get_calls"] += 1
        try:
            st["unique_templates"].add(str(name))
        except Exception:
            pass
        # capture pre-call counters to classify cache vs compile
        before_loads = int(st.get("load_calls", 0) or 0)
        before_loaded_len = len(st.get("loaded_names", []))
        # call original
        tpl = orig_get_template(name, parent=parent, globals=globals)
        # classification
        after_loads = int(st.get("load_calls", 0) or 0)
        delta = after_loads - before_loads
        loaded_seq = st.get("loaded_names", [])
        recent = loaded_seq[before_loaded_len:]
        # Determine whether this specific name was loaded (compiled) now
        compiled_here = delta > 0 and (str(name) in [str(x) for x in recent])
        klass = "compiled" if compiled_here else ("loaded-deps" if delta > 0 else "cached")
        url = _req_url_var.get() or "-"
        ts = _now_iso()
        try:
            _append_log(f"{ts} url={url} template={name} status={klass} loads_delta={delta}")
        except Exception:
            pass
        return tpl

    env.get_template = get_template_patched  # type: ignore[assignment]

    # Wrap loader.get_source if available
    loader = getattr(env, "loader", None)
    if loader is not None and hasattr(loader, "get_source"):
        orig_get_source = loader.get_source

        def get_source_patched(environment, template):  # type: ignore[misc]
            st = _get_stats()
            st["load_calls"] += 1
            try:
                st["loaded_names"].append(str(template))
            except Exception:
                pass
            return orig_get_source(environment, template)

        loader.get_source = get_source_patched  # type: ignore[assignment]

    env.__cache_stats_patched__ = True


class JinjaCacheStatsMiddleware(BaseHTTPMiddleware):
    """Resets per-request counters and adds summary headers on responses."""

    def __init__(self, app, header_prefix: str = "X-Jinja-"):
        super().__init__(app)
        self.header_prefix = header_prefix

    async def dispatch(self, request, call_next):
        _reset_stats()
        try:
            _req_url_var.set(str(getattr(request, 'url', '')))  # store for log lines
        except Exception:
            _req_url_var.set(None)
        response = await call_next(request)
        try:
            st = _get_stats()
            gets = int(st.get("get_calls", 0) or 0)
            loads = int(st.get("load_calls", 0) or 0)
            uniq = st.get("unique_templates", set())
            uniq_n = int(len(uniq)) if isinstance(uniq, set) else 0

            # Percent of unique templates that required a source load (compile)
            pct_unique = (100.0 * loads / uniq_n) if uniq_n else 0.0
            # Percent of get_template calls that triggered a source load
            pct_calls = (100.0 * loads / gets) if gets else 0.0

            hp = self.header_prefix
            response.headers[f"{hp}Get-Calls"] = str(gets)
            response.headers[f"{hp}Load-Calls"] = str(loads)
            response.headers[f"{hp}Unique-Templates"] = str(uniq_n)
            response.headers[f"{hp}Compile-Percent-Unique"] = f"{pct_unique:.2f}"
            response.headers[f"{hp}Compile-Percent-Calls"] = f"{pct_calls:.2f}"
            # Provide the log file location for discovery (relative if possible)
            try:
                lp = _log_path()
                cwd = os.getcwd()
                rel = lp
                try:
                    rel = os.path.relpath(lp, cwd)
                except Exception:
                    pass
                response.headers[f"{hp}Log-File"] = rel
            except Exception:
                pass
        except Exception:
            # Never break the response due to stats issues
            pass
        return response


def install_jinja_cache_stats(app, envs: Iterable) -> None:
    """Patch given Jinja2 Environments and add middleware to expose stats.

    Enabled only when env JINJA_CACHE_STATS is truthy (1/true/yes/on).
    """
    import os

    try:
        flag = str(os.getenv("JINJA_CACHE_STATS", "0")).lower() in ("1", "true", "yes", "on")
        if not flag:
            return

        # Patch environments
        for env in envs:
            try:
                _patch_env(env)
            except Exception:
                pass

        # Add middleware once
        app.add_middleware(JinjaCacheStatsMiddleware)

        # Emit a startup notice with the log path for discoverability
        try:
            lp = _log_path()
            cwd = os.getcwd()
            rel = lp
            try:
                rel = os.path.relpath(lp, cwd)
            except Exception:
                pass
            print(f"INFO: Jinja cache stats ENABLED; writing to {rel}")
            # Also write a startup marker to create the file and verify write access
            try:
                with open(lp, 'a', encoding='utf-8') as fh:
                    fh.write(f"{_now_iso()} startup enabled pid={os.getpid()}\n")
            except Exception as e:
                print(f"WARNING: Failed to write jinja stats file at {rel}: {e}")
        except Exception:
            pass
    except Exception:
        # Fail closed; do not disrupt app startup
        pass
