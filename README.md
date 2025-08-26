# Fast Todo Server

This repository contains a FastAPI-based todo/notes server backed by SQLite.

Quick notes:

- The server uses JWT-based authentication. Set a strong `SECRET_KEY` in
  production via the `SECRET_KEY` environment variable.

  Example (Linux/macOS):

  ```bash
  export SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
  ```

- For local testing the code falls back to a non-secret default value. Do not
  use that in production.

Running tests

- Install project dev dependencies into a virtualenv and run pytest:

  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements_server.txt
  python -m pytest -q
  ```

Starting the server (development)

- Start with uvicorn:

  ```bash
  export SECRET_KEY=your_real_secret_here
  uvicorn app.main:app --reload
  ```

## License

This project is licensed under the GNU General Public License v3.0 — see the included `LICENSE` file for details.

Copyright

Copyright (c) 2025 Mark Baily

