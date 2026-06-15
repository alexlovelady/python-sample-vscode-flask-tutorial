@echo off
title AMP Titans NoteBot
cd /d "%~dp0"
call venv\Scripts\activate

echo.
echo  ==========================================
echo   AMP Titans NoteBot
echo  ==========================================
echo   Dashboard + Bot starting...
echo   Dashboard: http://localhost:8080
echo   Recording: local mic + WASAPI loopback
echo  ==========================================
echo.

start "NoteBot Dashboard" cmd /k "cd /d "%~dp0" && call venv\Scripts\activate && python -m dashboard.server"
timeout /t 2 >nul
python recorder.py
pause
