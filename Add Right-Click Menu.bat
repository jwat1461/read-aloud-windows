@echo off
rem Double-click to add a "Read aloud" entry to the Explorer right-click menu
rem for text files. Undo with "Remove Right-Click Menu.bat".
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\install_context_menu.ps1"

echo.
pause
