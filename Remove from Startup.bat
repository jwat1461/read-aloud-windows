@echo off
rem Double-click to stop "Read Aloud Anywhere" launching at login.
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\install_startup.ps1" -Remove

echo.
pause
