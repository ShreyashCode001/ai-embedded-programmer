@echo off
setlocal
title AI-Driven Embedded Programmer - Setup
cd /d "%~dp0"
echo ==========================================================
echo   AI-Driven Embedded Programmer - one-time setup
echo ==========================================================

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH". Then run Setup.bat again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Creating the app's private Python environment...
  %PY% -m venv .venv || goto :fail
)
echo.
echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || goto :fail

".venv\Scripts\python.exe" scripts\setup_app.py %* || goto :fail

echo.
echo ==========================================================
echo   Done! Double-click Start.bat to open the app.
echo ==========================================================
pause
exit /b 0

:fail
echo.
echo Setup stopped because of an error (see above).
pause
exit /b 1
