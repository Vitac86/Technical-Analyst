@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean_generated.ps1" %*
