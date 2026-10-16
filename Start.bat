@echo off
title AI-Driven Embedded Programmer
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
echo Starting AI-Driven Embedded Programmer...
echo Keep this window open while you use the app. Close it to stop.
".venv\Scripts\python.exe" -m app.main
pause
