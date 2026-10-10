@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo   Hermes Windows Service Installer (NSSM Automation)
echo =======================================================

:: 1. Verify administrative privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be executed with Administrator privileges.
    echo Please right-click and select "Run as administrator".
    pause
    exit /b 1
)

:: 2. Locate NSSM executable
set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%.."
set "PYTHON_EXE=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "SERVICE_NAME=HermesAutomationEngine"

where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] 'nssm' was not found in your PATH.
    echo Please download NSSM from https://nssm.cc/download and place nssm.exe in C:\Windows\System32 or project root.
    echo.
    echo As an immediate alternative, you can run the standalone supervisor:
    echo   python "%SCRIPT_DIR%supervise_hermes.py"
    echo.
    pause
    exit /b 1
)

:: 3. Configure Windows Service
echo Installing %SERVICE_NAME%...
nssm stop %SERVICE_NAME% >nul 2>&1
nssm remove %SERVICE_NAME% confirm >nul 2>&1

nssm install %SERVICE_NAME% "%PYTHON_EXE%"
nssm set %SERVICE_NAME% AppParameters "%SCRIPT_DIR%supervise_hermes.py --port 8000"
nssm set %SERVICE_NAME% AppDirectory "%BACKEND_DIR%"
nssm set %SERVICE_NAME% Description "Hermes Autonomous AI Automation Engine - Supervised Production Service"
nssm set %SERVICE_NAME% Start SERVICE_AUTO_START
nssm set %SERVICE_NAME% AppStdout "%BACKEND_DIR%\hermes_stdout.log"
nssm set %SERVICE_NAME% AppStderr "%BACKEND_DIR%\hermes_stderr.log"
nssm set %SERVICE_NAME% AppRotateFiles 1
nssm set %SERVICE_NAME% AppRotateOnline 1
nssm set %SERVICE_NAME% AppRotateSeconds 86400
nssm set %SERVICE_NAME% AppRotateBytes 10485760
nssm set %SERVICE_NAME% AppRestartDelay 3000

:: 4. Start the service
echo Starting %SERVICE_NAME%...
nssm start %SERVICE_NAME%

echo.
echo =======================================================
echo  %SERVICE_NAME% successfully registered and running!
echo  Check health: curl http://localhost:8000/api/health/ready
echo =======================================================
pause
