@echo off
rem Start the OS-wide reader. Select text in any app and press Ctrl+Alt+R.
setlocal
set "HERE=%~dp0"

where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw.exe "%HERE%app\global_reader.py"
) else (
    python.exe "%HERE%app\global_reader.py"
)
