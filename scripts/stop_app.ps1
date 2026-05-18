#Requires -Version 5.1
<#
.SYNOPSIS  Stop Technical Analyst backend (8001) and frontend (5173).
#>
$ErrorActionPreference = 'Continue'

function Stop-PortListener {
    param([int]$Port, [string]$Label)
    $stopped = $false
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($conn in $conns) {
            $ownerPid = $conn.OwningProcess
            if ($null -ne $ownerPid -and $ownerPid -ne 0) {
                $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                $name = if ($proc) { $proc.ProcessName } else { 'unknown' }
                Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped PID $ownerPid ($name) on $Label (port $Port)" -ForegroundColor Green
                $stopped = $true
            }
        }
    } catch {
        # fallback: netstat
        $lines = & netstat -ano 2>$null |
                 Select-String "127\.0\.0\.1:$Port\s|0\.0\.0\.0:$Port\s" |
                 Select-String 'LISTENING'
        foreach ($line in $lines) {
            $parts = ($line.Line.Trim() -split '\s+')
            $pid = $parts[-1]
            if ($pid -match '^\d+$' -and [int]$pid -ne 0) {
                $proc = Get-Process -Id ([int]$pid) -ErrorAction SilentlyContinue
                $name = if ($proc) { $proc.ProcessName } else { 'unknown' }
                Stop-Process -Id ([int]$pid) -Force -ErrorAction SilentlyContinue
                Write-Host "  Stopped PID $pid ($name) on $Label (port $Port)" -ForegroundColor Green
                $stopped = $true
            }
        }
    }
    return $stopped
}

Write-Host ""
Write-Host "=== Technical Analyst - Stop ===" -ForegroundColor Cyan
Write-Host ""

$anyStopped = $false

Write-Host "Stopping backend (port 8001)..."
if (Stop-PortListener -Port 8001 -Label 'backend') { $anyStopped = $true }

Write-Host "Stopping frontend (port 5173)..."
if (Stop-PortListener -Port 5173 -Label 'frontend') { $anyStopped = $true }

Write-Host "Stopping legacy backend (port 8000, best-effort)..."
$null = Stop-PortListener -Port 8000 -Label 'legacy-backend'

if (-not $anyStopped) {
    Write-Host ""
    Write-Host "App is already stopped (no listeners on ports 8001 / 5173)." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "Done." -ForegroundColor Green
}
Write-Host ""
