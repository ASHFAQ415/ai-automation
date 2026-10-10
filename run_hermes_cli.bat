@echo off
chcp 65001 >nul
title Hermes Agent - Interactive Terminal Chat
cd /d "%~dp0hermes"
echo ===================================================
echo   Starting Hermes Agent (Terminal Chat)
echo ===================================================
echo Type your messages directly here. Press Ctrl+C to exit.
echo.

".venv\Scripts\python.exe" cli.py
pause
