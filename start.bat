@echo off
echo Stopping existing instances...

REM Kill process on port 5000 (OpenAlgo Backend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM Kill process on port 5173 (OpenAlgo Frontend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)

taskkill /FI "WINDOWTITLE eq OpenAlgo Backend" /F /T >nul 2>&1
taskkill /FI "WINDOWTITLE eq OpenAlgo Frontend" /F /T >nul 2>&1

:: Kill processes on port 8000 (Trademan Backend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Kill processes on port 3000 (Trademan Frontend)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Ensure logs directory exists
if not exist "%~dp0logs" mkdir "%~dp0logs"

timeout /t 4 /nobreak >nul

echo Existing instances stopped.
echo Starting all services in background...

powershell -WindowStyle Hidden -Command "Start-Process cmd -ArgumentList '/c cd /d D:\SnehalImp\git\OpenAlgo\openalgo && uv run app.py >> D:\SnehalImp\git\trademan\logs\openalgo_backend.log 2>&1' -WindowStyle Hidden"

powershell -WindowStyle Hidden -Command "Start-Process cmd -ArgumentList '/c cd /d D:\SnehalImp\git\OpenAlgo\openalgo\frontend && npm run dev >> D:\SnehalImp\git\trademan\logs\openalgo_frontend.log 2>&1' -WindowStyle Hidden"

powershell -WindowStyle Hidden -Command "Start-Process cmd -ArgumentList '/c cd /d %~dp0backend && C:\Users\sneha\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\Scripts\uvicorn.exe main:app --reload >> D:\SnehalImp\git\trademan\logs\trademan_backend.log 2>&1' -WindowStyle Hidden"

powershell -WindowStyle Hidden -Command "Start-Process cmd -ArgumentList '/c cd /d %~dp0frontend && npm run dev >> D:\SnehalImp\git\trademan\logs\trademan_frontend.log 2>&1' -WindowStyle Hidden"

echo All services started.
echo   OpenAlgo Backend:  http://localhost:5000
echo   OpenAlgo Frontend: http://localhost:5173
echo   Trademan Backend:  http://localhost:8000
echo   Trademan Frontend: http://localhost:3000
echo.
echo Logs: %~dp0logs\
