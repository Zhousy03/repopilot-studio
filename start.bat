@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Create environment first: python -m venv .venv
  echo Then: .venv\Scripts\python.exe -m pip install -e ".[ai,dev]"
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m uvicorn repopilot.api:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
