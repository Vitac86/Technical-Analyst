@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0size_report.ps1" %*
