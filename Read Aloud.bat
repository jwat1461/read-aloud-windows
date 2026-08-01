@echo off
rem Launch the Read Aloud desktop app. pythonw keeps the console window hidden.
setlocal
set "HERE=%~dp0"

where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw.exe "%HERE%app\tts_app.py" %*
) else (
    python.exe "%HERE%app\tts_app.py" %*
)
