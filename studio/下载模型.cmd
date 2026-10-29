@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "NOMAS_PYTHON=%~dp0runtime\managed\cpython-3.12.9-windows-x86_64-none\python.exe"
if not exist "%NOMAS_PYTHON%" (
  echo 运行环境尚未安装，请先运行「安装环境.cmd」。
  pause
  exit /b 1
)
echo 下载整合包所需的三个权重，读取界面中保存的自定义路径。
echo 支持断点续传，下载完成后校验 SHA256。
"%NOMAS_PYTHON%" scripts\download_models.py
pause
