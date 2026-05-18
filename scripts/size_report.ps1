# Prints folder/file sizes (MB) for top-level entries and known heavy paths.
# Read-only — modifies nothing.
param()

$root = Split-Path -Parent $PSScriptRoot

function Get-SizeMB {
    param([string]$Path)
    $item = Get-Item -Path $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return 0 }
    if ($item -is [System.IO.FileInfo]) {
        $bytes = $item.Length
    } else {
        $bytes = (Get-ChildItem -Path $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum).Sum
    }
    if (-not $bytes) { $bytes = 0 }
    return [math]::Round($bytes / 1MB, 2)
}

# --- Top-level entries ---
$topResults = @()
Get-ChildItem -Path $root -Force | ForEach-Object {
    $topResults += [PSCustomObject]@{
        'Size (MB)' = Get-SizeMB $_.FullName
        'Path'      = $_.Name
    }
}

Write-Host ''
Write-Host '=== Top-level sizes ===' -ForegroundColor Cyan
$topResults | Sort-Object 'Size (MB)' -Descending | Format-Table -AutoSize

# --- Known heavy generated paths ---
$heavyPaths = @(
    [PSCustomObject]@{ Label = 'backend/.venv';                Path = Join-Path $root 'backend\.venv' }
    [PSCustomObject]@{ Label = 'frontend/node_modules';        Path = Join-Path $root 'frontend\node_modules' }
    [PSCustomObject]@{ Label = 'frontend/dist';                Path = Join-Path $root 'frontend\dist' }
    [PSCustomObject]@{ Label = 'logs';                         Path = Join-Path $root 'logs' }
    [PSCustomObject]@{ Label = 'backend/.pytest_cache';        Path = Join-Path $root 'backend\.pytest_cache' }
    [PSCustomObject]@{ Label = 'backend/.ruff_cache';          Path = Join-Path $root 'backend\.ruff_cache' }
    [PSCustomObject]@{ Label = 'backend/technical_analyst.db'; Path = Join-Path $root 'backend\technical_analyst.db' }
)

$heavyResults = @()
foreach ($entry in $heavyPaths) {
    if (Test-Path $entry.Path) {
        $heavyResults += [PSCustomObject]@{
            'Size (MB)' = Get-SizeMB $entry.Path
            'Path'      = $entry.Label
        }
    }
}

if ($heavyResults.Count -gt 0) {
    Write-Host '=== Heavy generated paths ===' -ForegroundColor Cyan
    $heavyResults | Sort-Object 'Size (MB)' -Descending | Format-Table -AutoSize
}
