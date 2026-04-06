@echo off
title DevEx Studios - Set API Keys
color 1F

echo.
echo  =========================================================
echo   DevEx Studios - API Key Setup
echo   Run this once to save your keys, then use START_SERVER.bat
echo  =========================================================
echo.
echo  Keys will be saved as permanent Windows environment variables.
echo  Press Enter to skip any key you don't have yet.
echo.

set /p DEEPSEEK_KEY="  DeepSeek API Key   (from platform.deepseek.com): "
if not "%DEEPSEEK_KEY%"=="" setx DEEPSEEK_API_KEY "%DEEPSEEK_KEY%"

set /p GEMINI_KEY="  Gemini API Key     (from aistudio.google.com):    "
if not "%GEMINI_KEY%"=="" setx GEMINI_API_KEY "%GEMINI_KEY%"

set /p TWILIO_SID="  Twilio Account SID (from console.twilio.com):      "
if not "%TWILIO_SID%"=="" setx TWILIO_ACCOUNT_SID "%TWILIO_SID%"

set /p TWILIO_TOK="  Twilio Auth Token  (from console.twilio.com):      "
if not "%TWILIO_TOK%"=="" setx TWILIO_AUTH_TOKEN "%TWILIO_TOK%"

set /p TWILIO_NUM="  Twilio WA Number   (e.g. whatsapp:+14155238886):   "
if not "%TWILIO_NUM%"=="" setx TWILIO_WA_FROM "%TWILIO_NUM%"

echo.
echo  =========================================================
echo   Done. Close this window and run START_SERVER.bat
echo  =========================================================
echo.
pause
