# Fast Todo Server

## License

This project is licensed under the GNU General Public License v3.0 — see the included `LICENSE` file for details.

Copyright

Copyright (c) 2025 Mark Baily

## Purpose

This app is for quick notes, tasks with priorities, and task management, and also small to medium size notes. You can use it like a filofax or to avoid using portable paper notepads when you own a smartphone. You can store lists of notes or todos recursively (infinite depth).

You can use it instead of other apps where you have to position the cursor on a smartphone at the end of your note text to add a new todo item to the list, which may be difficult or slow when out and about. Or it might be quicker to use for some tasks (with numeric priorities and hashtags support), even on your PC.

I use it on client windows and linux PCs (with Google Chrome), and my ipad and iphone.

If you need to write or store more extensive documentation, I recommend dokuwiki (open-source).


## Quick server usage (Windows and Debian)

This project can run either as a local app on Windows or as a server on a Linux machine (for example a mini PC running Debian).

- Windows
  - Use the included PowerShell helper: `scripts/run_server_dev_windows.ps1`.
  - The script creates a Python virtual environment (by default in `.venv`), installs required packages from `requirements.txt` (or a sensible fallback), generates or loads a `SECRET_KEY`, optionally generates a self-signed certificate, and starts the server (uvicorn) on HTTPS.
  - The script will also create a repository env file named `fast_todo.env` containing a generated `SECRET_KEY` when one is not already present. That file is used for the JWT access token signing key.

  - Here are the steps in windows
```
Windows steps from windows powershell prompt

git clone xxxxxxxxxxxx - find out url
cd fast_todo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python .\scripts\add_user.py your_username
.\scripts\run_server_dev_windows.ps1

browse to https://localhost:10443/html_no_js

to change the password
python .\scripts\change_user_password.py --username mbaily
```


- Debian / Linux
  - Follow similar steps to windows just above. git clone, cd to dir, create venv in .venv, activate venv, pip install -r requirements.txt, run add_user.py, run ./scripts/run_server_dev_debian.sh
  - Use the shell helper: `scripts/run_server_dev_debian.sh` (or `scripts/run_server_debian.sh` where present for production-style runs).
  - The script will create a `.venv` virtualenv, install packages with `pip`, generate/load `SECRET_KEY`, optionally produce a self-signed certificate, and run the server using `uvicorn` (dev reload or production mode depending on flags).
  - For a permanent public-facing server, install on a small Linux box and expose it via your router (DMZ or IP forwarding). Access the app from any browser (tested with iOS Safari and Android Chrome, plus desktop Chrome).

Notes
  - Both scripts default to HTTPS using a self-signed certificate (useful for local/home deployments). For production, use a valid CA-signed certificate.
  - Scripts detected in the repository: `scripts/run_server_dev_windows.ps1` and `scripts/run_server_dev_debian.sh`.

## Self-signed certificates

For local testing and small home deployments the helper scripts can generate a self-signed certificate and key. This allows the server to serve HTTPS so browsers will use secure features and service workers.

- What the scripts do
  - They generate a cert/key pair and place it in a certificate directory used by the server launch.
  - Browsers will show a security warning because the certificate is not from a trusted CA. You can accept the warning in your browser for the local host or add the certificate to your OS/browser trust store if you prefer.

- Production recommendation
  - Use a CA-signed certificate (Let's Encrypt or similar) for public servers. Replace the generated cert/key with the CA files and point the service to those paths.

## Debian ./scripts/deploy.sh

The `scripts/deploy.sh` helper is intended to perform a simple install on a Debian server. It is typically run as root (or via `sudo`).

- deploy.sh will copy or install the project into `/opt/fast_todo` (matching the example systemd unit),
- you now need to cd to /opt/fast_todo and create a python venv using the command 'python -m venv .venv'
- deploy.sh will create an environment file for services (for example `/etc/default/fast_todo` or similar) containing `SECRET_KEY` and other runtime vars,
- deploy.sh will generate self-signed certificates under `/opt/fast_todo/.certs` when needed,
- deploy.sh install and enable a systemd service unit so the app runs as a managed service and starts on boot.

Assumption: this description matches the repository's systemd example (WorkingDirectory `/opt/fast_todo`, `EnvironmentFile=/etc/default/fast_todo`, and `.certs` under `/opt/fast_todo`). Adjust paths if your deploy script writes elsewhere.

## Example systemd service file - taken from my server, change to suit your needs
```
testuser@server:/etc/systemd/system/multi-user.target.wants$ cat fast_todo.service
[Unit]
Description=Fast Todo FastAPI service
After=network.target

[Service]
# The user which will run the uvicorn process. Default is 'www-data'.
User=www-data
Group=www-data

# Path where the app lives
WorkingDirectory=/opt/fast_todo

# Optional environment file (created by deploy script)
# The installer will write /etc/default/fast_todo
EnvironmentFile=/etc/default/fast_todo
Environment="PYTHONPATH=/opt/fast_todo"

# ExecStart runs uvicorn from the virtualenv. Adjust venv path if needed.
ExecStart=/opt/fast_todo/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 10443 \
  --ssl-keyfile /opt/fast_todo/.certs/privkey.pem --ssl-certfile /opt/fast_todo/.certs/fullchain.pem

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## SECRET_KEY and environment configuration

The app uses a `SECRET_KEY` to sign JWT access tokens. Keep this key private and persistent between server restarts unless you intentionally want to invalidate sessions.

- Windows (development/local)
  - The PowerShell startup script will create `fast_todo.env` in the project root and write a generated `SECRET_KEY` into it if one is not found. That file will be loaded for the running process when you start the server via the script.
  - Example: `fast_todo.env` will contain a line like `SECRET_KEY=...`.

- Debian / Linux (server)
  - For a durable server place the secret in a system location that your service can read. A common pattern is a file under `/etc/` (for example `/etc/fast_todo/env`) or as an environment variable in your systemd service unit. The scripts accept an externally-provided `SECRET_KEY` (e.g. `SECRET_KEY=yourkey scripts/run_server_debian.sh`).
  - Rotating the `SECRET_KEY` will invalidate previously issued JWTs. Users may need to log out and log back in after rotation.

Security notes
  - Do not commit `fast_todo.env` or any file containing `SECRET_KEY` to version control. Treat it like a secret.

## Hashtags in names and todos

You can type hashtags (`#tag`) directly into list names or todo item names. The client will extract those hashtags and add them to the todo metadata automatically. This makes tagging quick — type a tag into the title and it will be recorded separately while the visible text remains usable.

Behavior details
  - Hashtags can be typed anywhere in the name. They are parsed and added to the todo/list tags, and removed from the name.
  - The visible title will not retain the hashtags you typed; tags are stored as structured metadata for searches and filters.
  - You can also put hashtags in the note text of a todo. These hashtags are retained in the note text.

## Multi-tag search markup (in-note clickable links)

You can create quick multi-hashtag searches by typing a small inline markup into a todo's note text. The client recognizes the pattern and renders it as a clickable link that runs a search combining multiple tags.

This is useful for shopping, for example. You can make hashtags for each shop you go to then list them all at once so you know which shops to go to while you are out.

Example multi-tag search (put in todo note):

```
Supermarket {{fn:search.multi tags=#supermarket,#coles,#woolworths,#aldi | Supermarket }}

Your regular shops {{fn:search.multi tags=#bunnings,#mitre10,#officeworks,#kmart,#target,#bigw,#myer | Shops }}

Your houses or work places {{fn:search.multi tags=#wilsonst,#edwardst | Houses or Places }}

Your Suburbs {{fn:search.multi tags=#newyork,#london,#tokyo,#nottinghill | Suburbs }}
```


## Scripts: run server and create venv

Both the Windows and Debian scripts perform these steps automatically:
  - Create a virtual environment in `.venv` (if not present).
  - Install Python packages using `pip` from `requirements.txt` (or documentation fallback files if the requirements file looks like docs).
  - Load or generate `SECRET_KEY`.
  - Optionally generate a self-signed TLS certificate for HTTPS.
  - Launch the server (uvicorn) on the configured host/port.

Files to inspect
  - `scripts/run_server_dev_windows.ps1`
  - `scripts/run_server_dev_debian.sh`

These scripts are intended to make local setup quick. For production you should wire the same steps into a service manager (systemd).

## scripts/add_user (admin user creation)

There is a command-line helper for adding a user: `scripts/add_user.py`.

Usage
  - Activate your venv in .venv 
    - Linux: source .venv/bin/activate
    - Windows:  .\.venv\Scripts\Activate.ps1
  - Run from the project root: `python scripts/add_user.py username password [--admin]`.
  - This is a local, manual administrative action. It has been tested on Debian 13 and on local Windows environments where Python and the project PYTHONPATH are configured.

Notes
  - Ensure the script can import the project package (running from the project root or setting PYTHONPATH appropriately). The `scripts/` folder contains the helper script but the project root needs to be on Python's import path.

## Change user password scripts

There are two helper scripts under `scripts/` for updating a user's password:

- `scripts/change_password.py`
  - Non-interactive: requires `--username` and `--password` on the command line.
  - Accepts `--db` (defaults to `./fast_do.db`). If you pass a path it will be converted into a `sqlite+aiosqlite:///` URL and set as `DATABASE_URL` for the script.
  - Intended for automation or scripted workflows where you already have the new password available.
  - Example (non-interactive):

```powershell
python scripts/change_password.py --db ./fast_todo.db --username alice --password 'newpass'
```

- `scripts/change_user_password.py`
  - Interactive and flexible: if `--password` is omitted the script prompts for the new password twice (confirmation).
  - Accepts a full SQLAlchemy URL (e.g. `postgres://...` or `sqlite+aiosqlite:///./fast_todo.db`) via `--db`, so it works for non-sqlite deployments as well.
  - Recommended for manual administrative use because of the prompt/confirm flow and broader DB support.
  - Example (interactive):

```powershell
python scripts/change_user_password.py --username alice
# prompts for new password and confirmation
```

Example (non-interactive with explicit DB URL):

```powershell
python scripts/change_user_password.py --db sqlite+aiosqlite:///./fast_todo.db --username alice --password 'newpass'
```

Recommendation

- For manual, interactive password changes use `scripts/change_user_password.py` (safer: prompts and supports full DB URLs).
- For automation (CI, deploy scripts, bulk updates) use `scripts/change_password.py` because it's explicitly non-interactive and meant for scripted invocation.

Notes common to both

- Both scripts reuse the app's password hashing (`pwd_context`) so hashes are compatible with the running app.
- Run them from the project root (or ensure the project root is on `PYTHONPATH`) so the scripts can import `app` modules.
- Both set `DATABASE_URL` in the environment for the script run; you can also export `DATABASE_URL` yourself before running if you prefer.

## Auto-save behavior in the UI

Auto-save applies to the editable text fields for todo items and notes while editing a todo. Key points:
  - The text is auto-saved as you edit, preventing accidental loss of typed content. Default: 1 second.
  - Auto-save does not automatically add a new todo item — it only persists edits to an existing item or note. To create a new todo you still need to explicitly add it.

## Recursive sublists and nested todos

The data model supports nested structures:
  - A list can contain sublists. Those sublists can themselves contain todos and further sublists (recursive nesting).
  - A todo can be the child of a list or a sublist. This allows representing tasks grouped into nested categories and subtasks.

If you mainly use a particular todo or list for a notes storage folder, you can use the "Up Top" option for sublists. Then the sublists of the todo or list will be nearer the top of the page for convenience.

## Priority numbers and colored indicators

Priorities are represented numerically in the index UI and also with colored numbers (red) where appropriate.

  - Index view: priority numbers are shown beside todos to indicate their urgency or order. If a list has an uncompleted todo in it, which has a higher priority, this is also shown beside the list in the index view.
  - Red numbers: The number of uncompleted todos under a list is shown in red-colored numbers in the index.

Behavior
  - Lower/higher numeric values of 1 to 10 correspond to different priority meanings (higher number is higher priority).

## Hide icons option

This is for using a todo list a bit more like a notes app, or you can't accidentally delete todos. Completed todos in this view: you can't completed a todo in the view but the completed todos are shown with a green checkbox. You can flip the view as you please.

## Trashcan

Deleted items will go into the trashcan. From there you can recover them or permanently delete them.

## Calendar and recurring occurrences

The app extracts calendar occurrences from todo/list text by parsing dates and recurrence descriptions found in the title and note text. Detected occurrences are exposed via the calendar endpoints (for example `/calendar/occurrences`) so the UI and external tools can render a calendar view.

How occurrences are extracted
  - The server scans todo text and list titles for date-like text and recurrence hints. It builds a normalized date/dtstart and (when present) an RRULE-like recurrence string and stores recurrence metadata on the todo/list record.
  - Recurrence metadata is stored in the database with fields such as `recurrence_rrule`, `recurrence_meta`, and `recurrence_dtstart` (see tools/migrate_add_recurrence.sh and tools/backfill_recurrence.py for details).
  - A separate parsing heuristic attempts to resolve ambiguous dates (yearless dates, windows, and created-at fallback) so occurrences fall into the expected calendar window.

Recurring occurrences behavior
  - When a todo has recurrence metadata the calendar endpoint will expand that recurrence into concrete occurrence instances within the requested date window. The expansion respects the stored `dtstart` and rrule-like definition.
  - Recurring lists (a list that itself has a recurrence rule) generate occurrences representing the list instance dates and can be used to pre-populate list contents on those dates.
  - The UI and API return occurrence objects that include the `occurrence_dt`, item type (`todo` or `list`), the source item id, and any display metadata required by the client.

Ignore and completion controls
  - Ignore completely: mark an item or list as ignored for calendar generation. Ignored items do not produce calendar occurrences and will be excluded from calendar queries.
  - Ignore from date: set an ignore-from date so occurrences before (or after, depending on semantics) a cutoff are suppressed. This is useful when you want to stop showing older occurrences without deleting recurrence metadata.
  - Task complete: completing a recurring todo typically only affects a single occurrence instance. The recurrence metadata remains so subsequent occurrences still appear in the calendar unless the item is explicitly marked to be ignored.

Notes and tips
  - When creating recurring todos, prefer explicit recurrence phrasing (e.g. "every Monday" or RFC-style rrules) to improve parsing accuracy.
  - If you see unexpected occurrences, check the todo's `recurrence_meta` and `recurrence_dtstart` fields (via the API or DB) to understand how the parser interpreted the text.

## Completion types in list view

The list view supports one or more "completion types" in addition to the built-in default completion state. Completion types are intended as lightweight, per-list markers (for example: "Done", "Reviewed", "QA", "Deployed") that you can mark on individual todos.

Key behavior
- Normal todo view (icons not hidden) view:
  - The list is rendered as a table with one column for the default completion control and one additional column for each extra completion type defined on the list (ordered by creation time).
  - Each extra completion type is shown as a small checkbox-like control in its own column. You can click the control to mark or unmark that completion type for the todo.
  - A header row is shown when there are extra completion types so the column meaning is visible.

- Hide-icons view:
  - The list switches to a compact, linear layout (no per-row icon columns). In this mode the extra completion types are not shown as separate columns. Instead the row keeps a compact indicator (a green check glyph for completed items) and the todo text flows inline with fewer controls.
  - The hide-icons mode is useful when you prefer a notes-like layout or want fewer interactive controls on the page.

Important notes about semantics
- Completion types are reference-only metadata for each todo. They do not change how priorities, recurrence, or other server-side logic behaves.
- Marking a completion type does not re-order or re-prioritize todos automatically. Priority numbers and the app's priority-derived behaviors are independent of completion type flags.
- Completion types are stored per-list and ordered by creation time; when extra completion types are shown in the table they follow that creation order so their columns are stable and predictable.

If you want to change how completion types behave (for example, to make them affect ordering or filter results), that would need a server-side behavior change and is not the current behavior.

## Final notes and references

This README section documents the common workflows for local and small-server deployments. For production, replace self-signed certs with CA-signed certs, secure `SECRET_KEY` storage, and run the server under a service manager (systemd) with proper logging and rotation.

Files and scripts referenced in this README (located in the repository):
  - `scripts/run_server_dev_windows.ps1` — Windows helper script (creates .venv, installs, runs server).
  - `scripts/run_server_dev_debian.sh` — Debian/Linux helper script (creates .venv, installs, runs server).
  - `scripts/add_user.py` — CLI helper to add users from the server machine.

