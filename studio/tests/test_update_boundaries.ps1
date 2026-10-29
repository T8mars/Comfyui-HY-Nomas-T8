param([string]$Report, [string]$CorePath, [switch]$CaptureBaseline, [string]$PythonPath, [string]$BundleRoot)
$ErrorActionPreference = 'Stop'
$boundaryReport = $Report
$boundaryBaseline = [bool]$CaptureBaseline
$boundaryCore = $CorePath
$boundaryPython = $PythonPath
$boundaryBundle = $BundleRoot
. (Join-Path $PSScriptRoot 'test_updater.ps1') -HelpersOnly
if ($boundaryCore) { . $boundaryCore }
$boundaryResults = [Collections.Generic.List[object]]::new()
$ownedLinks = [Collections.Generic.List[string]]::new()
$script:boundaryOwnedPython = $null
function Probe($Name, [scriptblock]$Action) {
    $value = & $Action
    $boundaryResults.Add([pscustomobject]@{name=$Name;result=$value})
}
try {
    Probe 'Required launch/update helper closure' {
        $f = Fixture 'boundary-helpers'
        $omitted=@('scripts/launch_app.ps1','scripts/release_catalog.ps1')
        $f.Manifest.files = @($f.Manifest.files | Where-Object { $_.path -notin $omitted })
        $index=Get-Content -LiteralPath (Join-Path $f.Input 'program-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $index.files=@($index.files | Where-Object {$_ -notin $omitted})
        Write-UpdateJson (Join-Path $f.Input 'program-files.json') $index
        $zip=[IO.Compression.ZipFile]::Open($f.Archive,[IO.Compression.ZipArchiveMode]::Update)
        try {
            foreach($name in $omitted+@('program-files.json')) {$zip.GetEntry('Hy-Nomas-By-T8star/'+$name).Delete()}
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,(Join-Path $f.Input 'program-files.json'),'Hy-Nomas-By-T8star/program-files.json') | Out-Null
        } finally {$zip.Dispose()}
        $indexSpec=$f.Manifest.files | Where-Object {$_.path -eq 'program-files.json'}
        $indexSpec.bytes=(Get-Item -LiteralPath (Join-Path $f.Input 'program-files.json')).Length
        $indexSpec.sha256=Get-ProgramHash (Join-Path $f.Input 'program-files.json')
        Archive-Metadata $f
        $accepted = $true
        try { Assert-UpdateManifest $f.Manifest } catch { $accepted = $false }
        $installedWithoutHelpers=$false
        if($accepted) {
            Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest
            $root=Old-Install 'boundary-helpers'
            foreach($name in $omitted) {Put $root $name '# old required helper'}
            $oldIndex=Get-Content -LiteralPath (Join-Path $root 'program-files.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $oldIndex.files+= $omitted
            Write-UpdateJson (Join-Path $root 'program-files.json') $oldIndex
            Install-ProgramUpdate $root $f.Stage $f.Manifest (Join-Path $root 'cache/updates/backups/missing-helpers') | Out-Null
            $installedWithoutHelpers=(-not(Test-Path -LiteralPath (Join-Path $root $omitted[0]))) -and (-not(Test-Path -LiteralPath (Join-Path $root $omitted[1])))
            Assert $installedWithoutHelpers 'Missing-helper baseline did not reproduce successful broken install'
        }
        if (-not $boundaryBaseline) { Assert (-not $accepted) 'Missing helper dependencies accepted' }
        @{accepted_missing_helpers=$accepted;successful_install_removed_required_helpers=$installedWithoutHelpers;passed=(-not $accepted)}
    }
    Probe 'Pre-existing backup subtree junction' {
        $f = Fixture 'boundary-backup-link'
        Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest
        $root = Old-Install 'boundary-backup-link'
        $backup = Join-Path $root 'cache/updates/backups/pre-existing'
        $outside = Join-Path $testRoot 'backup-outside'
        Put $outside 'a.py' 'external sentinel'
        [IO.Directory]::CreateDirectory($backup) | Out-Null
        $link = Join-Path $backup 'nomas_app'
        New-Item -ItemType Junction -Path $link -Target $outside | Out-Null
        $ownedLinks.Add($link)
        $rejected = $false
        try { Install-ProgramUpdate $root $f.Stage $f.Manifest $backup | Out-Null } catch { $rejected = $true }
        $preserved = (Get-Content -LiteralPath (Join-Path $outside 'a.py') -Raw) -eq 'external sentinel'
        if (-not $boundaryBaseline) { Assert ($rejected -and $preserved) 'Pre-existing backup junction changed external sentinel' }
        @{rejected=$rejected;external_sentinel_preserved=$preserved;passed=($rejected -and $preserved)}
    }
    Probe 'Manifest schema and updater schema JSON types' {
        $cases = @()
        foreach($field in @('schema','updater_schema')) {
            foreach($value in @($true,'1',1.0)) {
                $f = Fixture ('boundary-types-' + [guid]::NewGuid().ToString('N'))
                $f.Manifest[$field] = $value
                $rejected = $false
                try { Assert-UpdateManifest $f.Manifest } catch { $rejected = $true }
                $cases += @{field=$field;type=$value.GetType().Name;rejected=$rejected}
            }
        }
        $passed = @($cases | Where-Object { -not $_.rejected }).Count -eq 0
        if (-not $boundaryBaseline) { Assert $passed 'Noninteger format schema accepted' }
        @{cases=$cases;passed=$passed}
    }
    Probe 'Canonical installation identity through trailing separators and junction root' {
        $root = Old-Install 'boundary-root-identity'
        $alias = Join-Path $testRoot 'identity-alias'
        New-Item -ItemType Junction -Path $alias -Target $root | Out-Null
        $ownedLinks.Add($alias)
        $first = New-ProgramMutex $root
        $trail = New-ProgramMutex ($root + '\')
        $other = New-ProgramMutex $alias
        $firstHeld = $false
        try {
            $firstHeld = $first.WaitOne(0)
            # Named mutex ownership is re-entrant on this thread. Compare names via
            # a separate thread so equal names must reject concurrent ownership.
            $code = @'
using System;
using System.Threading;
public static class HyBoundaryMutexProbe {
  public static bool Available(Mutex mutex) {
    bool available=false;
    var t=new Thread(()=> { try { available=mutex.WaitOne(0); if(available) mutex.ReleaseMutex(); } catch(AbandonedMutexException) { available=true; mutex.ReleaseMutex(); } });
    t.Start(); t.Join(); return available;
  }
}
'@
            if (-not ('HyBoundaryMutexProbe' -as [type])) { Add-Type $code }
            $trailingBypass = [HyBoundaryMutexProbe]::Available($trail)
            $aliasBypass = [HyBoundaryMutexProbe]::Available($other)
            if (-not $boundaryBaseline) { Assert (-not $trailingBypass -and -not $aliasBypass) 'Installation aliases bypassed named mutex' }
            @{trailing_separator_bypasses_mutex=$trailingBypass;junction_alias_bypasses_mutex=$aliasBypass;passed=(-not $trailingBypass -and -not $aliasBypass)}
        } finally {
            if ($firstHeld) { $first.ReleaseMutex() }
            $first.Dispose();$trail.Dispose();$other.Dispose()
        }
    }
    Probe 'Unresolvable other-project process paths do not break own process guard' {
        $root=Old-Install 'boundary-unreadable-process'
        $owned=Join-Path $root 'runtime/managed/python.exe'
        $other=Join-Path $testRoot 'other-project/python.exe'
        $canonicalFunction=(Get-Command Get-ProgramCanonicalPath).ScriptBlock
        $cimFunction=Get-Item Function:Get-CimInstance -ErrorAction SilentlyContinue
        function Get-ProgramCanonicalPath($Path) {
            if($Path -eq $owned -or $Path -eq $other) {throw 'Simulated vanished/unreadable executable'}
            & $canonicalFunction $Path
        }
        function Get-CimInstance {param($ClassName,$Filter,$ErrorAction)
            return @([pscustomobject]@{ProcessId=101;ExecutablePath=$owned},[pscustomobject]@{ProcessId=102;ExecutablePath=$other})
        }
        try {
            $rows=@(Get-ProgramProcesses $root)
            $passed=$rows.Count -eq 1 -and $rows[0].ProcessId -eq 101
            Assert $passed 'Unresolvable unrelated path broke process guard or hid lexical own path'
            @{mock_only=$true;other_project_paths_not_opened_or_changed=$true;owned_lexical_prefix_still_blocked=$true;passed=$passed}
        } finally {
            Set-Item Function:Get-ProgramCanonicalPath -Value $canonicalFunction
            if($cimFunction) {Set-Item Function:Get-CimInstance -Value $cimFunction.ScriptBlock}
            else {Remove-Item Function:Get-CimInstance -ErrorAction SilentlyContinue}
        }
    }
    Probe 'Manifest file/parent ambiguity preflight' {
        $cases = @()
        foreach($path in @('nomas_app/a.py/child.json','nomas_app/A.PY/child.json')) {
            $f = Fixture ('boundary-parent-' + [guid]::NewGuid().ToString('N'))
            $f.Manifest.files += @{path=$path;bytes=0;sha256=('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')}
            $rejected=$false
            try { Assert-UpdateManifest $f.Manifest } catch { $rejected=$true }
            $cases += @{path=$path;rejected_before_stage_creation=$rejected}
        }
        $passed=@($cases | Where-Object {-not $_.rejected_before_stage_creation}).Count -eq 0
        if (-not $boundaryBaseline) { Assert $passed 'Ambiguous file and parent paths accepted' }
        @{cases=$cases;passed=$passed}
    }
    Probe 'Declared ZIP stream underflow and zero-length overflow' {
        $cases=@()
        foreach($declared in @(0,55)) {
            $f=Fixture ('boundary-stream-' + $declared)
            $entryName='Hy-Nomas-By-T8star/nomas_app/a.py'
            $bytes=[IO.File]::ReadAllBytes($f.Archive)
            $found=$false
            for($position=0;$position -le $bytes.Length-46;$position++) {
                if([BitConverter]::ToUInt32($bytes,$position) -ne 0x02014b50) {continue}
                $nameLength=[BitConverter]::ToUInt16($bytes,$position+28)
                if([Text.Encoding]::UTF8.GetString($bytes,$position+46,$nameLength) -cne $entryName) {continue}
                [Array]::Copy([BitConverter]::GetBytes([uint32]$declared),0,$bytes,$position+24,4)
                $local=[BitConverter]::ToUInt32($bytes,$position+42)
                [Array]::Copy([BitConverter]::GetBytes([uint32]$declared),0,$bytes,$local+22,4)
                $found=$true;break
            }
            Assert $found 'Could not locate isolated ZIP entry'
            [IO.File]::WriteAllBytes($f.Archive,$bytes)
            ($f.Manifest.files | Where-Object {$_.path -eq 'nomas_app/a.py'}).bytes=[long]$declared
            Archive-Metadata $f
            $rejected=$false
            try {Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest} catch {$rejected=$true}
            $partialAbsent=-not(Test-Path -LiteralPath (Join-Path $f.Stage 'nomas_app/a.py'))
            Assert ($rejected -and $partialAbsent) 'Invalid-length ZIP left incomplete output'
            $cases+=@{declared=$declared;actual=5;rejected=$rejected;partial_absent=$partialAbsent}
        }
        @{cases=$cases;passed=$true}
    }
    Probe 'Recovery schema types, array shape and file-count bound' {
        $cases=@()
        foreach($kind in @('bool-schema','string-schema','fraction-schema','scalar-files','too-many-files')) {
            $root=Old-Install ('boundary-recovery-' + $kind)
            $backup='cache/updates/backups/types'
            Put $root ($backup+'/nomas_app/a.py') 'old a'
            Put $root 'nomas_app/a.py' 'new a'
            $file=@{path='nomas_app/a.py';old_sha256=(Get-ProgramHash (Join-Path $root ($backup+'/nomas_app/a.py')));new_sha256=(Get-ProgramHash (Join-Path $root 'nomas_app/a.py'))}
            $journal=@{schema=1;app='hy-nomas-t8-studio';backup=$backup;files=@($file)}
            switch($kind) {
                'bool-schema' {$journal.schema=$true}
                'string-schema' {$journal.schema='1'}
                'fraction-schema' {$journal.schema=1.5}
                'scalar-files' {$journal.files=$file}
                'too-many-files' {$journal.files=@(1..20001 | ForEach-Object {$file})}
            }
            Write-UpdateJson (Join-Path $root 'cache/updates/transaction.json') $journal
            $rejected=$false
            try {Restore-ProgramUpdate $root | Out-Null} catch {$rejected=$_.Exception.Message -like 'RECOVERY_REQUIRED:*'}
            $unchanged=(Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'new a'
            $marker=Test-Path -LiteralPath (Join-Path $root 'cache/updates/transaction.json')
            $cases+=@{case=$kind;rejected=$rejected;program_unchanged=$unchanged;marker_retained=$marker}
        }
        $passed=@($cases | Where-Object {-not($_.rejected -and $_.program_unchanged -and $_.marker_retained)}).Count -eq 0
        if(-not $boundaryBaseline) {Assert $passed 'Malformed recovery journal mutated program or cleared marker'}
        @{cases=$cases;passed=$passed}
    }
    Probe 'Interrupted recovery is retryable and idempotent' {
        $root=Old-Install 'boundary-restore-retry'
        $backup='cache/updates/backups/retry'
        $records=@()
        foreach($letter in @('a','z')) {
            $path="nomas_app/$letter.py"
            Put $root ($backup+'/'+$path) "old $letter"
            Put $root $path "new $letter"
            $records+=@{path=$path;old_sha256=(Get-ProgramHash (Join-Path $root ($backup+'/'+$path)));new_sha256=(Get-ProgramHash (Join-Path $root $path))}
        }
        $marker=Join-Path $root 'cache/updates/transaction.json'
        Write-UpdateJson $marker @{schema=1;app='hy-nomas-t8-studio';backup=$backup;files=$records}
        $originalCopy=(Get-Command Copy-ProgramFile).ScriptBlock
        function Copy-ProgramFile($Source,$Target) {
            if($Target -eq (Join-Path $root 'nomas_app/z.py')) {throw 'Isolated interrupted recovery fault'}
            & $originalCopy $Source $Target
        }
        $blocked=$false
        try {Restore-ProgramUpdate $root | Out-Null} catch {$blocked=$_.Exception.Message -like 'RECOVERY_REQUIRED:*'}
        finally {Set-Item Function:Copy-ProgramFile -Value $originalCopy}
        $mixed=($blocked -and (Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a') -and ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/z.py') -Raw) -eq 'new z'))
        $recovered=Restore-ProgramUpdate $root
        $retryNoop= -not (Restore-ProgramUpdate $root)
        $passed=$mixed -and $recovered -and $retryNoop -and (-not(Test-Path -LiteralPath $marker)) -and ((Get-Content -LiteralPath (Join-Path $root 'nomas_app/z.py') -Raw) -eq 'old z')
        Assert $passed 'Interrupted recovery was not safely retryable'
        @{fault_left_marker=$mixed;second_attempt_recovered=$recovered;third_attempt_noop=$retryNoop;passed=$passed}
    }
    if($boundaryPython -and $boundaryBundle) {
        Probe 'Owned Python detection through real directory alias' {
            $alias=Join-Path $testRoot 'owned-bundle-alias'
            New-Item -ItemType Junction -Path $alias -Target $boundaryBundle | Out-Null
            $ownedLinks.Add($alias)
            $base=[IO.Path]::GetFullPath($boundaryBundle).TrimEnd('\')
            $python=[IO.Path]::GetFullPath($boundaryPython)
            Assert ($python.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)) 'Python must belong to supplied test bundle'
            $aliasPython=Join-Path $alias $python.Substring($base.Length+1)
            $script:boundaryOwnedPython=Start-Process -FilePath $aliasPython -ArgumentList @('-c','"import time; time.sleep(12)"') -WindowStyle Hidden -PassThru
            Start-Sleep -Milliseconds 500
            $real=@(Get-ProgramProcesses $boundaryBundle | Where-Object {$_.ProcessId -eq $script:boundaryOwnedPython.Id}).Count -eq 1
            $viaAlias=@(Get-ProgramProcesses $alias | Where-Object {$_.ProcessId -eq $script:boundaryOwnedPython.Id}).Count -eq 1
            if(-not $boundaryBaseline) {Assert ($real -and $viaAlias) 'Alias process guard missed owned Python'}
            @{physical_root_detects_owned_child=$real;alias_root_detects_owned_child=$viaAlias;no_model_or_gpu_started=$true;passed=($real -and $viaAlias)}
        }
    }
} finally {
    if($script:boundaryOwnedPython -and -not $script:boundaryOwnedPython.HasExited) {Stop-Process -Id $script:boundaryOwnedPython.Id -Force; $script:boundaryOwnedPython.WaitForExit()}
    foreach($link in $ownedLinks) { if(Test-Path -LiteralPath $link) { [IO.Directory]::Delete($link) } }
    $summary = @{passed=(@($boundaryResults | Where-Object {-not $_.result.passed}).Count -eq 0);baseline_capture=$boundaryBaseline;checks=@($boundaryResults);powershell=$PSVersionTable.PSVersion.ToString()}
    if($boundaryReport) { Write-UpdateJson $boundaryReport $summary }
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith($tempBase + '\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolved) -notmatch '^HyNomas-updater-test-[a-f0-9]{32}$') { throw 'Unsafe test cleanup' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
$summary | ConvertTo-Json -Depth 10
