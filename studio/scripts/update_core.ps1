Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Write-UpdateJson($Path, $Value) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    [IO.File]::WriteAllText($Path, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
}

function Get-UpdateVersion($Value) {
    if ($Value -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') { throw 'Invalid program version.' }
    return [version]$Value
}

function Assert-ProgramPath([string]$Name) {
    if (-not $Name -or $Name.Contains('\') -or $Name.StartsWith('/') -or $Name.Contains(':')) { throw "Invalid path: $Name" }
    foreach ($part in $Name.Split('/')) {
        if ($part -in @('', '.', '..') -or $part -match '[<>"|?*\x00-\x1f]' -or $part -match '[. ]$' -or
            $part -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') { throw "Unsafe path: $Name" }
    }
    $allowed = $Name -match '^(nomas_app/|scripts/|licenses/|docs/|custom_nodes/ComfyUI-HyNomas-T8/|runtime/ComfyUI/)' -or
        $Name -in @('LICENSE','NOTICE','README.zh-CN.md','README.en.md','BUILD.md','versions.json','app-version.json',
            'program-files.json','models.manifest.json','requirements.txt','requirements.lock.txt',
            'tools/uv.exe','启动整合包.cmd','启动ComfyUI原生节点.cmd','安装环境.cmd','环境诊断.cmd',
            '下载模型.cmd','校验模型.cmd','更新程序.cmd','检查更新.cmd')
    if (-not $allowed) { throw "Not a managed program path: $Name" }
    if ($Name -match '\.(safetensors|ckpt|pth|pt|gguf|onnx|h5|hdf5|pkl|pyc|pyo|dll|pyd)$' -or
        ($Name -match '\.exe$' -and $Name -ne 'tools/uv.exe') -or
        $Name -match '(^|/)(models|managed|python_embeded|python_embedded|user|input|output|inputs|outputs|config|cache|logs|downloads|temp|\.git)(/|$)' -or
        $Name -match '^runtime/ComfyUI/custom_nodes/' -or
        $Name -eq 'runtime/ComfyUI/extra_model_paths.yaml') { throw "Protected data/runtime path: $Name" }
}

function Assert-NoReparse([string]$Root, [string]$Path) {
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\','/')
    $current = [IO.Path]::GetFullPath($Path)
    if ($current -ne $base -and -not $current.StartsWith($base + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Destination escapes installation.'
    }
    while ($true) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked destination refused: $current" }
        }
        if ($current -eq $base) { break }
        $current = Split-Path -Parent $current
    }
}

function Get-ProgramProcesses([string]$Root) {
    $prefix = [IO.Path]::GetFullPath((Join-Path $Root 'runtime')).TrimEnd('\') + '\'
    return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction Stop |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) })
}

function Assert-UpdateManifest($Manifest) {
    if ($Manifest.schema -ne 1 -or $Manifest.app -ne 'hy-nomas-t8-studio' -or $Manifest.updater_schema -ne 1) { throw 'Unsupported update format.' }
    Get-UpdateVersion $Manifest.version | Out-Null
    if ($Manifest.archive.name -ne "Hy-Nomas-T8-Program-$($Manifest.version)-win64.zip" -or
        $Manifest.archive.sha256 -notmatch '^[a-f0-9]{64}$' -or
        $Manifest.archive.bytes -le 0 -or $Manifest.archive.bytes -gt 536870912) { throw 'Invalid archive metadata.' }
    if ($Manifest.runtime_lock_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Missing runtime compatibility fingerprint.' }
    $seen = @{}
    [long]$total = 0
    foreach ($file in $Manifest.files) {
        Assert-ProgramPath $file.path
        if ($seen.ContainsKey($file.path)) { throw "Duplicate file: $($file.path)" }
        $seen[$file.path] = $true
        if ($file.sha256 -notmatch '^[a-f0-9]{64}$' -or $file.bytes -lt 0 -or $file.bytes -gt 134217728) { throw 'Invalid file metadata.' }
        $total += $file.bytes
    }
    if ($total -gt 536870912 -or $seen.Count -eq 0) { throw 'Invalid expanded update size.' }
    foreach ($required in @('app-version.json','program-files.json','requirements.lock.txt','nomas_app/server.py',
        'scripts/update_app.ps1','scripts/update_core.ps1','启动整合包.cmd','启动ComfyUI原生节点.cmd')) {
        if (-not $seen.ContainsKey($required)) { throw "Missing required program file: $required" }
    }
}

function Expand-ProgramUpdate([string]$Archive, [string]$Stage, $Manifest) {
    Assert-UpdateManifest $Manifest
    if ((Get-Item -LiteralPath $Archive).Length -ne $Manifest.archive.bytes -or
        (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Manifest.archive.sha256) { throw 'Archive SHA256/size mismatch.' }
    if (Test-Path -LiteralPath $Stage) { throw 'Staging directory already exists.' }
    [IO.Directory]::CreateDirectory($Stage) | Out-Null
    $expected = @{}
    foreach ($f in $Manifest.files) { $expected[$f.path] = $f }
    $seen = @{}
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            # The release format contains files only, with exactly one fixed root.
            if (-not $entry.FullName.StartsWith('Hy-Nomas-By-T8star/', [StringComparison]::Ordinal)) { throw 'Unexpected archive root.' }
            $name = $entry.FullName.Substring('Hy-Nomas-By-T8star/'.Length)
            Assert-ProgramPath $name
            if ($seen.ContainsKey($name) -or -not $expected.ContainsKey($name)) { throw "Unlisted/duplicate ZIP entry: $name" }
            if (($entry.ExternalAttributes -shr 16 -band 61440) -eq 40960) { throw 'Symbolic ZIP entry refused.' }
            $spec = $expected[$name]
            if ($entry.Length -ne $spec.bytes) { throw "ZIP size mismatch: $name" }
            $path = Join-Path $Stage $name
            [IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
            $inputStream = $entry.Open()
            $outputStream = [IO.File]::Create($path)
            try { $inputStream.CopyTo($outputStream) } finally { $inputStream.Dispose(); $outputStream.Dispose() }
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $spec.sha256) { throw "File SHA256 mismatch: $name" }
            $seen[$name] = $true
        }
    } finally { $zip.Dispose() }
    if ($seen.Count -ne $expected.Count) { throw 'Incomplete archive.' }
    $version = Get-Content -LiteralPath (Join-Path $Stage 'app-version.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($version.version -ne $Manifest.version -or $version.app -ne $Manifest.app) { throw 'Payload version mismatch.' }
    $index = Get-Content -LiteralPath (Join-Path $Stage 'program-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $indexed = @($index.files | Sort-Object)
    $listed = @($expected.Keys | Where-Object { $_ -ne 'program-files.json' } | Sort-Object)
    if (($indexed -join "`n") -cne ($listed -join "`n")) { throw 'Program file index mismatch.' }
    $lock = (Get-FileHash -LiteralPath (Join-Path $Stage 'requirements.lock.txt') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($lock -ne $Manifest.runtime_lock_sha256) { throw 'Payload runtime fingerprint mismatch.' }
}

function Copy-ProgramFile([string]$Source, [string]$Target) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Target)) | Out-Null
    $temporary = $Target + '.update-' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::Copy($Source, $temporary, $false)
        if (Test-Path -LiteralPath $Target -PathType Leaf) { [IO.File]::Replace($temporary, $Target, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Target) }
    } finally { if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) } }
}

function Install-ProgramUpdate([string]$Root, [string]$Stage, $Manifest, [string]$Backup) {
    Assert-UpdateManifest $Manifest
    if (@(Get-ProgramProcesses $Root).Count -gt 0) { throw 'Close this installation before updating.' }
    if ((Get-FileHash -LiteralPath (Join-Path $Root 'requirements.lock.txt') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Manifest.runtime_lock_sha256) {
        throw 'Current runtime dependency fingerprint is incompatible.'
    }
    $new = @{}
    foreach ($f in $Manifest.files) { $new[$f.path] = $f }
    $old = @()
    $indexPath = Join-Path $Root 'program-files.json'
    if (Test-Path -LiteralPath $indexPath) {
        $old = @((Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8 | ConvertFrom-Json).files)
        foreach ($name in $old) { Assert-ProgramPath $name }
    }
    $names = @(@($new.Keys) + $old | Sort-Object -Unique)
    $existing = @{}
    foreach ($name in $names) {
        Assert-NoReparse $Root (Join-Path $Root $name)
        $target = Join-Path $Root $name
        if (Test-Path -LiteralPath $target) {
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "File path is a directory: $name" }
            Copy-ProgramFile $target (Join-Path $Backup $name)
            $existing[$name] = $true
        }
    }
    $changed = [Collections.Generic.List[string]]::new()
    try {
        foreach ($name in @($new.Keys | Sort-Object { if ($_ -eq 'app-version.json') { 'zzzz' } else { $_ } })) {
            Assert-NoReparse $Root (Join-Path $Root $name)
            $source = Join-Path $Stage $name
            if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $new[$name].sha256) { throw 'Staged file changed before installation.' }
            Copy-ProgramFile $source (Join-Path $Root $name)
            $changed.Add($name)
        }
        foreach ($name in $old) {
            if (-not $new.ContainsKey($name) -and $existing.ContainsKey($name)) {
                Assert-NoReparse $Root (Join-Path $Root $name)
                [IO.File]::Delete((Join-Path $Root $name))
                $changed.Add($name)
            }
        }
    } catch {
        $failure = $_
        $rollbackFailed = $false
        for ($i = $changed.Count - 1; $i -ge 0; $i--) {
            $name = $changed[$i]
            try {
                if ($existing.ContainsKey($name)) { Copy-ProgramFile (Join-Path $Backup $name) (Join-Path $Root $name) }
                else { [IO.File]::Delete((Join-Path $Root $name)) }
            } catch { $rollbackFailed = $true }
        }
        if ($rollbackFailed) { throw "ROLLBACK_INCOMPLETE: restore program files from $Backup" }
        throw "Update rolled back: $($failure.Exception.Message)"
    }
    return $Backup
}
