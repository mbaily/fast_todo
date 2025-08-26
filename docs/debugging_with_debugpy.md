Debugging the FastAPI server with debugpy

This project supports starting a debugpy listener when the server process starts so you can attach a remote debugger (VS Code or other). The listener is enabled via environment variables.

Quick setup

1. Install debugpy into your virtualenv:

   pip install debugpy

2. Start the server with the env var to enable debugpy. Example (development, no SSL):

```bash
# enable debugpy, do not wait for attach, listen on port 5678
ENABLE_DEBUGPY=1 DEBUGPY_PORT=5678 DEBUGPY_WAIT=0 \ 
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info --workers 1
```

If you prefer the server to pause until a debugger attaches (useful when you want to hit breakpoints during startup):

```bash
ENABLE_DEBUGPY=1 DEBUGPY_PORT=5678 DEBUGPY_WAIT=1 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level info --workers 1
```

Notes and recommendations

- The code only imports and starts debugpy when `ENABLE_DEBUGPY` is truthy (`1`, `true`, `yes`).
- When `DEBUGPY_WAIT=1`, the process will block at startup until a debugger attaches; this is helpful to debug startup/lifespan code.
- For local VS Code attach use a launch configuration like:

```json
{
  "name": "Attach to FastAPI (debugpy)",
  "type": "python",
  "request": "attach",
  "connect": { "host": "localhost", "port": 5678 },
  "pathMappings": [{ "localRoot": "${workspaceFolder}", "remoteRoot": "." }]
}
```

- If you run uvicorn with multiple workers (`--workers N`), debugpy will start in each worker process. Attach to the worker process you want to debug, or run with `--workers 1` for single-process debugging.

- Avoid enabling debugpy in production environments.
