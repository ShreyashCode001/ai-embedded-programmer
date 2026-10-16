@echo off
title AI-Driven Embedded Programmer
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :nosetup
".venv\Scripts\python.exe" -c "import fastapi, uvicorn" >nul 2>nul || goto :nosetup
echo Starting AI-Driven Embedded Programmer...
echo Keep this window open while you use the app. Close it to stop.
".venv\Scripts\python.exe" -m app.main
pause
exit /b 0

:nosetup
echo The app is not installed yet (or setup did not finish).
echo Double-click Setup.bat first, wait until it says "Done", then run Start.bat again.
pause
exit /b 1
