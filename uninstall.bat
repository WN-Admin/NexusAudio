@echo off
cd /d "%~dp0"

echo NexusAudio Uninstaller
echo =====================
echo.

REM --- pip installed ---
where nexusaudio >nul 2>&1
if %errorlevel% equ 0 (
    echo [1/3] Removing pip-installed package...
    pip uninstall nexusaudio -y >nul 2>&1
) else (
    echo [1/3] No pip-installed package found.
)

REM --- auto-venv ---
if exist venv\ (
    echo [2/3] Removing virtual environment...
    rmdir /s /q venv
) else (
    echo [2/3] No virtual environment found.
)

REM --- config ---
set CONFIG_DIR=%USERPROFILE%\.config\nexusaudio
if exist "%CONFIG_DIR%" (
    echo [3/3] Remove user config (%CONFIG_DIR%)?
    set /p CONFIRM="  Type y to confirm: "
    if /i "!CONFIRM!"=="y" (
        rmdir /s /q "%CONFIG_DIR%"
        echo      Config removed.
    ) else (
        echo      Skipped.
    )
) else (
    echo [3/3] No user config found.
)

echo.
echo Done. NexusAudio has been uninstalled.
pause
