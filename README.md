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


## Server and Client

The server is Python + FastAPI.

The main web client as of 2025-09-12 is called html_no_js. The other clients are prototypes or skeletons.
The name html_no_js is now a misnomer as there is some javascript used to update the DOM, and other things.


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

The app uses a `SECRET_KEY` to sign JWT access tokens and CSRF tokens. Keep this key private and persistent between server restarts unless you intentionally want to invalidate sessions.

- Windows (development/local)
  - The PowerShell startup script will create `fast_todo.env` in the project root and write a generated `SECRET_KEY` into it if one is not found. That file will be loaded for the running process when you start the server via the script.
  - Example: `fast_todo.env` will contain a line like `SECRET_KEY=...`.

- Debian / Linux (server)
  - For a durable server place the secret in a system location that your service can read. A common pattern is a file under `/etc/` (for example `/etc/fast_todo/env`) or as an environment variable in your systemd service unit. The scripts accept an externally-provided `SECRET_KEY` (e.g. `SECRET_KEY=yourkey scripts/run_server_debian.sh`).
  - Rotating the `SECRET_KEY` will invalidate previously issued JWTs. Users may need to log out and log back in after rotation.

CSRF tokens across restarts or rotations

- To avoid CSRF failures immediately after a restart where `SECRET_KEY` changed (common in development if scripts generate a new key each run), the server can be configured to accept CSRF tokens signed with one or more previous secrets for verification only.
- Set `CSRF_VERIFY_KEYS` to a comma-separated list of previous secrets. New tokens are always signed with `SECRET_KEY`; the entries in `CSRF_VERIFY_KEYS` are used only to verify existing CSRF tokens until they expire.

Examples

```bash
# systemd EnvironmentFile=/etc/default/fast_todo
SECRET_KEY=your-current-production-secret
CSRF_VERIFY_KEYS=prior-secret-1,prior-secret-2
```

## Date parsing / locale

The server contains heuristics to extract dates and recurrence phrases from
freeform todo titles and notes. Two configuration points control how numeric
dates and synthesized datetimes are interpreted:

- `DATE_ORDER` (env / `app.config.DATE_ORDER`): controls numeric ordering for
  ambiguous numeric dates such as `5/9` or `12/9/25`. Valid values are
  `DMY` (day-month-year) or `MDY` (month-day-year). The default is `DMY`
  (Australian-style). You can override it with the environment variable
  `DATE_ORDER=MDY`.

- `DEFAULT_TIMEZONE` (env / `app.config.DEFAULT_TIMEZONE`): the IANA timezone
  name the server should use for formatting or synthesizing local datetimes
  when needed. Default: `Australia/Melbourne`.

Examples (systemd / env file):

```bash
# Australian day/month ordering and Melbourne local timezone
DATE_ORDER=DMY
DEFAULT_TIMEZONE=Australia/Melbourne

# US style month/day ordering
# DATE_ORDER=MDY
```

Notes
- The parser uses targeted numeric heuristics: if a numeric triplet includes a
  4-digit year it is interpreted as `YYYY/MM/DD`. Otherwise the configured
  `DATE_ORDER` is used for disambiguation. See `app/utils.py` around the
  `_explicit_date_substrings` logic for the exact heuristics.
- `extract_dates_meta` will mark whether a match included an explicit 4-digit
  year (`year_explicit`) so callers can resolve yearless matches against a
  calendar window or creation time.

Notes
- This helps browsers continue to POST successfully after a controlled key rotation or dev restart without forcing an immediate logout/login, as long as the CSRF token hasn’t expired.
- For production, prefer a persistent `SECRET_KEY` managed outside the repo (EnvironmentFile, vault, etc.). The fallback list is optional and meant to smooth planned rotations.

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


## Hide a "search page" todo from search results (search_ignored)

If you keep special todos whose note contains many multi-tag search links (a "hub" page), those notes can match normal searches and clutter results. You can mark such a todo to be ignored by search.

- What it does
  - Sets a boolean flag `search_ignored` on a todo. The HTML search (`/html_no_js/search`) and JSON client search both exclude todos where `search_ignored` is true, across all search branches (text/note match, hashtag match, and include-list-todos expansion).
  - The todo still appears in its list views and APIs that aren’t search-specific.

- When to use it
  - For navigation-style todos used as link hubs with markup like `{{fn:search.multi ...}}` containing many tags.
  - For index/reference pages that shouldn’t appear as regular search hits.

- Toggle via CLI

  Activate your virtualenv and run from the project root:

  ```bash
  # Toggle the flag (true <-> false)
  python -m scripts.toggle_search_ignore 327

  # Explicitly set true or false
  python -m scripts.toggle_search_ignore 327 --set true
  python -m scripts.toggle_search_ignore 327 --set false
  ```

  The script prints the previous and new values and a short summary of the todo.

- Verify from bash (SQLite one-liners)

  ```bash
  # Show id and flag
  sqlite3 -header -column fast_todo.db "SELECT id, search_ignored FROM todo WHERE id=327;"

  # Show flag only
  sqlite3 -noheader -batch fast_todo.db "SELECT search_ignored FROM todo WHERE id=327;"
  ```

- Notes
  - After changing the flag, just refresh the search page. No server restart is needed.
  - If a flagged todo still appears, confirm the DB value is `1` (true) and that you’re using the updated server code where the HTML search route also applies the filter.


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

## Calculate button (CalcDict) on the todo page

You can run quick calculations from a todo using the Calculate button. It evaluates the note text using a tiny RPN-style calculator with variables and shows the result below the note.

Where to find it
- On a todo page, the Calculate button appears next to the “DokuWiki page for this todo” link (or by itself if DokuWiki isn’t configured).
- The output appears in a read-only “Calculation Output” box under the note.

How it works
- It reads the entire Note field and treats it as a set of line-based assignments.
- Each non-empty, non-comment line has the form: name expression
  - name is a variable identifier (letters/numbers/underscore, starting with a letter or underscore).
  - expression is a space-separated Reverse Polish Notation (RPN) expression.
- Variables within expressions are referenced as $name.
- Comments start with # and run to the end of the line.
- Blank lines are ignored.

Supported tokens (selection)
- Numbers: 1, 2.5, etc.
- Basic ops (binary): +  -  *  /
- N-ary ops (reduce the whole stack): n+  n-  n*  n/
- Constants: pi  e
- Trig: sin  cos  tan   (arguments are in radians)
- Inverse trig: asin  acos  atan
- Logs: log (base 10), ln (natural)
- Powers/roots: pow (a^b), sqrt
- Other: abs  round  swap (swap top two stack values)

What the output shows
- A header with the todo name (e.g. todo-123).
- For each assigned variable: name: value = sum
  - If a value is a single number, value and sum are the same.
  - If a value is a list (rare; only if you finish with more than one stack value), sum is the numeric sum of that list.
- A final Total that sums all variable sums.

Quick examples (paste into the todo note and click Calculate)

1) Budget roll-up and tax

```
# Line-based assignments: name then RPN expression
groceries 12.50 8.20 5.30 n+
fuel 60
subtotal $groceries $fuel +
tax $subtotal 0.10 *
total $subtotal $tax +
```

2) Geometry with constants

```
r 3
area $r $r * pi *
circumference 2 pi * $r *
```

3) Logs, powers, roots

```
a 100 ln
b 10 log
pow_ex 2 8 pow
root 81 sqrt
```

4) Stack helpers and n-ary arithmetic

```
series 1 2 3 4 5 n+
diff 100 30 10 n-
swap_demo 2 3 swap -   # (3 2 then 3-2 = 1)
```

Usage tips
- Radians: trig functions use radians.
- Variables: reference earlier results with $varname.
- Comments: start a comment with # anywhere on the line.
- Errors: unknown tokens, division by zero, or missing variables ($name not defined) will cause an error message instead of results.
- Idempotent: calculations don’t modify your note; they only read it.

Privacy/logging
- The server executes the calculation and returns only the output; it does not persist calculation results.
- For troubleshooting, the server may log calculation input and output at INFO level. If you don’t want that in production logs, reduce the log level or disable those log lines.

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



## Inline link markup (fn:link)

You can insert inline links in a todo’s note that navigate to other todos or lists by database ID. These render as normal anchors (you can middle/Ctrl-click) and, by default, show the target’s title and circled priority when present.

- Purpose: cross-link between todos/lists directly from note text.
- Works in: the no‑JS HTML UI (notes are rendered server-side).

Basic syntax (preferred)
- Todo by id: `{{fn:link target=todo:123}}`
- List by id: `{{fn:link target=list:45}}`

Alternate forms (all equivalent)
- Separate keys: `{{fn:link type=todo,id=123}}`
- Shorthand keys: `{{fn:link todo=123}}` or `{{fn:link list=45}}`

Custom label
- Use a trailing pipe to supply link text:
  - `{{fn:link target=todo:123 | Next Action}}`
  - `{{fn:link list=45 | Someday/Maybe}}`

Priority display
- By default, if the target has a priority (1–10), the rendered link appends the usual circled number ①..⑩ in the same large style used elsewhere.
- Suppress the priority using any of the following (choose one):
  - `show_priority=false`
  - `priority=false`
  - `no_priority` (flag)
  - `nopriority` (flag)

Examples
- Auto label + priority: `{{fn:link target=todo:77}}` → “<todo title> ③”
- Custom label + priority: `{{fn:link target=todo:77 | Next Action}}` → “Next Action ③”
- Suppress priority: `{{fn:link target=list:9, show_priority=false}}` → “<list name>”

Label resolution
- If you don’t provide a custom label, the server resolves the label to the target’s actual name:
  - Todo → its `text`
  - List → its `name`
- If the title can’t be loaded (rare), a safe fallback is used: “Todo #id” or “List #id”.

Navigation behavior
- Rendered links are plain anchors with no special interception, so middle/Ctrl‑click opens in a new tab as expected.

Note insertion helper (optional)
- On the todo page, a small “Insert link” combobox next to the Note uses your recently marked items to insert link markup for you.

## Collation lists (grouping todos across lists)
  
You can maintain multiple personal “collation” lists and quickly mark whether a todo belongs to any of them.

- A collation is just a regular list you own, registered in your set of collations.
- Collations can be active or inactive. Only active collations show a small toggle on each todo page so you can add/remove the todo to/from that collation with one click.
- Membership is stored via ItemLink edges: src_type='list' (the collation list) → tgt_type='todo'.

JSON endpoints (session auth):

- GET /client/json/collations → { ok, collations: [{list_id, name, active}] }
- POST /client/json/collations { list_id, active? } → register/update a collation for the user
- POST /client/json/collations/{list_id}/active { active } → set active flag
- GET /client/json/collations/status?todo_id=123 → { ok, memberships: [{list_id, name, linked}] }
- POST /client/json/collations/{list_id}/toggle { todo_id, link? } → toggles or forces membership; returns { ok, linked }

UI behavior:

- On `todo.html`, active collations render buttons like “+ My Focus” / “✓ My Focus”. Clicking toggles membership via the JSON API.
- To create a new collation list programmatically: POST /client/json/lists { name } then POST /client/json/collations { list_id }.
  - It first shows placeholders like “Todo #123” / “List #45”, then enriches to “123 — <title snippet>”.
  - When you click “Insert Link”, it inserts the correct `{{fn:link target=...}}` markup at the cursor in the note.
  - Mark items using the 🔖 button on list/todo pages; marks expire after a few minutes.

Tips
- You can put multiple fn:link items on separate lines (or inline) to build a small hub note.
- For consistent titles across a note, prefer not mixing custom labels with auto‑labels unless you need a specific phrasing.

### External URL link markup (fn:url)

Use this to add an external hyperlink in a todo’s note. Renders as a plain anchor (middle/Ctrl‑click works) and opens in a new tab by default.

Accepted forms
- `{{fn:url href=https://example.com}}`
- `{{fn:url url=https://example.com}}`
- `{{fn:url https://example.com}}` (positional)
- `{{fn:url example.com}}` or `{{fn:url www.example.com}}` → scheme auto‑prepended to `http://` for convenience

Custom label
- `{{fn:url href=https://example.com | Example Site}}`

Options
- `target=_blank` by default (override with `target=_self`, etc.).
- `rel="noopener noreferrer"` by default (extend with `nofollow` via `nofollow=true` or a `nofollow` flag).

Examples
- `{{fn:url https://news.ycombinator.com}}`
- `{{fn:url href=https://example.com | Example}}`
- `{{fn:url www.example.com nofollow}}`

## Item links (non‑markup links between todos and lists)

Apart from inline note markup, you can add persistent links between items (todo→todo, todo→list, list→todo, list→list). These links live on the item and appear in the UI.

Where links appear
- Compact: a “Links:” row near the top of todo and list pages (comma‑separated anchors).
- Full list: a “Links” section lower on the page with add/remove controls.

How to add a link
1) Mark the target item using the 🔖 button on a todo or list page (marks expire after a few minutes).
2) On the source item’s page, in the “Links” section:
  - Choose the marked target from the “Marked” dropdown.
  - Optionally enter a custom label.
  - Click “Add link”.

Notes
- Labels: If you don’t provide a label, the UI shows the target’s title. You can edit/remove the link later.
- Navigation: These render as plain anchors, so middle/Ctrl‑click opens in a new tab.
- Ownership: You can only link items you own; the server checks ownership on add/remove.
- Storage: Links are stored in the database table `itemlink` with a uniqueness constraint on (src_type, src_id, tgt_type, tgt_id). A `position` field is reserved for future ordering.

Related helpers
- Marked items are kept client‑side in localStorage (`ft_marks_v1`) with a short TTL so the “Marked” dropdowns stay relevant and fast.
- The “Insert link” combobox next to a todo’s Note is separate; it inserts note markup `{{fn:link ...}}` and isn’t the same as persistent item links above.



