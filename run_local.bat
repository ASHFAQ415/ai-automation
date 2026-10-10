@echo off
chcp 65001 >nul
title Hermes Brain - Autonomous AI Lead and Project Engine
echo =========================================================
echo [HERMES BRAIN] - AUTONOMOUS FREELANCE ENGINE
echo   Executive: Mohd Ashfaq
echo   Core: Ollama qwen3:4b (RTX 3050 GPU) + Sub-Agent Swarm
echo =========================================================
echo Checking Ollama local server...
start /b "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve >nul 2>&1

echo Opening Hermes Brain Hub in your browser...
start "" "http://localhost:8000/brain"

echo Starting Backend and Sub-Agent Orchestrator...
cd /d "%~dp0backend"
".venv\Scripts\python.exe" run_server.py
pause
