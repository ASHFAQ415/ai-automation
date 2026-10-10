@echo off
chcp 65001 >nul
title Hermes Agent - Chat and News Gateway
cd /d "%~dp0hermes"
echo ===================================================
echo   Starting Hermes Agent (Chat and News Gateway)
echo ===================================================
echo Telegram / Messaging Gateway starting...
echo (Project building engine is NOT running)
echo.

".venv\Scripts\python.exe" cli.py --gateway
pause
