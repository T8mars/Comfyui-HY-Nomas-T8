param([string]$Report,[string]$CorePath,[switch]$CaptureBaseline)
$ErrorActionPreference='Stop'
$thirdReport=$Report;$thirdCore=$CorePath;$thirdBaseline=[bool]$CaptureBaseline
. (Join-Path $PSScriptRoot 'test_updater.ps1') -HelpersOnly
if($thirdCore){. $thirdCore}
$thirdResults=[Collections.Generic.List[object]]::new()
function Repack($Fixture){
    [IO.File]::Delete($Fixture.Archive)
    $zip=[IO.Compression.ZipFile]::Open($Fixture.Archive,[IO.Compression.ZipArchiveMode]::Create)
    try{
        foreach($file in Get-ChildItem -LiteralPath $Fixture.Input -Recurse -File){
            $name=$file.FullName.Substring($Fixture.Input.Length+1).Replace('\','/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,$file.FullName,('Hy-Nomas-By-T8star/'+$name))|Out-Null
            $entry=@($Fixture.Manifest.files|Where-Object {$_.path -ceq $name})[0]
            $entry.bytes=$file.Length;$entry.sha256=Get-ProgramHash $file.FullName
        }
    }finally{$zip.Dispose()}
    Archive-Metadata $Fixture
}
try{
    foreach($case in @('bool-payload-schema','future-payload-schema','wrong-index-version','scalar-newline-index')){
        $f=Fixture ('third-'+$case)
        if($case -eq 'bool-payload-schema'){Put $f.Input 'app-version.json' '{"app":"hy-nomas-t8-studio","version":"0.2.1","updater_schema":true}'}
        if($case -eq 'future-payload-schema'){Put $f.Input 'app-version.json' '{"app":"hy-nomas-t8-studio","version":"0.2.1","updater_schema":2}'}
        if($case -eq 'wrong-index-version'){
            $index=Get-Content -LiteralPath (Join-Path $f.Input 'program-files.json') -Raw -Encoding UTF8|ConvertFrom-Json
            $index.version='9.9.9';Put $f.Input 'program-files.json' ($index|ConvertTo-Json)
        }
        if($case -eq 'scalar-newline-index'){
            $names=@($f.Manifest.files|Where-Object {$_.path -ne 'program-files.json'}|ForEach-Object {$_.path}|Sort-Object)
            Put $f.Input 'program-files.json' (@{version='0.2.1';files=($names -join "`n")}|ConvertTo-Json)
        }
        Repack $f
        $rejected=$false;$reason=''
        try{Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest}catch{$rejected=$true;$reason=$_.Exception.Message}
        $blockedNext=$false
        if($case -eq 'scalar-newline-index' -and -not $rejected){
            $installed=Old-Install 'third-scalar-installed'
            Install-ProgramUpdate $installed $f.Stage $f.Manifest (Join-Path $installed 'cache/updates/backups/first')|Out-Null
            $next=Fixture 'third-next';Expand-ProgramUpdate $next.Archive $next.Stage $next.Manifest
            try{Install-ProgramUpdate $installed $next.Stage $next.Manifest (Join-Path $installed 'cache/updates/backups/next')|Out-Null}catch{$blockedNext=$true}
            Assert $blockedNext 'Baseline corrupt-index reproduction did not block next update'
        }
        $thirdResults.Add(@{name=$case;rejected=$rejected;reason=$reason;subsequent_install_blocked_by_bad_index=$blockedNext;passed=$rejected})
        if(-not $thirdBaseline){Assert $rejected ('Invalid payload metadata accepted: '+$case)}
    }
    $f=Fixture 'third-readonly';Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest
    $root=Old-Install 'third-readonly';$target=Join-Path $root 'nomas_app/z.py'
    [IO.File]::SetAttributes($target,[IO.FileAttributes]::ReadOnly)
    $failed=$false
    try{Install-ProgramUpdate $root $f.Stage $f.Manifest (Join-Path $root 'cache/updates/backups/readonly')|Out-Null}catch{$failed=$true}
    [IO.File]::SetAttributes($target,[IO.FileAttributes]::Normal)
    $kept=(Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a' -and (Get-Content -LiteralPath $target -Raw) -eq 'old z'
    $noJournal=-not(Test-Path -LiteralPath (Join-Path $root 'cache/updates/transaction.json'))
    Assert ($failed -and $kept -and $noJournal) 'Read-only target rollback did not preserve originals'
    $thirdResults.Add(@{name='Read-only destination fails with complete rollback';passed=$true;old_files_preserved=$kept;no_pending_journal=$noJournal})

    $f=Fixture 'third-backup-error';Expand-ProgramUpdate $f.Archive $f.Stage $f.Manifest
    $root=Old-Install 'third-backup-error';$originalCopy=${function:Copy-ProgramFile}
    function Copy-ProgramFile($Source,$Target){throw 'Injected owned-fixture backup failure before journal'}
    $failed=$false
    try{Install-ProgramUpdate $root $f.Stage $f.Manifest (Join-Path $root 'cache/updates/backups/backup-error')|Out-Null}catch{$failed=$true}
    finally{Set-Item Function:Copy-ProgramFile $originalCopy}
    $kept=(Get-Content -LiteralPath (Join-Path $root 'nomas_app/a.py') -Raw) -eq 'old a'
    $noJournal=-not(Test-Path -LiteralPath (Join-Path $root 'cache/updates/transaction.json'))
    Assert ($failed -and $kept -and $noJournal) 'Pre-journal backup failure mutated the program'
    $thirdResults.Add(@{name='Backup failure before journal preserves installed program';passed=$true;old_files_preserved=$kept;no_pending_journal=$noJournal})
}finally{
    $summary=@{passed=(@($thirdResults|Where-Object {-not $_.passed}).Count -eq 0);baseline_capture=$thirdBaseline;checks=@($thirdResults);powershell=$PSVersionTable.PSVersion.ToString()}
    if($thirdReport){Write-UpdateJson $thirdReport $summary}
    $resolved=[IO.Path]::GetFullPath($testRoot)
    if(-not $resolved.StartsWith($tempBase+'\',[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolved) -notmatch '^HyNomas-updater-test-[a-f0-9]{32}$'){throw 'Unsafe cleanup'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
$summary|ConvertTo-Json -Depth 8
