"""Optional cProfile instrumentation for the FastAPI server.

Enable via environment variables (all optional; disabled by default):
- PROFILE_REQUESTS=1  -> profile each HTTP request and write a .prof file
- PROFILE_GLOBAL=1    -> profile the entire app lifetime and write a single .prof
- PROFILE_DIR=profiles -> base directory to store profile outputs (default 'profiles')

Per-request middleware also writes a brief top-N summary alongside the .prof.
"""
from __future__ import annotations

import os
import re
import io
import time
import atexit
import cProfile
import pstats
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _sanitize_path(path: str) -> str:
    if not path:
        return 'root'
    # collapse slashes, remove leading slash, replace non-word chars with '_'
    s = re.sub(r"/+", "/", path).lstrip('/')
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", s)
    return s or 'root'


class RequestProfilerMiddleware(BaseHTTPMiddleware):
    """Profiles each HTTP request and saves .prof files under PROFILE_DIR/requests.

    Adds response headers:
      - X-Profile-Time-ms
      - X-Profile-File
    """

    def __init__(self, app, out_dir: str):
        super().__init__(app)
        self.out_dir = out_dir
        _ensure_dir(self.out_dir)

    async def dispatch(self, request: Request, call_next):
        import sys
        another_active = sys.getprofile() is not None
        pr = None
        t0 = time.perf_counter()
        if not another_active:
            pr = cProfile.Profile()
            try:
                pr.enable()
            except ValueError:
                # Another profiler got enabled between checks; treat as active
                pr = None
                another_active = True
        try:
            response = await call_next(request)
        finally:
            if pr is not None:
                try:
                    pr.disable()
                except Exception:
                    pass
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Build file paths
        ts = _now_ts()
        method = (request.method or 'GET').upper()
        path = _sanitize_path(request.url.path)
        pid = os.getpid()
        base = f"{ts}_{method}_{path}_{pid}"
        prof_path = os.path.join(self.out_dir, base + ".prof")
        txt_path = os.path.join(self.out_dir, base + ".txt")

        if pr is not None:
            try:
                pr.dump_stats(prof_path)
                # Also write a short top summary for quick inspection
                s = io.StringIO()
                ps = pstats.Stats(pr, stream=s).strip_dirs().sort_stats('cumulative')
                ps.print_stats(40)
                with open(txt_path, 'w', encoding='utf-8') as fh:
                    fh.write(f"Elapsed: {elapsed_ms:.2f} ms\n\n")
                    fh.write(s.getvalue())
            except Exception:
                # Do not fail requests on profiling issues.
                pass

        try:
            # Attach headers for discovery
            response.headers['X-Profile-Time-ms'] = f"{elapsed_ms:.2f}"
            if pr is not None:
                response.headers['X-Profile-File'] = os.path.relpath(prof_path, os.getcwd())
            else:
                response.headers['X-Profile-Skipped'] = 'active_profiler'
        except Exception:
            pass
        return response


_GLOBAL_PROFILER: Optional[cProfile.Profile] = None
_GLOBAL_OUTDIR: Optional[str] = None


def _start_global_profiler(out_dir: str) -> None:
    global _GLOBAL_PROFILER, _GLOBAL_OUTDIR
    if _GLOBAL_PROFILER is not None:
        return
    _ensure_dir(out_dir)
    _GLOBAL_OUTDIR = out_dir
    try:
        import sys
        if sys.getprofile() is not None:
            # Another profiler already active; skip starting global profiler
            return
        pr = cProfile.Profile()
        pr.enable()
        _GLOBAL_PROFILER = pr
    except ValueError:
        # Another profiler is already active; skip
        return

    def _dump_on_exit():
        try:
            stop_global_profiler()
        except Exception:
            pass

    # Best-effort dump on process exit
    try:
        atexit.register(_dump_on_exit)
    except Exception:
        pass


def stop_global_profiler() -> str | None:
    """Stop the global profiler and write output to a .prof and .txt summary.

    Returns the .prof file path if written.
    """
    global _GLOBAL_PROFILER, _GLOBAL_OUTDIR
    if _GLOBAL_PROFILER is None:
        return None
    pr = _GLOBAL_PROFILER
    _GLOBAL_PROFILER = None
    pr.disable()

    out_dir = _GLOBAL_OUTDIR or os.path.join(os.getcwd(), 'profiles', 'global')
    _ensure_dir(out_dir)
    ts = _now_ts()
    pid = os.getpid()
    base = f"global_{ts}_{pid}"
    prof_path = os.path.join(out_dir, base + ".prof")
    txt_path = os.path.join(out_dir, base + ".txt")
    try:
        pr.dump_stats(prof_path)
        s = io.StringIO()
        pstats.Stats(pr, stream=s).strip_dirs().sort_stats('cumulative').print_stats(80)
        with open(txt_path, 'w', encoding='utf-8') as fh:
            fh.write(s.getvalue())
    except Exception:
        pass
    return prof_path


def install_profiler(app: FastAPI) -> None:
    """Conditionally install request/global profilers based on env vars.

    - PROFILE_DIR sets the base directory (default 'profiles')
    - PROFILE_REQUESTS=1 enables per-request profiling
    - PROFILE_GLOBAL=1 enables global lifetime profiling
    """
    try:
        base_dir = os.getenv('PROFILE_DIR', 'profiles')
        req_flag = str(os.getenv('PROFILE_REQUESTS', '0')).lower() in ('1', 'true', 'yes', 'on')
        glob_flag = str(os.getenv('PROFILE_GLOBAL', '0')).lower() in ('1', 'true', 'yes', 'on')

        # If both flags are set, prefer per-request (global profiling would
        # conflict with per-request cProfile under CPython).
        if req_flag and glob_flag:
            glob_flag = False

        if req_flag:
            out_dir = os.path.join(base_dir, 'requests')
            app.add_middleware(RequestProfilerMiddleware, out_dir=out_dir)

        if glob_flag:
            out_dir = os.path.join(base_dir, 'global')
            # start at import time (app startup) and stop at shutdown
            _start_global_profiler(out_dir)

            @app.on_event('shutdown')
            async def _stop_profiler_event():
                stop_global_profiler()
    except Exception:
        # Never break app startup due to profiling hooks
        pass
