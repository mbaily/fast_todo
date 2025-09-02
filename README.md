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
  - The script will also create a repository env file named `gpt5_fast_todo.env` containing a generated `SECRET_KEY` when one is not already present. That file is used for the JWT access token signing key.

- Debian / Linux
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

The `scripts/deploy.sh` helper is intended to perform a simple install on a Debian server. It is typically run as root (or via `sudo`) and will:

- copy or install the project into `/opt/gpt5_fast_todo` (matching the example systemd unit),
- create a Python virtual environment under `/opt/gpt5_fast_todo/.venv` and install required packages,
- create an environment file for services (for example `/etc/default/gpt5_fast_todo` or similar) containing `SECRET_KEY` and other runtime vars,
- generate self-signed certificates under `/opt/gpt5_fast_todo/.certs` when needed,
- install and enable a systemd service unit so the app runs as a managed service and starts on boot.

Assumption: this description matches the repository's systemd example (WorkingDirectory `/opt/gpt5_fast_todo`, `EnvironmentFile=/etc/default/gpt5_fast_todo`, and `.certs` under `/opt/gpt5_fast_todo`). Adjust paths if your deploy script writes elsewhere.

## Example systemd service file - taken from my server, change to suit your needs
```
testuser@server:/etc/systemd/system/multi-user.target.wants$ cat gpt5_fast_todo.service
[Unit]
Description=Fast Todo FastAPI service
After=network.target

[Service]
# The user which will run the uvicorn process. Default is 'www-data'.
User=www-data
Group=www-data

# Path where the app lives
WorkingDirectory=/opt/gpt5_fast_todo

# Optional environment file (created by deploy script)
# The installer will write /etc/default/gpt5_fast_todo
EnvironmentFile=/etc/default/gpt5_fast_todo
Environment="PYTHONPATH=/opt/gpt5_fast_todo"

# ExecStart runs uvicorn from the virtualenv. Adjust venv path if needed.
ExecStart=/opt/gpt5_fast_todo/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 10443 \
  --ssl-keyfile /opt/gpt5_fast_todo/.certs/privkey.pem --ssl-certfile /opt/gpt5_fast_todo/.certs/fullchain.pem

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## SECRET_KEY and environment configuration

The app uses a `SECRET_KEY` to sign JWT access tokens. Keep this key private and persistent between server restarts unless you intentionally want to invalidate sessions.

- Windows (development/local)
  - The PowerShell startup script will create `gpt5_fast_todo.env` in the project root and write a generated `SECRET_KEY` into it if one is not found. That file will be loaded for the running process when you start the server via the script.
  - Example: `gpt5_fast_todo.env` will contain a line like `SECRET_KEY=...`.

- Debian / Linux (server)
  - For a durable server place the secret in a system location that your service can read. A common pattern is a file under `/etc/` (for example `/etc/gpt5_fast_todo/env`) or as an environment variable in your systemd service unit. The scripts accept an externally-provided `SECRET_KEY` (e.g. `SECRET_KEY=yourkey scripts/run_server_debian.sh`).
  - Rotating the `SECRET_KEY` will invalidate previously issued JWTs. Users may need to log out and log back in after rotation.

Security notes
  - Do not commit `gpt5_fast_todo.env` or any file containing `SECRET_KEY` to version control. Treat it like a secret.

## Hashtags in names and todos

You can type hashtags (`#tag`) directly into list names or todo item names. The client will extract those hashtags and add them to the todo metadata automatically. This makes tagging quick — type a tag into the title and it will be recorded separately while the visible text remains usable.

Behavior details
  - Hashtags can be typed anywhere in the name. They are parsed and added to the todo/list tags, and removed from the name.
  - The visible title will not retain the hashtags you typed; tags are stored as structured metadata for searches and filters.
  - You can also put hashtags in the note text of a todo. These hashtags are retained in the note text.

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

## Final notes and references

This README section documents the common workflows for local and small-server deployments. For production, replace self-signed certs with CA-signed certs, secure `SECRET_KEY` storage, and run the server under a service manager (systemd) with proper logging and rotation.

Files and scripts referenced in this README (located in the repository):
  - `scripts/run_server_dev_windows.ps1` — Windows helper script (creates .venv, installs, runs server).
  - `scripts/run_server_dev_debian.sh` — Debian/Linux helper script (creates .venv, installs, runs server).
  - `scripts/add_user.py` — CLI helper to add users from the server machine.

