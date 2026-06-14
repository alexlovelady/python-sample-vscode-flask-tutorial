@echo off
title AMP Titans NoteBot
call venv\Scripts\activate

echo.
echo  ==========================================
echo   AMP Titans NoteBot
echo  ==========================================
echo   Discord bot + Dashboard starting...
echo   Dashboard: http://localhost:8080
echo  ==========================================
echo.

start "NoteBot Dashboard" cmd /k "python -m dashboard.server"
timeout /t 2 >nul
python bot.py
