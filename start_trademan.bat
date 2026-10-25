@echo off
echo Stopping any existing Trademan instances...

REM Kill process on port 8000 (Trademan Backend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo Killing Trademan Backend process PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

REM Kill process on port 3000 (Trademan Frontend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 " ^| findstr "LISTENING"') do (
    echo Killing Trademan Frontend process PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

REM Close any existing titled windows from a previous run
taskkill /FI "WINDOWTITLE eq Trademan Backend" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq Trademan Frontend" /F /T >nul 2>&1

timeout /t 4 /nobreak >nul

echo Starting Trademan Backend...
start "Trademan Backend" cmd /k "cd /d D:\SnehalImp\git\trademan\backend && C:\Users\sneha\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\Scripts\uvicorn.exe main:app --reload"

echo Starting Trademan Frontend...
start "Trademan Frontend" cmd /k "cd /d D:\SnehalImp\git\trademan\frontend && npm run dev"

echo Both servers started.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
