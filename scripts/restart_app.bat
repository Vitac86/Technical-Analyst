@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0restart_app.ps1"
if errorlevel 1 (
    echo.
    echo Script exited with an error. Press any key to close...
    pause >nul
)
