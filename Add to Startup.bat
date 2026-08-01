@echo off
rem Double-click to start "Read Aloud Anywhere" automatically at every login.
rem Creates a shortcut in your own Startup folder. Undo with "Remove from Startup.bat".
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\install_startup.ps1"

echo.
echo Starting it now so you don't have to log out and back in...
start "" "%HERE%Read Aloud Anywhere.bat"

echo.
pause
