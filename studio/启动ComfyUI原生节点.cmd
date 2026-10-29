@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_app.ps1" -Automatic
if "%ERRORLEVEL%"=="32" (pause & exit /b 32)
if "%ERRORLEVEL%"=="33" (pause & exit /b 33)
set "NOMAS_PYTHON=%~dp0runtime\managed\cpython-3.12.9-windows-x86_64-none\python.exe"
if not exist "%NOMAS_PYTHON%" (
  echo 运行环境尚未安装，请先运行「安装环境.cmd」。
  pause
  exit /b 1
)
echo Hy-Nomas ComfyUI 原生节点 — By T8star
echo 打开 http://127.0.0.1:8189 ，请不要同时在两个界面运行 GPU 生成。
"%NOMAS_PYTHON%" scripts\run_comfy.py
if errorlevel 1 pause
