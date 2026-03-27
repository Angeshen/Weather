@echo off
echo ============================================
echo   Kalshi Bot - Update Server
echo ============================================
echo.

set SERVER_IP=159.223.129.65

echo Uploading bot files to %SERVER_IP%...
echo.

:: Create directories
ssh -o StrictHostKeyChecking=no root@%SERVER_IP% "mkdir -p /opt/kalshi-bot/src/core /opt/kalshi-bot/src/data /opt/kalshi-bot/src/web/templates /opt/kalshi-bot/src/web/static"

:: Upload source files
scp -o StrictHostKeyChecking=no src\__init__.py root@%SERVER_IP%:/opt/kalshi-bot/src/
scp -o StrictHostKeyChecking=no src\config.py root@%SERVER_IP%:/opt/kalshi-bot/src/
scp -o StrictHostKeyChecking=no src\core\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/core/
scp -o StrictHostKeyChecking=no src\data\*.py root@%SERVER_IP%:/opt/kalshi-bot/src/data/
scp -o StrictHostKeyChecking=no src\web\__init__.py root@%SERVER_IP%:/opt/kalshi-bot/src/web/
scp -o StrictHostKeyChecking=no src\web\app.py root@%SERVER_IP%:/opt/kalshi-bot/src/web/
scp -o StrictHostKeyChecking=no src\web\templates\dashboard.html root@%SERVER_IP%:/opt/kalshi-bot/src/web/templates/
scp -o StrictHostKeyChecking=no dashboard.py root@%SERVER_IP%:/opt/kalshi-bot/
scp -o StrictHostKeyChecking=no .env root@%SERVER_IP%:/opt/kalshi-bot/
scp -o StrictHostKeyChecking=no C:\Users\CPecoraro\kalshi-key.pem root@%SERVER_IP%:/opt/kalshi-bot/kalshi-key.pem

:: Fix paths for Linux and bind to 0.0.0.0
ssh -o StrictHostKeyChecking=no root@%SERVER_IP% "cd /opt/kalshi-bot && sed -i 's|C:\\Users\\CPecoraro\\kalshi-key.pem|/opt/kalshi-bot/kalshi-key.pem|g' .env && sed -i 's|127.0.0.1|0.0.0.0|g' dashboard.py"

:: Restart the bot
ssh -o StrictHostKeyChecking=no root@%SERVER_IP% "systemctl restart kalshi-bot"

echo.
echo ============================================
echo   Update complete! Bot restarted.
echo   Dashboard: http://%SERVER_IP%:5050
echo ============================================
echo.
pause
