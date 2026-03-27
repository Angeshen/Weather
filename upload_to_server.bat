@echo off
echo ============================================
echo   Kalshi Bot - Upload to DigitalOcean
echo ============================================
echo.

set /p SERVER_IP="Enter your droplet IP address: "
set /p SERVER_PASS="Enter your root password: "

echo.
echo Installing SCP tool if needed...
where pscp >nul 2>&1 || (
    echo ERROR: pscp not found. We'll use PowerShell instead.
)

echo.
echo Uploading bot files to %SERVER_IP%...
echo This may take a minute.
echo.

:: Use PowerShell SCP (built into Windows 10+)
ssh root@%SERVER_IP% "mkdir -p /opt/kalshi-bot/src/core /opt/kalshi-bot/src/data /opt/kalshi-bot/src/web/templates /opt/kalshi-bot/src/web/static"

:: Upload all source files
scp -r src\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/
scp -r src\core\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/core/
scp -r src\data\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/data/
scp -r src\web\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/web/
scp -r src\web\templates\*.html root@%SERVER_IP%:/opt/kalshi-bot/src/web/templates/
scp dashboard.py root@%SERVER_IP%:/opt/kalshi-bot/
scp .env root@%SERVER_IP%:/opt/kalshi-bot/
scp requirements.txt root@%SERVER_IP%:/opt/kalshi-bot/
scp deploy.sh root@%SERVER_IP%:/opt/kalshi-bot/

:: Upload your Kalshi private key
scp C:\Users\CPecoraro\kalshi-key.pem root@%SERVER_IP%:/opt/kalshi-bot/kalshi-key.pem

echo.
echo Files uploaded! Now running setup script...
ssh root@%SERVER_IP% "chmod +x /opt/kalshi-bot/deploy.sh && /opt/kalshi-bot/deploy.sh"

echo.
echo Starting the bot...
ssh root@%SERVER_IP% "cd /opt/kalshi-bot && sed -i 's|C:\\Users\\CPecoraro\\kalshi-key.pem|/opt/kalshi-bot/kalshi-key.pem|g' .env && sed -i 's|127.0.0.1|0.0.0.0|g' dashboard.py && systemctl start kalshi-bot"

echo.
echo ============================================
echo   DONE! Your bot is live at:
echo   http://%SERVER_IP%:5050
echo ============================================
echo.
pause
