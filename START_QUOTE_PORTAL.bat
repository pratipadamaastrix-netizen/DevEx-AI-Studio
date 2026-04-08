@echo off
title DevEx Studios — FM + Quote Portal

echo.
echo ============================================================
echo  DevEx Studios FM Platform + Quote Portal QP1.0
echo ============================================================
echo.

REM Check for .env file
if not exist .env (
    echo [SETUP] .env not found — copying from .env.example
    copy .env.example .env
    echo.
    echo  *** IMPORTANT ***
    echo  Edit .env and add your DEEPSEEK_API_KEY and GEMINI_API_KEY
    echo  before using the AI features. The portal works without them
    echo  but will return mock/fallback AI responses.
    echo.
    pause
)

REM Install dependencies
echo [SETUP] Installing Python dependencies...
pip install -r requirements.txt --quiet

echo.
echo [START] Starting server on http://localhost:5000
echo.
echo  Quote Wizard  ^>  http://localhost:5000/quote
echo  Ops Dashboard ^>  http://localhost:5000/ops/quotes
echo  FM Console    ^>  http://localhost:5000/fm
echo.
echo  Press Ctrl+C to stop
echo.

python app.py
pause
