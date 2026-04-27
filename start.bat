@echo off
setlocal
set PORT=8001

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

echo [INFO] Cleaning old StayEase API processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*AI agent for StayEase*' -and $_.CommandLine -like '*uvicorn main:app*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }" >nul 2>nul

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [WARN] Port %PORT% still in use by PID %%P. Trying to stop it...
  taskkill /PID %%P /F >nul 2>nul
)

netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [WARN] Port %PORT% is still busy. Trying fallback cleanup for python.exe...
  taskkill /F /IM python.exe >nul 2>nul
)

netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo [ERROR] Port %PORT% is still busy. Please close the process using this port, then run start.bat again.
  exit /b 1
)

echo [INFO] Starting StayEase API on http://localhost:%PORT%/docs
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port %PORT% --reload
