Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-ProgramHash([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $hash.Dispose() }
}

function Write-UpdateJson($Path, $Value) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $Path)) | Out-Null
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally { if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) } }
}

function Get-ProgramCanonicalPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ((Test-Path -LiteralPath $full) -and -not ('HyNomasUpdateNativePaths' -as [type])) {
        Add-Type @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class HyNomasUpdateNativePaths {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint size, uint flags);
    public static string Resolve(string path) {
        using(var handle=CreateFile(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if(handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            var buffer=new StringBuilder(512);
            uint length=GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
            if(length==0) throw new Win32Exception(Marshal.GetLastWin32Error());
            if(length>=buffer.Capacity) {
                buffer=new StringBuilder(checked((int)length+1));
                length=GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
                if(length==0 || length>=buffer.Capacity) throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            string value=buffer.ToString();
            if(value.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\"+value.Substring(8);
            if(value.StartsWith(@"\\?\", StringComparison.Ordinal)) return value.Substring(4);
            return value;
        }
    }
}
'@
    }
    if (Test-Path -LiteralPath $full) { $full = [HyNomasUpdateNativePaths]::Resolve($full) }
    $volumeRoot = [IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $volumeRoot.Length) { $full = $full.TrimEnd('\','/') }
    return $full
}

function New-ProgramMutex([string]$Root) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes((Get-ProgramCanonicalPath $Root).ToUpperInvariant()))).Replace('-','') }
    finally { $sha.Dispose() }
    return [Threading.Mutex]::new($false, "Local\HyNomasUpdate-$key")
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
    if ($Name -match '\.(safetensors|ckpt|pth|pt|gguf|onnx|bin|h5|hdf5|pkl|pyc|pyo|dll|pyd)$' -or
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
    $prefix = (Get-ProgramCanonicalPath (Join-Path $Root 'runtime')).TrimEnd('\') + '\'
    return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction Stop |
        Where-Object {
            if (-not $_.ExecutablePath) { return $false }
            $executablePath = $_.ExecutablePath
            try { $executable = Get-ProgramCanonicalPath $executablePath }
            catch {
                # A different project's file can disappear or deny metadata access
                # after the process snapshot. Its path must not break this launcher.
                try { $executable = [IO.Path]::GetFullPath($executablePath) }
                catch { return $false }
            }
            $executable.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
        })
}

function Assert-UpdateManifest($Manifest) {
    if (-not ($Manifest.schema -is [int] -or $Manifest.schema -is [long]) -or $Manifest.schema -ne 1 -or
        $Manifest.app -cne 'hy-nomas-t8-studio' -or
        -not ($Manifest.updater_schema -is [int] -or $Manifest.updater_schema -is [long]) -or $Manifest.updater_schema -ne 1) { throw 'Unsupported update format.' }
    Get-UpdateVersion $Manifest.version | Out-Null
    if ($Manifest.archive.name -ne "Hy-Nomas-T8-Program-$($Manifest.version)-win64.zip" -or
        $Manifest.archive.sha256 -notmatch '^[a-f0-9]{64}$' -or
        -not ($Manifest.archive.bytes -is [int] -or $Manifest.archive.bytes -is [long]) -or
        $Manifest.archive.bytes -le 0 -or $Manifest.archive.bytes -gt 536870912) { throw 'Invalid archive metadata.' }
    if ($Manifest.runtime_lock_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Missing runtime compatibility fingerprint.' }
    if (-not ($Manifest.files -is [Collections.IList])) { throw 'Manifest files must be a JSON array.' }
    $seen = @{}
    [long]$total = 0
    foreach ($file in $Manifest.files) {
        Assert-ProgramPath $file.path
        if ($seen.ContainsKey($file.path)) { throw "Duplicate file: $($file.path)" }
        $seen[$file.path] = $true
        if ($file.sha256 -notmatch '^[a-f0-9]{64}$' -or -not ($file.bytes -is [int] -or $file.bytes -is [long]) -or
            $file.bytes -lt 0 -or $file.bytes -gt 134217728) { throw 'Invalid file metadata.' }
        $total += $file.bytes
    }
    if ($total -gt 536870912 -or $seen.Count -eq 0 -or $seen.Count -gt 10000) { throw 'Invalid expanded update size/file count.' }
    foreach ($name in $seen.Keys) {
        $parts = $name.Split('/')
        $parent = ''
        for ($i = 0; $i -lt $parts.Length - 1; $i++) {
            if ($parent) { $parent += '/' }
            $parent += $parts[$i]
            if ($seen.ContainsKey($parent)) { throw "Program file is also a parent directory: $parent" }
        }
    }
    foreach ($required in @('app-version.json','program-files.json','requirements.lock.txt','nomas_app/server.py',
        'scripts/update_app.ps1','scripts/update_core.ps1','scripts/launch_app.ps1','scripts/release_catalog.ps1',
        '启动整合包.cmd','启动ComfyUI原生节点.cmd')) {
        if (-not $seen.ContainsKey($required)) { throw "Missing required program file: $required" }
    }
}

function Expand-ProgramUpdate([string]$Archive, [string]$Stage, $Manifest) {
    Assert-UpdateManifest $Manifest
    if ((Get-Item -LiteralPath $Archive).Length -ne $Manifest.archive.bytes -or
        (Get-ProgramHash $Archive) -ne $Manifest.archive.sha256) { throw 'Archive SHA256/size mismatch.' }
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
            Assert-NoReparse $Stage $path
            [IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
            $inputStream = $null; $outputStream = $null; $complete = $false
            try {
                $inputStream = $entry.Open()
                $outputStream = [IO.File]::Create($path)
                $buffer = [byte[]]::new(65536)
                [long]$remaining = $spec.bytes
                while ($true) {
                    $read = $inputStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining + 1))
                    if ($read -eq 0) { break }
                    if ($read -gt $remaining) { throw "Expanded file exceeds declared size: $name" }
                    $outputStream.Write($buffer, 0, $read)
                    $remaining -= $read
                }
                $outputStream.Dispose(); $outputStream = $null
                if ($remaining -ne 0 -or (Get-ProgramHash $path) -ne $spec.sha256) { throw "File SHA256/size mismatch: $name" }
                $complete = $true
            } finally {
                if ($inputStream) { $inputStream.Dispose() }
                if ($outputStream) { $outputStream.Dispose() }
                if (-not $complete -and (Test-Path -LiteralPath $path -PathType Leaf)) { [IO.File]::Delete($path) }
            }
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
    $lock = (Get-ProgramHash (Join-Path $Stage 'requirements.lock.txt'))
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

function Restore-ProgramUpdate([string]$Root) {
    $journalPath = Join-Path $Root 'cache/updates/transaction.json'
    if (-not (Test-Path -LiteralPath $journalPath)) { return $false }
    try {
        Assert-NoReparse $Root $journalPath
        if (@(Get-ProgramProcesses $Root).Count -gt 0) { throw 'Close this installation before recovery.' }
        if ((Get-Item -LiteralPath $journalPath).Length -gt 8388608) { throw 'Recovery transaction is too large.' }
        $journal = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not ($journal.schema -is [int] -or $journal.schema -is [long]) -or $journal.schema -ne 1 -or
            $journal.app -cne 'hy-nomas-t8-studio' -or
            -not ($journal.files -is [Collections.IList]) -or
            $journal.backup -notmatch '^cache/updates/backups/[a-zA-Z0-9_-]+$') { throw 'Invalid recovery transaction.' }
        $seen = @{}
        if (@($journal.files).Count -eq 0 -or @($journal.files).Count -gt 20000) { throw 'Invalid recovery file count.' }
        foreach ($file in $journal.files) {
            Assert-ProgramPath $file.path
            if ($seen.ContainsKey($file.path)) { throw 'Duplicate recovery path.' }
            $seen[$file.path] = $true
            $target = Join-Path $Root $file.path
            Assert-NoReparse $Root $target
            if ($file.old_sha256) {
                if ($file.old_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid backup hash.' }
                $source = Join-Path (Join-Path $Root $journal.backup) $file.path
                Assert-NoReparse $Root $source
                if ((Get-ProgramHash $source) -ne $file.old_sha256) { throw 'Recovery backup SHA256 mismatch.' }
            }
            if ($file.new_sha256 -and $file.new_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Invalid recovery file hash.' }
            if (Test-Path -LiteralPath $target) {
                if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw 'Recovery path became a directory.' }
                $current = (Get-ProgramHash $target)
                if ($current -ne $file.old_sha256 -and $current -ne $file.new_sha256) { throw "Program changed after interrupted update: $($file.path)" }
            }
        }
        # All backups and destinations have been checked before the first restore.
        foreach ($file in $journal.files) {
            $target = Join-Path $Root $file.path
            if ($file.old_sha256) { Copy-ProgramFile (Join-Path (Join-Path $Root $journal.backup) $file.path) $target }
            elseif (Test-Path -LiteralPath $target -PathType Leaf) { [IO.File]::Delete($target) }
        }
        [IO.File]::Delete($journalPath)
        return $true
    } catch { throw "RECOVERY_REQUIRED: $($_.Exception.Message)" }
}

function Install-ProgramUpdate([string]$Root, [string]$Stage, $Manifest, [string]$Backup) {
    Assert-UpdateManifest $Manifest
    if (@(Get-ProgramProcesses $Root).Count -gt 0) { throw 'Close this installation before updating.' }
    if ((Get-ProgramHash (Join-Path $Root 'requirements.lock.txt')) -ne $Manifest.runtime_lock_sha256) {
        throw 'Current runtime dependency fingerprint is incompatible.'
    }
    $journalPath = Join-Path $Root 'cache/updates/transaction.json'
    Assert-NoReparse $Root $journalPath
    if (Test-Path -LiteralPath $journalPath) { throw 'RECOVERY_REQUIRED: previous update has not been restored.' }
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $backupPath = [IO.Path]::GetFullPath($Backup)
    if (-not $backupPath.StartsWith($base + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside this installation.' }
    $relativeBackup = $backupPath.Substring($base.Length + 1).Replace('\','/')
    if ($relativeBackup -notmatch '^cache/updates/backups/[a-zA-Z0-9_-]+$') { throw 'Invalid backup directory.' }
    Assert-NoReparse $Root $Backup
    if (Test-Path -LiteralPath $Backup) { throw 'Backup directory must be new; existing backups are never overwritten.' }
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
    $recoveryFiles = @()
    foreach ($name in $names) {
        Assert-NoReparse $Root (Join-Path $Root $name)
        $target = Join-Path $Root $name
        if (Test-Path -LiteralPath $target) {
            if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "File path is a directory: $name" }
            Copy-ProgramFile $target (Join-Path $Backup $name)
            $existing[$name] = $true
        }
        $oldHash = $null
        if ($existing.ContainsKey($name)) { $oldHash = (Get-ProgramHash (Join-Path $Backup $name)) }
        $newHash = $null
        if ($new.ContainsKey($name)) { $newHash = $new[$name].sha256 }
        $recoveryFiles += @{path=$name;old_sha256=$oldHash;new_sha256=$newHash}
    }
    Write-UpdateJson $journalPath @{schema=1;app='hy-nomas-t8-studio';version=$Manifest.version;backup=$relativeBackup;files=$recoveryFiles}
    $changed = [Collections.Generic.List[string]]::new()
    try {
        foreach ($name in @($new.Keys | Sort-Object { if ($_ -eq 'app-version.json') { 'zzzz' } else { $_ } })) {
            Assert-NoReparse $Root (Join-Path $Root $name)
            $source = Join-Path $Stage $name
            if ((Get-ProgramHash $source) -ne $new[$name].sha256) { throw 'Staged file changed before installation.' }
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
        [IO.File]::Delete($journalPath)
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
        try { [IO.File]::Delete($journalPath) } catch { throw 'RECOVERY_REQUIRED: could not clear the interrupted update record.' }
        throw "Update rolled back: $($failure.Exception.Message)"
    }
    return $Backup
}
