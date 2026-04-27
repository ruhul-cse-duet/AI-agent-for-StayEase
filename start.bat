@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [INFO] Creating virtual environment...
  python -m venv .venv
)

if not exist ".env" (
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo [INFO] Created .env from .env.example. Update keys before sending chat requests.
  ) else (
    echo [WARN] .env.example not found. Create a .env file with DATABASE_URL and LLM config.
  )
)

".venv\Scripts\python.exe" -c "import uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo [INFO] Starting StayEase API on http://localhost:8001/docs
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
