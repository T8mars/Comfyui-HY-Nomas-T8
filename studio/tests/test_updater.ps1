param([string]$Report, [string]$PythonPath, [string]$BundleRoot, [switch]$HelpersOnly)
$ErrorActionPreference = 'Stop'
$core = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\update_core.ps1'
. $core
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$testRoot = Join-Path $tempBase ('HyNomas-updater-test-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
$results = [Collections.Generic.List[string]]::new()
$child = $null
$junction = $null

function Put($Root, $Name, $Text) {
    $p = Join-Path $Root $Name
    [IO.Directory]::CreateDirectory((Split-Path -Parent $p)) | Out-Null
    [IO.File]::WriteAllText($p, $Text, [Text.UTF8Encoding]::new($false))
}
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Rejected([scriptblock]$Action, $Name) {
    $failed = $false
    try { & $Action | Out-Null } catch { $failed = $true }
    Assert $failed "Expected rejection: $Name"
    $results.Add($Name)
}
function Archive-Metadata($F) {
    $F.Manifest.archive.bytes = (Get-Item -LiteralPath $F.Archive).Length
    $F.Manifest.archive.sha256 = (Get-FileHash -LiteralPath $F.Archive -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Fixture($FixtureName) {
    $inputRoot = Join-Path $testRoot ("input-$FixtureName\Hy-Nomas-By-T8star")
    $content = @{
        'app-version.json' = '{"app":"hy-nomas-t8-studio","version":"0.2.1","updater_schema":1}'
        'nomas_app/server.py' = 'new server'; 'nomas_app/a.py' = 'new a'; 'nomas_app/z.py' = 'new z'
        'scripts/update_core.ps1' = '# new core'; 'scripts/update_app.ps1' = '# new updater'
        'scripts/launch_app.ps1' = '# new launcher'; 'scripts/release_catalog.ps1' = '# new release catalogue'
        'requirements.lock.txt' = 'fixed runtime'
        '启动整合包.cmd' = '@echo studio'; '启动ComfyUI原生节点.cmd' = '@echo comfy'
    }
    foreach ($name in $content.Keys) { Put $inputRoot $name $content[$name] }
    Put $inputRoot 'program-files.json' (@{version='0.2.1';files=@($content.Keys | Sort-Object)} | ConvertTo-Json)
    $archive = Join-Path $testRoot ("fixture-$FixtureName.zip")
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem -LiteralPath $inputRoot -File -Recurse) {
            $relative = $file.FullName.Substring($inputRoot.Length + 1).Replace('\','/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, ('Hy-Nomas-By-T8star/' + $relative)) | Out-Null
        }
    } finally { $zip.Dispose() }
    $files = @(Get-ChildItem -LiteralPath $inputRoot -File -Recurse | ForEach-Object {
        @{path=$_.FullName.Substring($inputRoot.Length + 1).Replace('\','/'); bytes=$_.Length;
          sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
    })
    $manifest = @{schema=1;app='hy-nomas-t8-studio';version='0.2.1';updater_schema=1;
        runtime_lock_sha256=(Get-FileHash -LiteralPath (Join-Path $inputRoot 'requirements.lock.txt') -Algorithm SHA256).Hash.ToLowerInvariant();
        archive=@{name='Hy-Nomas-T8-Program-0.2.1-win64.zip';bytes=0;sha256=''};files=$files}
    $f = [pscustomobject]@{Archive=$archive;Manifest=$manifest;Input=$inputRoot;Stage=(Join-Path $testRoot "stage-$FixtureName")}
    Archive-Metadata $f
    return $f
}
function Old-Install($Name) {
    $root = Join-Path $testRoot ("安装目录 空格-$Name")
    Put $root 'requirements.lock.txt' 'fixed runtime'
    Put $root 'app-version.json' '{"app":"hy-nomas-t8-studio","version":"0.2.0"}'
    Put $root 'nomas_app/a.py' 'old a'
    Put $root 'nomas_app/z.py' 'old z'
    Put $root 'nomas_app/stale.py' 'obsolete managed code'
    Put $root 'program-files.json' '{"files":["nomas_app/a.py","nomas_app/z.py","nomas_app/stale.py","app-version.json"]}'
    foreach ($name in @('models/vae/user.safetensors','runtime/managed/python.exe','config/model_paths.json',
        'outputs/my.mp4','inputs/my.png','runtime/ComfyUI/user/workflows/mine.json',
        'runtime/ComfyUI/custom_nodes/OtherNode/node.py','custom_nodes/OtherNode/node.py')) { Put $root $name "user data $name" }
    return $root
}

if ($HelpersOnly) { return }

try {
    foreach ($name in @('../escape.py','nomas_app/../escape.py','/root.py','nomas_app/A:stream.py','nomas_app/CON.py',
        'nomas_app/bad.','nomas_app/back\slash.py','models/a.safetensors','runtime/managed/python.exe',
        'config/model_paths.json','outputs/video.mp4','runtime/ComfyUI/user/a.json','nomas_app/weights.bin','scripts/python.exe',
        'runtime/ComfyUI/custom_nodes/Mine/a.py','runtime/ComfyUI/extra_model_paths.yaml')) {
        Rejected { Assert-ProgramPath $name } "protected/unsafe path $name"
    }
    $valid = Fixture 'valid'
    Expand-ProgramUpdate $valid.Archive $valid.Stage $valid.Manifest
    $root = Old-Install 'success'
    $sentinels = @{}
    Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object { $_.FullName -notmatch 'nomas_app|program-files.json|app-version.json' } | ForEach-Object {
        $sentinels[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    $backup = Join-Path $root 'cache/updates/backups/success'
    Install-ProgramUpdate $root $valid.Stage $valid.Manifest $backup | Out-Null
    Assert ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'new a') 'New program not installed'
    Assert ((Get-Content -LiteralPath (Join-Path $backup 'nomas_app/a.py') -Raw) -eq 'old a') 'Old program not backed up'
    Assert (-not (Test-Path -LiteralPath (Join-Path $root 'nomas_app/stale.py'))) 'Stale managed code not removed'
    foreach ($path in $sentinels.Keys) { Assert ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $sentinels[$path]) "User data changed: $path" }
    $results.Add('Install in Chinese/space path; preserve weights/Python/config/outputs/other nodes; backup and stale removal')
    $bad = Fixture 'bad-archive'; $bad.Manifest.archive.sha256 = '0' * 64
    Rejected { Expand-ProgramUpdate $bad.Archive $bad.Stage $bad.Manifest } 'Wrong archive SHA256'
    $bad = Fixture 'bad-file'; $bad.Manifest.files[0].sha256 = '0' * 64
    Rejected { Expand-ProgramUpdate $bad.Archive $bad.Stage $bad.Manifest } 'Wrong per-file SHA256'
    $bad = Fixture 'duplicate'; $bad.Manifest.files += $bad.Manifest.files[0]
    Rejected { Assert-UpdateManifest $bad.Manifest } 'Duplicate manifest path'
    foreach ($value in @('1',1.5,$true)) {
        $bad = Fixture ('typed-size-' + [guid]::NewGuid().ToString('N'))
        $bad.Manifest.files[0].bytes = $value
        Rejected { Assert-UpdateManifest $bad.Manifest } ('Noninteger manifest byte size: ' + $value)
    }
    $bad = Fixture 'zip-slip'
    $z = [IO.Compression.ZipFile]::Open($bad.Archive, [IO.Compression.ZipArchiveMode]::Update)
    try { $e=$z.CreateEntry('Hy-Nomas-By-T8star/../escape.py'); $s=$e.Open(); $s.Dispose() } finally { $z.Dispose() }
    Archive-Metadata $bad
    Rejected { Expand-ProgramUpdate $bad.Archive $bad.Stage $bad.Manifest } 'ZIP traversal'
    Assert (-not (Test-Path -LiteralPath (Join-Path $testRoot 'escape.py'))) 'ZIP escaped staging'
    $bad = Fixture 'unlisted'
    $z = [IO.Compression.ZipFile]::Open($bad.Archive, [IO.Compression.ZipArchiveMode]::Update)
    try { $e=$z.CreateEntry('Hy-Nomas-By-T8star/nomas_app/unlisted.py'); $s=$e.Open(); $s.Dispose() } finally { $z.Dispose() }
    Archive-Metadata $bad
    Rejected { Expand-ProgramUpdate $bad.Archive $bad.Stage $bad.Manifest } 'Unlisted ZIP file'
    $bad = Fixture 'symlink'
    $z = [IO.Compression.ZipFile]::Open($bad.Archive, [IO.Compression.ZipArchiveMode]::Update)
    try { $entry=$z.GetEntry('Hy-Nomas-By-T8star/nomas_app/a.py'); $entry.ExternalAttributes=-1610612736 } finally { $z.Dispose() }
    Archive-Metadata $bad
    Rejected { Expand-ProgramUpdate $bad.Archive $bad.Stage $bad.Manifest } 'Symbolic ZIP entry'
    $root = Old-Install 'incompatible'; Put $root 'requirements.lock.txt' 'different environment'
    Rejected { Install-ProgramUpdate $root $valid.Stage $valid.Manifest (Join-Path $root 'cache/updates/backups/incompatible') } 'Incompatible runtime lock'
    $root = Old-Install 'rollback'
    $locked = [IO.File]::Open((Join-Path $root 'nomas_app/z.py'), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try { Rejected { Install-ProgramUpdate $root $valid.Stage $valid.Manifest (Join-Path $root 'cache/updates/backups/rollback') } 'Locked destination triggers rollback' }
    finally { $locked.Dispose() }
    Assert ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a') 'Rollback failed for already replaced file'
    Assert ((Get-Content -LiteralPath (Join-Path $root 'app-version.json') -Raw | ConvertFrom-Json).version -eq '0.2.0') 'Version changed after rollback'
    Assert (-not (Test-Path -LiteralPath (Join-Path $root 'nomas_app/server.py'))) 'New file survived rollback'
    $results.Add('Rollback restores replaced code and old version; removes newly added files')
    $tampered = Fixture 'stage-tamper'
    Expand-ProgramUpdate $tampered.Archive $tampered.Stage $tampered.Manifest
    Put $tampered.Stage 'nomas_app/a.py' 'changed after extraction'
    $root = Old-Install 'stage-tamper'
    Rejected { Install-ProgramUpdate $root $tampered.Stage $tampered.Manifest (Join-Path $root 'cache/updates/backups/stage-tamper') } 'Staged file changes after verification'
    Assert ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a') 'Tampered stage damaged current program'
    Assert (-not (Test-Path -LiteralPath (Join-Path $root 'cache/updates/transaction.json'))) 'Successful rollback left a pending transaction'
    $root = Old-Install 'junction'
    Remove-Item -LiteralPath (Join-Path $root 'nomas_app/a.py'),(Join-Path $root 'nomas_app/z.py'),(Join-Path $root 'nomas_app/stale.py') -Force
    [IO.Directory]::Delete((Join-Path $root 'nomas_app'))
    $outside = Join-Path $testRoot 'outside'; [IO.Directory]::CreateDirectory($outside) | Out-Null
    $junction = Join-Path $root 'nomas_app'
    New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null
    Rejected { Install-ProgramUpdate $root $valid.Stage $valid.Manifest (Join-Path $root 'cache/updates/backups/junction') } 'Linked destination'
    [IO.Directory]::Delete($junction); $junction=$null
    if ($PythonPath -and $BundleRoot) {
        $child = Start-Process -FilePath $PythonPath -ArgumentList @('-c','"import time; time.sleep(45)"') -WindowStyle Hidden -PassThru
        Start-Sleep -Milliseconds 600
        Assert (@(Get-ProgramProcesses $BundleRoot | Where-Object { $_.ProcessId -eq $child.Id }).Count -eq 1) 'Running bundled Python not detected'
        $results.Add('Real Windows process detection for this bundle; only owned test child is stopped')
    }
    $summary = @{passed=$true;checks=$results.Count;tests=@($results);powershell=$PSVersionTable.PSVersion.ToString()}
    if ($Report) { Write-UpdateJson $Report $summary }
    $summary | ConvertTo-Json -Depth 5
} finally {
    if ($child -and -not $child.HasExited) { Stop-Process -Id $child.Id -Force }
    if ($junction -and (Test-Path -LiteralPath $junction)) { [IO.Directory]::Delete($junction) }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith($tempBase + '\', [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolved) -notmatch '^HyNomas-updater-test-[a-f0-9]{32}$') { throw 'Refuse cleanup outside test directory' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
