@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
"%~dp0runtime\managed\cpython-3.12.9-windows-x86_64-none\python.exe" -E -s -X utf8 scripts\diagnose.py
set "NOMAS_EXIT=%ERRORLEVEL%"
pause
exit /b %NOMAS_EXIT%
