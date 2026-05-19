#Requires -Version 5.1
<#
.SYNOPSIS  Start Technical Analyst: backend 8001, frontend 5173, browser at /scanner.
#>
$ErrorActionPreference = 'Continue'

# --- Resolve repo root from script location -----------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$LogDir      = Join-Path $RepoRoot 'logs'
$PythonExe   = Join-Path $BackendDir '.venv\Scripts\python.exe'

$BackendPort  = 8001
$FrontendPort = 5173
$BackendUrl   = "http://127.0.0.1:$BackendPort"
$FrontendUrl  = "http://127.0.0.1:$FrontendPort"
$HealthUrl    = "$BackendUrl/api/v1/health"

# --- Helper: kill all listeners on a port ------------------------------------
function Stop-PortListener {
    param([int]$Port, [string]$Label)
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $conns) {
            $ownerPid = $conn.OwningProcess
            if ($null -ne $ownerPid -and $ownerPid -ne 0) {
                $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                $name = if ($proc) { $proc.ProcessName } else { 'unknown' }
                Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped PID $ownerPid ($name) on $Label"
            }
        }
    } catch { }
}

# --- Banner ------------------------------------------------------------------
Write-Host ""
Write-Host "=== Technical Analyst - Starting ===" -ForegroundColor Cyan
Write-Host "  Backend  : $BackendUrl"
Write-Host "  Frontend : $FrontendUrl"
Write-Host "  Scanner  : $FrontendUrl/scanner"
Write-Host ""

# --- Guard: python venv must exist -------------------------------------------
if (-not (Test-Path $PythonExe)) {
    Write-Host "ERROR: Python venv not found at $PythonExe" -ForegroundColor Red
    Write-Host "       Run: cd backend; python -m venv .venv; .\.venv\Scripts\pip install -e .[dev]" -ForegroundColor Red
    exit 1
}

# --- Stop existing listeners -------------------------------------------------
Write-Host "Stopping existing listeners..." -ForegroundColor Yellow
Stop-PortListener -Port 8001 -Label 'backend'
Stop-PortListener -Port 5173 -Label 'frontend'
Stop-PortListener -Port 8000 -Label 'legacy-backend'
Write-Host "  Done."

# --- Create logs directory ---------------------------------------------------
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "Created logs\ directory."
}

# --- Write frontend/.env.local -----------------------------------------------
$envLocalPath = Join-Path $FrontendDir '.env.local'
"VITE_API_BASE_URL=http://127.0.0.1:$BackendPort/api/v1" |
    Set-Content -Path $envLocalPath -Encoding utf8
Write-Host "Updated frontend\.env.local  (VITE_API_BASE_URL -> port $BackendPort)"

# --- Start backend -----------------------------------------------------------
Write-Host ""
Write-Host "Starting backend..." -ForegroundColor Yellow

$backendLog    = Join-Path $LogDir 'backend.log'
$backendStdLog = Join-Path $LogDir 'backend_stdout.log'

# uvicorn writes informational logs to stderr; backend.log captures stderr
$backendProc = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$BackendPort" `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $backendStdLog `
    -RedirectStandardError  $backendLog `
    -NoNewWindow `
    -PassThru

Write-Host "  PID  : $($backendProc.Id)"
Write-Host "  Logs : logs\backend.log"

# --- Wait for backend health -------------------------------------------------
Write-Host ""
Write-Host "Waiting for backend health check..." -ForegroundColor Yellow
$maxWait  = 30
$interval = 1
$elapsed  = 0
$ready    = $false

while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds $interval
    $elapsed += $interval
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    Write-Host ""
    Write-Host "ERROR: Backend did not become healthy within $maxWait seconds." -ForegroundColor Red
    Write-Host "       Check logs\backend.log for details." -ForegroundColor Red
    Write-Host ""
    exit 1
}
Write-Host "  Backend ready." -ForegroundColor Green

# --- Start frontend ----------------------------------------------------------
Write-Host ""
Write-Host "Starting frontend..." -ForegroundColor Yellow

$frontendLog    = Join-Path $LogDir 'frontend.log'
$frontendErrLog = Join-Path $LogDir 'frontend_stderr.log'

# Use cmd.exe wrapper so npm.cmd resolves correctly with output redirection
$npmCmd = "npm.cmd run dev -- --host 127.0.0.1 --port $FrontendPort"
$frontendProc = Start-Process `
    -FilePath 'cmd.exe' `
    -ArgumentList "/c $npmCmd" `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $frontendLog `
    -RedirectStandardError  $frontendErrLog `
    -NoNewWindow `
    -PassThru

Write-Host "  PID  : $($frontendProc.Id)"
Write-Host "  Logs : logs\frontend.log"

# --- Wait for frontend -------------------------------------------------------
Write-Host ""
Write-Host "Waiting for frontend dev server..." -ForegroundColor Yellow
$elapsed = 0
$ready   = $false

while ($elapsed -lt $maxWait) {
    Start-Sleep -Seconds $interval
    $elapsed += $interval
    try {
        $resp = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}

if (-not $ready) {
    Write-Host ""
    Write-Host "ERROR: Frontend did not become ready within $maxWait seconds." -ForegroundColor Red
    Write-Host "       Check logs\frontend.log for details." -ForegroundColor Red
    Write-Host ""
    exit 1
}
Write-Host "  Frontend ready." -ForegroundColor Green

# --- Open browser ------------------------------------------------------------
Write-Host ""
$scannerUrl = "$FrontendUrl/scanner"
Write-Host "Opening browser at $scannerUrl ..." -ForegroundColor Yellow
Start-Process $scannerUrl
Write-Host "  Browser opened." -ForegroundColor Green

# --- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "=== App running - OK ===" -ForegroundColor Cyan
Write-Host "  Backend  : $BackendUrl    (PID $($backendProc.Id))"
Write-Host "  Frontend : $FrontendUrl   (PID $($frontendProc.Id))"
Write-Host "  Scanner  : $scannerUrl"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $backendLog"
Write-Host "  $frontendLog"
Write-Host ""
Write-Host "To stop   : .\scripts\stop_app.ps1"
Write-Host "To restart: .\scripts\restart_app.ps1"
Write-Host ""
