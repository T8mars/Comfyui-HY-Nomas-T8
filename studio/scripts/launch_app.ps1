param([ValidateSet('Studio','ComfyUI')][string]$Mode = 'Studio')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'
. (Join-Path $PSScriptRoot 'update_core.ps1')
$mutex = New-ProgramMutex $root
$owned = $false
$code = 0
try {
    try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { Write-Host '本整合包正在运行或更新，请先关闭原窗口。'; exit 32 }
    if (@(Get-ProgramProcesses $root).Count -gt 0) { Write-Host '本整合包的 Python 仍在运行，请先关闭原窗口。'; exit 32 }
    $global:LASTEXITCODE = 0
    & (Join-Path $PSScriptRoot 'update_app.ps1') -Automatic
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $python = Join-Path $root 'runtime\managed\cpython-3.12.9-windows-x86_64-none\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { Write-Host '运行环境尚未安装，请先补入兼容环境或运行「安装环境.cmd」。'; exit 1 }
    Write-Host 'Hy-Nomas 本地世界工作室 — By T8star'
    if ($Mode -eq 'Studio') { & $python -m nomas_app.server --open }
    else { & $python (Join-Path $root 'scripts/run_comfy.py') }
    $code = $LASTEXITCODE
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; $code = 1 }
finally { if ($owned) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
exit $code
