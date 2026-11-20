@echo off
rem Double-click to start the Instagram auto-post dashboard.
cd /d %~dp0
set PYTHONUTF8=1
chcp 65001 >nul

if not exist .venv (
  echo [setup] Creating virtual environment...
  python -m venv .venv || goto :error
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :error
)
if not exist .env copy .env.example .env >nul

start "" http://127.0.0.1:5000
.venv\Scripts\python.exe run.py
pause
exit /b 0

:error
echo.
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
exit /b 1
