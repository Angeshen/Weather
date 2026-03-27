@echo off
taskkill /f /im python.exe >nul 2>&1
echo Bot stopped.
timeout /t 2 /nobreak >nul
