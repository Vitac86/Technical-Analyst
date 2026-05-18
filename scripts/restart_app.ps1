#Requires -Version 5.1
<#
.SYNOPSIS  Restart Technical Analyst: stop then start.
#>
$ErrorActionPreference = 'Continue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "=== Technical Analyst - Restart ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "Step 1/2: Stopping..." -ForegroundColor Yellow
& "$ScriptDir\stop_app.ps1"

Write-Host "Waiting 2 seconds before restart..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

Write-Host "Step 2/2: Starting..." -ForegroundColor Yellow
& "$ScriptDir\start_app.ps1"
