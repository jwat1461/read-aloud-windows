@echo off
rem Double-click to remove the "Read aloud" entry from the Explorer right-click menu.
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\uninstall_context_menu.ps1"

echo.
pause
