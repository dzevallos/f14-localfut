@echo off
rem Settings editor for the local FIFA 14 FUT server.
rem Double-click this, or run it from a command prompt.
setlocal
set "TOOLS=%~dp0"
set "PROJECT=%TOOLS%.."
set "VENV=%PROJECT%\.venv\Scripts\python.exe"

if exist "%VENV%" (
    set "PY=%VENV%"
) else (
    where py >nul 2>nul && set "PY=py" || set "PY=python"
)

"%PY%" "%TOOLS%fut_settings.py"
if errorlevel 1 (
    echo.
    echo Settings editor exited with an error.
)
echo.
pause
endlocal
