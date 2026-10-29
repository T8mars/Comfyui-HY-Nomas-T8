$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
$uvPath = Join-Path $packageRoot 'tools\uv.exe'
$pythonPath = Join-Path $packageRoot 'runtime\managed\cpython-3.12.9-windows-x86_64-none\python.exe'
if (-not (Test-Path -LiteralPath $uvPath)) { throw '缺少 tools\uv.exe，请解压完整整合包。' }
$uvHash = (Get-FileHash -LiteralPath $uvPath -Algorithm SHA256).Hash
if ($uvHash -ne '311BA603F935E850FFEA25CBD798D7437CA1498F30128795823F0FF4778239EA') { throw 'uv.exe 校验失败，请检查整合包完整性。' }
$env:UV_CACHE_DIR = Join-Path $packageRoot 'downloads\uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $packageRoot 'runtime\managed'
$env:UV_PYTHON_BIN_DIR = Join-Path $packageRoot 'runtime\bin'
$env:PYTHONUTF8 = '1'
$env:UV_HTTP_TIMEOUT = '1200'
$env:UV_CONCURRENT_DOWNLOADS = '4'
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'downloads\wheels') | Out-Null
Set-Location -LiteralPath $packageRoot
if ((Test-Path -LiteralPath $pythonPath) -and (Test-Path -LiteralPath (Join-Path $packageRoot 'requirements.lock.txt'))) {
    & $pythonPath scripts/check_environment.py
    if ($LASTEXITCODE -eq 0) {
        & $uvPath pip check --python $pythonPath
        if ($LASTEXITCODE -eq 0) {
            & $pythonPath scripts/diagnose.py
            if ($LASTEXITCODE -ne 0) { throw '环境诊断失败，请查看日志。' }
            Write-Host '完整环境已就绪，无需重复下载。请运行 启动整合包.cmd。' -ForegroundColor Green
            return
        }
    }
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & $uvPath python install 3.12.9
    if ($LASTEXITCODE -ne 0) { throw 'Python 安装失败，保留下载缓存后重试。' }
}
& $pythonPath -c "import importlib.metadata as m; raise SystemExit(0 if any(d.metadata.get('Name','').lower() == 'torch' and d.version == '2.10.0+cu130' for d in m.distributions()) else 1)"
if ($LASTEXITCODE -ne 0) {
    $torchWheel = Join-Path $packageRoot 'downloads\wheels\torch-2.10.0+cu130-cp312-cp312-win_amd64.whl'
    & $pythonPath scripts/range_download.py 'https://download-r2.pytorch.org/whl/cu130/torch-2.10.0%2Bcu130-cp312-cp312-win_amd64.whl' $torchWheel --size 1867405006 --sha256 224649fa0ab181ec483cc368e3303dda1760e4ba31bea806b88979f855436aaa --workers 8
    if ($LASTEXITCODE -ne 0) { throw 'PyTorch 分块下载失败，进度已保留，可重试。' }
    & $uvPath pip install --python $pythonPath --break-system-packages $torchWheel torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
} else {
    & $uvPath pip install --python $pythonPath --break-system-packages torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu130
}
if ($LASTEXITCODE -ne 0) { throw 'PyTorch 安装失败，保留下载缓存后重试。' }
$requirementsPath = Join-Path $packageRoot 'requirements.txt'
$lockedRequirements = Join-Path $packageRoot 'requirements.lock.txt'
if (Test-Path -LiteralPath $lockedRequirements) { $requirementsPath = $lockedRequirements }
if (Test-Path -LiteralPath $lockedRequirements) {
    $coreRequirements = @(Get-Content -LiteralPath $lockedRequirements | Where-Object { $_ -notmatch '^comfyui-workflow-templates' })
} else {
    $coreRequirements = @(Get-Content -LiteralPath (Join-Path $packageRoot 'runtime\ComfyUI\requirements.txt') | Where-Object { $_ -notmatch '^comfyui-workflow-templates==' })
    $coreRequirements += @(Get-Content -LiteralPath (Join-Path $packageRoot 'requirements.txt') | Where-Object { $_ -notmatch '^\s*-r\s' })
}
$coreRequirementsPath = Join-Path $packageRoot 'downloads\core-requirements.txt'
[IO.File]::WriteAllLines($coreRequirementsPath,$coreRequirements,[Text.UTF8Encoding]::new($false))
& $uvPath pip install --python $pythonPath --break-system-packages --find-links (Join-Path $packageRoot 'downloads\wheels') -r $coreRequirementsPath
if ($LASTEXITCODE -ne 0) { throw '推理核心依赖安装失败，请查看启动窗口错误。' }
Write-Host '推理核心已就绪，继续补齐完整ComfyUI模板资源。' -ForegroundColor Green
& $pythonPath scripts/fetch_template_wheels.py
if ($LASTEXITCODE -ne 0) { throw '模板资源下载失败，断点已保留，可重试。' }
& $uvPath pip install --python $pythonPath --break-system-packages --find-links (Join-Path $packageRoot 'downloads\wheels') -r $requirementsPath
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请查看启动窗口错误。' }
& $pythonPath scripts/diagnose.py
Write-Host '环境准备完成。请运行 下载模型.cmd 或在界面配置已有模型路径。' -ForegroundColor Green
