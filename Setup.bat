@echo off
setlocal EnableDelayedExpansion
title AI-Driven Embedded Programmer - Setup
cd /d "%~dp0"
echo ==========================================================
echo   AI-Driven Embedded Programmer - one-time setup
echo ==========================================================

rem ---- pick a Python: prefer 3.13 / 3.12 / 3.11 (best package support), else any Python 3
set "PY="
for %%V in (3.13 3.12 3.11 3.14 3) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY ( python -c "import sys" >nul 2>nul && set "PY=python" )
if not defined PY (
  echo.
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH". Then run Setup.bat again.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('!PY! -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%v"
echo Using Python !PYVER!  (!PY!)

rem ---- create or repair the app's private environment (.venv)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip --version >nul 2>nul || (
    echo The existing .venv has no pip. Repairing it...
    ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>nul
  )
  ".venv\Scripts\python.exe" -m pip --version >nul 2>nul || (
    echo Repair failed. Re-creating .venv...
    rmdir /s /q ".venv"
  )
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Creating the app's private Python environment...
  !PY! -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip --version >nul 2>nul || ".venv\Scripts\python.exe" -m ensurepip --upgrade || goto :fail
)

echo.
echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check || goto :fail

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
echo Please take a screenshot of this window and send it.
pause
exit /b 1
