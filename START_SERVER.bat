@echo off
title DevEx Studios FM Platform
color 1F

echo.
echo  =========================================================
echo   DevEx Studios FM Platform v0.2.5
echo   FM Operations + WhatsApp Bridge
echo  =========================================================
echo.

:: Move to the folder this bat file lives in
cd /d "%~dp0"

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Please install Python 3.10+ from https://python.org
    echo  Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  [1/3] Installing dependencies...
pip install -r requirements.txt --quiet 2>&1
if errorlevel 1 (
    echo  [WARN] pip install had errors - trying anyway
)
echo  Done.
echo.

echo  [2/3] Checking API keys...
echo.
if "%DEEPSEEK_API_KEY%"==""   echo   [WARN] DEEPSEEK_API_KEY not set
if "%GEMINI_API_KEY%"==""     echo   [WARN] GEMINI_API_KEY not set
if "%TWILIO_ACCOUNT_SID%"=""  echo   [WARN] TWILIO_ACCOUNT_SID not set
if "%TWILIO_AUTH_TOKEN%"==""  echo   [WARN] TWILIO_AUTH_TOKEN not set
if "%TWILIO_WA_FROM%"==""     echo   [WARN] TWILIO_WA_FROM not set
echo.
echo   Run SET_KEYS.bat once to save your API keys permanently.
echo.

echo  [3/3] Starting server...
echo.
echo  =========================================================
echo   Open your browser at:
echo.
echo     Landing Page   ^> http://localhost:5000
echo     FM Dashboard   ^> http://localhost:5000/fm
echo     WA Monitor     ^> http://localhost:5000/wa/monitor
echo.
echo   Press Ctrl+C to stop
echo  =========================================================
echo.

python app.py
if errorlevel 1 (
    echo.
    echo  =========================================================
    echo   [ERROR] Server crashed - see error above
    echo  =========================================================
)

echo.
echo  Press any key to close...
pause >nul
