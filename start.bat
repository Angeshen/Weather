@echo off
cd /d "%~dp0"
taskkill /f /im python.exe >nul 2>&1
timeout /t 1 /nobreak >nul
start http://localhost:5050
python dashboard.py
