@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_app.ps1"
set "T8_UPDATE_EXIT=%ERRORLEVEL%"
pause
exit /b %T8_UPDATE_EXIT%
