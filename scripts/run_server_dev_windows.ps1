<#
.SYNOPSIS
  Windows-friendly helper to create a venv, install dependencies, ensure SECRET_KEY,
  and run the FastAPI app with uvicorn (HTTPS with self-signed cert for local dev).

Usage:
  .\scripts\run_server_dev_windows.ps1          # install and run server (dev/reload)
  .\scripts\run_server_dev_windows.ps1 --dev
  .\scripts\run_server_dev_windows.ps1 --debug
  .\scripts\run_server_dev_windows.ps1 --debug-wait
#>

$ErrorActionPreference = 'Stop'

# Default flags
$RELOAD = $true
$DEBUGPY = $false
$DEBUGPY_WAIT = $false
$FN_LINK_DEBUG = $false

# Optional server log file. Set to a filename (e.g., 'server_run.log') to enable logging,
# or leave empty to disable file logging.
$LOG_FILE = 'server_win.log'

foreach ($arg in $args) {
    switch ($arg) {
        '--dev' { $RELOAD = $true }
    '--debug' { $DEBUGPY = $true }
        '--debug-wait' { $DEBUGPY = $true; $DEBUGPY_WAIT = $true }
    '--fn-link-debug' { $FN_LINK_DEBUG = $true }
        default { }
    }
}

$PY = 'python'
$VENV_DIR = '.venv'
$REQ_FILE = 'requirements.txt'
$REQ_FILE_DOC = 'requirements_server.txt'
$PIP_PACKAGES = @(
    'fastapi',
    'uvicorn[standard]',
    'sqlmodel',
    'aiosqlite',
    'passlib[bcrypt]',
    'python-jose[cryptography]',
    'httpx'
)

Write-Host "[run_server] Using Python: $(& $PY --version 2>&1)"

if (-not (Get-Command $PY -ErrorAction SilentlyContinue)) {
    Write-Error "ERROR: python not found. Install Python 3.x and ensure 'python' is on PATH."; exit 2
}

# Create venv if missing
if (-not (Test-Path $VENV_DIR)) {
    Write-Host "[run_server] Creating virtualenv in $VENV_DIR"
    & $PY -m venv $VENV_DIR
}

$VENV_PY = Join-Path $VENV_DIR 'Scripts\python.exe'
if (-not (Test-Path $VENV_PY)) {
    Write-Error "Virtualenv appears broken (missing $VENV_PY)"; exit 3
}

# Upgrade pip
& $VENV_PY -m pip install --upgrade pip setuptools wheel | Out-Null

# Install dependencies
if (Test-Path $REQ_FILE) {
    Write-Host "[run_server] Installing from $REQ_FILE"
    & $VENV_PY -m pip install -r $REQ_FILE
}
elseif (Test-Path $REQ_FILE_DOC) {
    $lines = Get-Content $REQ_FILE_DOC -ErrorAction SilentlyContinue
    $maybeReq = $lines | Where-Object { $_ -match '^[a-zA-Z0-9_.-]+' -and ($_ -notmatch '^```') }
    if ($maybeReq.Count -gt 0) {
        Write-Host "[run_server] Installing from $REQ_FILE_DOC"
        & $VENV_PY -m pip install -r $REQ_FILE_DOC
    }
    else {
        Write-Host "[run_server] $REQ_FILE_DOC appears to be documentation; installing common packages instead"
        & $VENV_PY -m pip install --no-cache-dir @($PIP_PACKAGES)
    }
}
else {
    Write-Host "[run_server] No requirements file found; installing common packages"
    & $VENV_PY -m pip install --no-cache-dir @($PIP_PACKAGES)
}

# Environment file (Windows fallback): look in ProgramData, then repo root
$envFile1 = Join-Path $env:ProgramData 'fast_todo\fast_todo.env'
$envFile2 = Join-Path (Get-Location) 'fast_todo.env'
$envFile = $null
if (Test-Path $envFile1) { $envFile = $envFile1 }
elseif (Test-Path $envFile2) { $envFile = $envFile2 }

# Load SECRET_KEY only from env file if present
if ($envFile) {
    Write-Host "[run_server] Loading SECRET_KEY from $envFile"
    $lines = Get-Content $envFile -ErrorAction SilentlyContinue
    foreach ($ln in $lines) {
        if ($ln -match '^SECRET_KEY=(.*)') {
            $val = $Matches[1].Trim()
            $val = $val.Trim('"')
            $env:SECRET_KEY = $val
            break
        }
    }
}
else {
    Write-Warning "Environment file not found; SECRET_KEY not set"
}

if (-not $env:SECRET_KEY) {
    Write-Host "[run_server] SECRET_KEY not set; generating a temporary one for this session"
    # Generate a temporary SECRET_KEY using python -c (here-doc is not valid in PowerShell)
    $secret = & $VENV_PY -c 'import secrets, sys; sys.stdout.write(secrets.token_hex(32))'
    $env:SECRET_KEY = $secret.Trim()
    Write-Host "[run_server] Generated SECRET_KEY (exported for this process). For production, set SECRET_KEY in the service environment."
}

# Ensure DATABASE_URL is set (default to local fast_todo.db with aiosqlite)
if (-not $env:DATABASE_URL -or $env:DATABASE_URL -eq '') {
    $env:DATABASE_URL = 'sqlite+aiosqlite:///./fast_todo.db'
    Write-Host "[run_server] DATABASE_URL not set; defaulting to $($env:DATABASE_URL)"
}

# Start uvicorn settings
$HOST_ADDR = if ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$PORT = if ($env:PORT) { $env:PORT } else { '10443' }
$APP_MODULE = if ($env:APP_MODULE) { $env:APP_MODULE } else { 'app.main:app' }
$UVICORN_LOG_LEVEL = if ($env:UVICORN_LOG_LEVEL) { $env:UVICORN_LOG_LEVEL } else { 'info' }
$UVICORN_ACCESS_LOG = if ($env:UVICORN_ACCESS_LOG) { $env:UVICORN_ACCESS_LOG } else { 'true' }

# Certificates (self-signed for local dev)
$CERT_DIR = '.certs'
$CERT_KEY = Join-Path $CERT_DIR 'privkey.pem'
$CERT_PUB = Join-Path $CERT_DIR 'fullchain.pem'

if (-not (Test-Path $CERT_DIR)) { New-Item -ItemType Directory -Path $CERT_DIR -Force | Out-Null }
if (-not (Test-Path $CERT_KEY) -or -not (Test-Path $CERT_PUB)) {
    Write-Host "[run_server] Generating self-signed certificate in $CERT_DIR using repository Python generator"
    $pyGenerator = Join-Path 'scripts' 'generate_self_signed_cert.py'
    if (Test-Path $pyGenerator) {
        & $VENV_PY $pyGenerator -o $CERT_DIR -k (Split-Path $CERT_KEY -Leaf) -c (Split-Path $CERT_PUB -Leaf) -n 'localhost'
    }
    else {
        Write-Error "ERROR: Python cert generator $pyGenerator missing. Ensure scripts/generate_self_signed_cert.py exists."; exit 3
    }
}

if ($RELOAD) {
    Write-Host "[run_server] Starting uvicorn in dev mode (reload) on https://$($HOST_ADDR):$($PORT) (log_level=$UVICORN_LOG_LEVEL access_log=$UVICORN_ACCESS_LOG)"
    if ($DEBUGPY) {
        Write-Host "[run_server] Enabling debugpy (wait=$DEBUGPY_WAIT)"
        $env:ENABLE_DEBUGPY = '1'
        if (-not $env:DEBUGPY_PORT) { $env:DEBUGPY_PORT = '5678' }
        if ($DEBUGPY_WAIT) { $env:DEBUGPY_WAIT = '1' }
    }
    $uvicornArgs = @('-m', 'uvicorn', $APP_MODULE, '--host', $HOST_ADDR, '--port', $PORT, '--reload', '--ssl-keyfile', $CERT_KEY, '--ssl-certfile', $CERT_PUB, '--log-level', $UVICORN_LOG_LEVEL)
    if ($UVICORN_ACCESS_LOG -ne 'true') { $uvicornArgs += '--access-log'; $uvicornArgs += 'false' }
    if ($FN_LINK_DEBUG) { $env:DEBUG_FN_LINKS = '1'; Write-Host "[run_server] DEBUG_FN_LINKS is enabled (env:DEBUG_FN_LINKS=1)" }
    if ($LOG_FILE -and $LOG_FILE -ne '') {
        $logDir = Split-Path $LOG_FILE -Parent
        if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        Write-Host "[run_server] Redirecting output to $LOG_FILE"
        $oldEAP = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $VENV_PY @uvicornArgs 2>&1 | Tee-Object -FilePath $LOG_FILE -Append
        }
        finally {
            $ErrorActionPreference = $oldEAP
        }
    }
    else {
        & $VENV_PY @uvicornArgs
    }
}
else {
    Write-Host "[run_server] Starting uvicorn on https://$($HOST_ADDR):$($PORT) (log_level=$UVICORN_LOG_LEVEL access_log=$UVICORN_ACCESS_LOG)"
    $uvicornArgs = @('-m', 'uvicorn', $APP_MODULE, '--host', $HOST_ADDR, '--port', $PORT, '--workers', '1', '--ssl-keyfile', $CERT_KEY, '--ssl-certfile', $CERT_PUB, '--log-level', $UVICORN_LOG_LEVEL)
    if ($UVICORN_ACCESS_LOG -ne 'true') { $uvicornArgs += '--access-log'; $uvicornArgs += 'false' }
    if ($FN_LINK_DEBUG) { $env:DEBUG_FN_LINKS = '1'; Write-Host "[run_server] DEBUG_FN_LINKS is enabled (env:DEBUG_FN_LINKS=1)" }
    if ($LOG_FILE -and $LOG_FILE -ne '') {
        $logDir = Split-Path $LOG_FILE -Parent
        if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        Write-Host "[run_server] Redirecting output to $LOG_FILE"
        $oldEAP = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $VENV_PY @uvicornArgs 2>&1 | Tee-Object -FilePath $LOG_FILE -Append
        }
        finally {
            $ErrorActionPreference = $oldEAP
        }
    }
    else {
        & $VENV_PY @uvicornArgs
    }
}
