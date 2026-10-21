@echo off
title AI-Driven Embedded Programmer - Full evaluation
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
echo ==========================================================
echo   Full evaluation: 22 requests x several setups x 2 models
echo   This takes a few hours. Keep the laptop plugged in.
echo   It is safe to close this window: run it again to continue.
echo ==========================================================

rem Make sure the local model server (Ollama) is running
ollama list >nul 2>nul || ( start "" /min ollama serve & timeout /t 5 >nul )

set PY=".venv\Scripts\python.exe"

echo.
echo ---- Groq GPT-OSS 120B: plain model, then full system ----
%PY% scripts\evaluate.py --provider groq --mode plain,full --pause 5
echo.
echo ---- Local Qwen2.5-Coder 7B: all four setups ----
%PY% scripts\evaluate.py --provider ollama --mode plain,full,no_examples,no_fix --pause 1
echo.
echo ---- Groq GPT-OSS 120B: the two extra setups (may hit the daily limit; run again tomorrow) ----
%PY% scripts\evaluate.py --provider groq --mode no_examples,no_fix --pause 5

echo.
%PY% scripts\eval_report.py
echo.
echo Finished. The report is in data\eval\REPORT.md
pause
