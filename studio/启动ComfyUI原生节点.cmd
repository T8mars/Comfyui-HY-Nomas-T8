@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_app.ps1" -Mode ComfyUI
set "T8_EXIT=%ERRORLEVEL%"
if not "%T8_EXIT%"=="0" pause
exit /b %T8_EXIT%
