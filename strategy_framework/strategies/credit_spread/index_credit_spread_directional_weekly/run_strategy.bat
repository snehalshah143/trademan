@echo off
REM =============================================================================
REM Directional Index Credit Spread (Weekly) — Daily Launcher
REM Scheduled via Windows Task Scheduler to run every weekday at 09:00 IST.
REM
REM Behaviour:
REM   - Execution script: skips entry if an open position already exists (idempotent)
REM   - Monitoring script: recovers open position from DB or positionbook on restart
REM =============================================================================

set STRATEGY_DIR=D:\SnehalImp\git\trademan\strategy_framework
set PYTHON=C:\Users\sneha\AppData\Local\Microsoft\WindowsApps\python.exe

REM Log file for Task Scheduler output (separate from strategy logs)
set LOG_DIR=%STRATEGY_DIR%\strategies\credit_spread\index_credit_spread_directional_weekly\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LAUNCHER_LOG=%LOG_DIR%\launcher_%date:~-4,4%%date:~-7,2%%date:~0,2%.log

echo ============================================================ >> "%LAUNCHER_LOG%"
echo %date% %time% — Strategy launcher started >> "%LAUNCHER_LOG%"
echo ============================================================ >> "%LAUNCHER_LOG%"

cd /d "%STRATEGY_DIR%"

"%PYTHON%" -m strategies.credit_spread.index_credit_spread_directional_weekly.final_index_credit_spread_directional_weekly >> "%LAUNCHER_LOG%" 2>&1

echo %date% %time% — Strategy launcher finished (exit code %ERRORLEVEL%) >> "%LAUNCHER_LOG%"
