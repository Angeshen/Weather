@echo off
echo ============================================
echo   Kalshi Weather Bot - Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python not found. Installing via winget...
    winget install -e --id Python.Python.3.12 --source winget
    echo.
    echo [!] Python installed. Please RESTART this terminal and run setup.bat again.
    pause
    exit /b
)

echo [OK] Python found
python --version
echo.

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt
echo.

:: Check for .env
if not exist .env (
    echo [!] No .env file found. Creating from template...
    copy .env.example .env
    echo.
    echo [!] IMPORTANT: Edit .env with your Kalshi API key and private key path.
    echo     1. Go to kalshi.com - Settings - API Keys
    echo     2. Create a key and download the .pem file
    echo     3. Edit .env with your API key ID and .pem file path
    echo.
) else (
    echo [OK] .env file found
)

echo.
echo ============================================
echo   Setup complete! Run the bot with:
echo     python dashboard.py
echo ============================================
pause
