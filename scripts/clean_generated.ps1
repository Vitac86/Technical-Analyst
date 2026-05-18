# Removes safe generated files. Source code, .venv, node_modules, and DB are
# preserved unless the corresponding optional switch is passed.
#
# Usage:
#   .\clean_generated.ps1                          # safe cleanup only
#   .\clean_generated.ps1 -NodeModules             # also delete frontend/node_modules
#   .\clean_generated.ps1 -Venv                    # also delete backend/.venv
#   .\clean_generated.ps1 -Database                # also delete backend/technical_analyst.db
#   .\clean_generated.ps1 -NodeModules -Venv -Database  # full wipe of generated dirs
param(
    [switch]$NodeModules,
    [switch]$Venv,
    [switch]$Database
)

$root = Split-Path -Parent $PSScriptRoot

function Remove-IfExists {
    param([string]$Path, [string]$Label)
    if (Test-Path $Path) {
        Remove-Item -Path $Path -Recurse -Force -Confirm:$false
        Write-Host "Removed  : $Label" -ForegroundColor Yellow
    } else {
        Write-Host "Not found: $Label" -ForegroundColor DarkGray
    }
}

Write-Host ''
Write-Host '=== Safe cleanup ===' -ForegroundColor Cyan

# Always-safe targets
Remove-IfExists (Join-Path $root 'frontend\dist')         'frontend/dist'
Remove-IfExists (Join-Path $root 'logs')                  'logs'
Remove-IfExists (Join-Path $root 'backend\.pytest_cache') 'backend/.pytest_cache'
Remove-IfExists (Join-Path $root 'backend\.ruff_cache')   'backend/.ruff_cache'

# All __pycache__ directories
$pycacheDirs = Get-ChildItem -Path $root -Filter '__pycache__' -Recurse -Directory -Force -ErrorAction SilentlyContinue
foreach ($d in $pycacheDirs) {
    Remove-Item -Path $d.FullName -Recurse -Force -Confirm:$false
    Write-Host "Removed  : $($d.FullName.Replace($root, '').TrimStart('\/'))" -ForegroundColor Yellow
}
if (-not $pycacheDirs) { Write-Host 'Not found: __pycache__ dirs' -ForegroundColor DarkGray }

# All *.pyc files (any that survived outside __pycache__)
$pycFiles = Get-ChildItem -Path $root -Filter '*.pyc' -Recurse -File -Force -ErrorAction SilentlyContinue
foreach ($f in $pycFiles) {
    Remove-Item -Path $f.FullName -Force -Confirm:$false
    Write-Host "Removed  : $($f.FullName.Replace($root, '').TrimStart('\/'))" -ForegroundColor Yellow
}

# --- Optional targets ---
if ($NodeModules -or $Venv -or $Database) {
    Write-Host ''
    Write-Host '=== Optional cleanup ===' -ForegroundColor Cyan
}

if ($NodeModules) {
    Remove-IfExists (Join-Path $root 'frontend\node_modules') 'frontend/node_modules'
}
if ($Venv) {
    Remove-IfExists (Join-Path $root 'backend\.venv') 'backend/.venv'
}
if ($Database) {
    Remove-IfExists (Join-Path $root 'backend\technical_analyst.db') 'backend/technical_analyst.db'
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
