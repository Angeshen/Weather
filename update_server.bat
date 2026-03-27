@echo off
echo ============================================
echo   Kalshi Bot - Push Update to Server
echo ============================================
echo.

cd /d "%~dp0"
set PATH=%PATH%;C:\Program Files\Git\cmd

echo [1/3] Committing changes...
git add -A
git commit -m "Bot update %date% %time%"
git push origin main

echo.
echo [2/3] Code pushed to GitHub.
echo.
echo [3/3] Now go to the DigitalOcean console and run:
echo.
echo   cd /opt/kalshi-bot ^&^& git pull ^&^& systemctl restart kalshi-bot
echo.
echo ============================================
echo   After running that command, your bot
echo   will be updated at:
echo   http://159.223.129.65:5050
echo ============================================
echo.
pause
