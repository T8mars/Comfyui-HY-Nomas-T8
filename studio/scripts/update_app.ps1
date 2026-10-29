param([switch]$Automatic, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
. (Join-Path $PSScriptRoot 'update_core.ps1')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = 'T8mars/Comfyui-HY-Nomas-T8'
$headers = @{ 'User-Agent' = 'Hy-Nomas-T8-Updater/1'; 'Accept' = 'application/vnd.github+json' }
$webOptions = @{ UseBasicParsing = $true; Headers = $headers }
if ($env:HTTPS_PROXY) { $webOptions.Proxy = $env:HTTPS_PROXY }
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToUpperInvariant()))).Replace('-','') }
finally { $sha.Dispose() }
$mutex = [Threading.Mutex]::new($false, "Local\HyNomasUpdate-$key")
$ownsMutex = $false
$exitCode = 0
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { Write-Host '另一个更新正在运行，请稍后再启动。'; exit 32 }
    $cache = Join-Path $root 'cache\updates'
    Assert-NoReparse $root $cache
    $statePath = Join-Path $cache 'state.json'
    $settingsPath = Join-Path $root 'config\update-settings.json'
    $interval = 24
    if (Test-Path -LiteralPath $settingsPath) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($Automatic -and $settings.PSObject.Properties.Name -contains 'auto_update' -and $settings.auto_update -eq $false) { return }
        if ($settings.PSObject.Properties.Name -contains 'interval_hours') {
            $interval = [int]$settings.interval_hours
            if ($interval -lt 1 -or $interval -gt 168) { throw '更新检查间隔必须为 1–168 小时。' }
        }
    }
    if ($Automatic -and (Test-Path -LiteralPath $statePath)) {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([DateTimeOffset]::UtcNow -lt ([DateTimeOffset]::Parse($state.checked_at).AddHours($interval))) { return }
    }
    if (-not $CheckOnly -and @(Get-ProgramProcesses $root).Count -gt 0) {
        Write-Host '当前整合包的 Python 仍在运行，暂不更新；关闭程序后再运行「更新程序.cmd」。'
        if (-not $Automatic) { throw '请先关闭当前整合包的 Python 进程。' }
        return
    }
    $versionPath = Join-Path $root 'app-version.json'
    if (-not (Test-Path -LiteralPath $versionPath)) { $versionPath = Join-Path $root 'versions.json' }
    $local = Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $current = Get-UpdateVersion $local.version
    Write-Host "Hy-Nomas T8：检查程序更新（当前 $current）…"
    $releases = @(Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases?per_page=20" -TimeoutSec 15 @webOptions)
    $candidates = @($releases | Where-Object {
        if ($_.draft -or $_.prerelease -or $_.tag_name -notmatch '^v(\d+\.\d+\.\d+)$') { return $false }
        $candidateVersion = $_.tag_name.Substring(1)
        return @($_.assets | Where-Object { $_.name -ceq "Hy-Nomas-T8-Program-$candidateVersion-manifest.json" }).Count -eq 1
    } | Sort-Object { Get-UpdateVersion $_.tag_name.Substring(1) } -Descending)
    if ($candidates.Count -eq 0) { Write-Host '暂未发布程序更新包。'; return }
    $release = $candidates[0]
    $version = $release.tag_name.Substring(1)
    $latest = Get-UpdateVersion $version
    Write-UpdateJson $statePath @{ checked_at = [DateTimeOffset]::UtcNow.ToString('o'); latest_version = $version }
    if ($latest -le $current) { Write-Host '程序已是最新版本。'; return }
    $manifestName = "Hy-Nomas-T8-Program-$version-manifest.json"
    $archiveName = "Hy-Nomas-T8-Program-$version-win64.zip"
    $manifestAssets = @($release.assets | Where-Object { $_.name -ceq $manifestName })
    $archiveAssets = @($release.assets | Where-Object { $_.name -ceq $archiveName })
    if ($manifestAssets.Count -ne 1 -or $archiveAssets.Count -ne 1) { throw '该 Release 尚无完整程序更新资产。' }
    foreach ($asset in @($manifestAssets[0], $archiveAssets[0])) {
        if ($asset.browser_download_url -cne "https://github.com/$repo/releases/download/v$version/$($asset.name)") { throw '更新地址不属于指定仓库。' }
    }
    if ($manifestAssets[0].size -gt 8388608) { throw '更新清单过大。' }
    $job = Join-Path $cache ([guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($job) | Out-Null
    $manifestPath = Join-Path $job $manifestName
    Invoke-WebRequest -Uri $manifestAssets[0].browser_download_url -OutFile $manifestPath -TimeoutSec 30 @webOptions
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-UpdateManifest $manifest
    if ($manifest.version -ne $version -or $manifest.archive.bytes -ne $archiveAssets[0].size) { throw 'Release 与清单版本/大小不匹配。' }
    $lock = (Get-FileHash -LiteralPath (Join-Path $root 'requirements.lock.txt') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($lock -ne $manifest.runtime_lock_sha256) { throw '新版本需要另一套 Python/依赖，请使用对应完整版；当前环境已保留。' }
    if ($CheckOnly) { Write-Host "发现程序版本 $version。关闭程序后运行「更新程序.cmd」即可安装。"; return }
    $archive = Join-Path $job $archiveName
    Write-Host "下载程序 $version（不下载 Python 和模型）…"
    $progress = $ProgressPreference
    try { $ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri $archiveAssets[0].browser_download_url -OutFile $archive -TimeoutSec 300 @webOptions }
    finally { $ProgressPreference = $progress }
    $stage = Join-Path $job 'stage'
    Expand-ProgramUpdate $archive $stage $manifest
    $backup = Join-Path $cache ('backups\' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
    Assert-NoReparse $root $backup
    Install-ProgramUpdate $root $stage $manifest $backup | Out-Null
    Write-UpdateJson (Join-Path $job 'installed.json') @{ version = $version; backup = $backup; installed_at = [DateTimeOffset]::UtcNow.ToString('o') }
    Write-Host "更新完成：$version。原程序备份：$backup" -ForegroundColor Green
} catch {
    Write-Host "更新未完成：$($_.Exception.Message)" -ForegroundColor Yellow
    if ($_.Exception.Message.StartsWith('ROLLBACK_INCOMPLETE:')) { $exitCode = 33 }
    elseif (-not $Automatic) { $exitCode = 1 }
    else { Write-Host '继续启动当前版本；稍后可运行「更新程序.cmd」重试。' }
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
exit $exitCode
