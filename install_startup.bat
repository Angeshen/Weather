@echo off
echo ============================================
echo   Kalshi Weather Bot - Startup Installer
echo ============================================
echo.
echo This will create a Windows startup shortcut
echo so the bot dashboard auto-launches when you log in.
echo.

set "SCRIPT_DIR=%~dp0"
set "PYTHON=C:\Users\CPecoraro\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "DASHBOARD=%SCRIPT_DIR%dashboard.py"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP%\KalshiWeatherBot.vbs"

:: Create a VBS launcher (runs without a console window)
echo Creating startup shortcut...
(
echo Set WshShell = CreateObject("WScript.Shell"^)
echo WshShell.CurrentDirectory = "%SCRIPT_DIR%"
echo WshShell.Run """%PYTHON%"" ""%DASHBOARD%""", 0, False
) > "%SHORTCUT%"

echo.
echo [OK] Startup shortcut created at:
echo      %SHORTCUT%
echo.
echo The bot dashboard will now start automatically
echo when you log into Windows.
echo.
echo To remove: delete the shortcut from
echo   %STARTUP%
echo.
pause
