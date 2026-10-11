@echo off
cd /d "%~dp0"

REM --- Check if already pip-installed (nexusaudio command exists) ---
where nexusaudio >nul 2>&1
if %errorlevel% equ 0 (
    echo Launching NexusAudio…
    nexusaudio %*
    exit /b %errorlevel%
)

REM --- Auto-venv ---
if not exist venv\ (
    echo Creating virtual environment…
    python -m venv venv
)
call venv\Scripts\activate.bat

if not exist venv\ok (
    echo Installing dependencies…
    pip install -e . 1>nul
    type nul > venv\ok
)

echo Launching NexusAudio…
python main.py %*
