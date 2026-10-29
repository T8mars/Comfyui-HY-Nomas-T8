param([string]$Report)
$ErrorActionPreference = 'Stop'
$recoveryReportPath = $Report
. (Join-Path $PSScriptRoot 'test_updater.ps1') -HelpersOnly
$production = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts'
$summary = @{passed=$false;checks=@()}
try {
    $fixture = Fixture 'recovery'
    Expand-ProgramUpdate $fixture.Archive $fixture.Stage $fixture.Manifest
    $root = Old-Install 'recovery'
    [IO.Directory]::CreateDirectory((Join-Path $root 'scripts')) | Out-Null
    foreach ($name in @('update_core.ps1','update_app.ps1','release_catalog.ps1')) { Copy-Item -LiteralPath (Join-Path $production $name) -Destination (Join-Path $root "scripts\$name") -Force }
    Put $root 'config/update-settings.json' '{"auto_update":false}'
    Write-UpdateJson (Join-Path $root 'cache/updates/state.json') @{checked_at=[DateTimeOffset]::UtcNow.ToString('o');latest_version='0.2.1'}
    $backup = Join-Path $root 'cache/updates/backups/recovery-case'
    $originalCopy = (Get-Command Copy-ProgramFile).ScriptBlock
    function Copy-ProgramFile($Source, $Target) {
        if ($Target -eq (Join-Path $root 'nomas_app/z.py')) { throw 'Injected installation failure' }
        if ($Target -eq (Join-Path $root 'nomas_app/a.py') -and $Source.StartsWith($backup)) { throw 'Injected rollback failure' }
        & $originalCopy $Source $Target
    }
    $incomplete = $false
    try { Install-ProgramUpdate $root $fixture.Stage $fixture.Manifest $backup | Out-Null }
    catch { $incomplete = $_.Exception.Message -match 'ROLLBACK_INCOMPLETE|RECOVERY_REQUIRED' }
    finally { Set-Item Function:Copy-ProgramFile -Value $originalCopy }
    Assert $incomplete 'Double failure was not reproduced'
    Assert ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'new a') 'Failure injection did not leave the replaced file'
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts/update_app.ps1') -Automatic
    Assert ($LASTEXITCODE -eq 0) 'Recovery prevented returning to the restored version'
    Assert ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a') 'Disabled/cached startup ignored an incomplete update'
    Assert (-not (Test-Path -LiteralPath (Join-Path $root 'cache/updates/transaction.json'))) 'Recovered transaction remains pending'
    $summary.checks += 'Persistent recovery runs before disabled automatic-update settings and daily cache'
    # A malformed transaction must block launch, even with updates disabled.
    Put $root 'cache/updates/transaction.json' '{broken'
    & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts/update_app.ps1') -Automatic
    Assert ($LASTEXITCODE -eq 33) 'Malformed recovery transaction allowed launch'
    $summary.checks += 'Corrupt recovery state blocks launch with exit 33'
    $summary.passed = $true
} finally {
    if ($recoveryReportPath) { Write-UpdateJson $recoveryReportPath $summary }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith($tempBase + '\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolved) -notmatch '^HyNomas-updater-test-[a-f0-9]{32}$') { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
$summary | ConvertTo-Json -Depth 5
