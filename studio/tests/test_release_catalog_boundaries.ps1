param([string]$Report, [string]$CatalogPath, [switch]$CaptureBaseline)
$ErrorActionPreference='Stop'
if(-not $CatalogPath) {$CatalogPath=Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts/release_catalog.ps1'}
. $CatalogPath
$script:boundaryCatalogRows=@()
function Invoke-RestMethod {param($Uri,$TimeoutSec,$Headers,[switch]$UseBasicParsing,$Proxy)
    if($Uri -notmatch '^https://api.github.com/repos/T8mars/Comfyui-HY-Nomas-T8/releases\?per_page=100&page=1$') {throw 'Unexpected boundary catalogue request'}
    return ,$script:boundaryCatalogRows
}
function Boundary-Release($Version) {
    $assets=@()
    foreach($suffix in @('manifest.json','win64.zip')) {
        $name="Hy-Nomas-T8-Program-$Version-$suffix"
        $assets += [pscustomobject]@{name=$name;state='uploaded';size=1024;browser_download_url="https://github.com/T8mars/Comfyui-HY-Nomas-T8/releases/download/v$Version/$name"}
    }
    return [pscustomobject]@{tag_name="v$Version";draft=$false;prerelease=$false;assets=$assets}
}
function Boundary-Select {Get-ProgramRelease -Repository 'T8mars/Comfyui-HY-Nomas-T8' -WebOptions @{}}
$checks=@()
$malformed=@()
foreach($kind in @('missing-release-fields','error-object','asset-missing-name')) {
    $good=Boundary-Release '0.2.3'
    switch($kind) {
        'missing-release-fields' {$bad=[pscustomobject]@{tag_name='v0.2.4';assets=@()}}
        'error-object' {$bad=[pscustomobject]@{message='Fixture API error'}}
        'asset-missing-name' {$bad=Boundary-Release '0.2.4';$bad.assets+= [pscustomobject]@{state='uploaded';size=2;browser_download_url='https://example.invalid/fixture'}}
    }
    $script:boundaryCatalogRows=@($good,$bad)
    $rejected=$false
    try {Boundary-Select | Out-Null} catch {$rejected=$true}
    if(-not $rejected) {throw "Malformed response was silently treated as complete: $kind"}
    $malformed+=@{case=$kind;explicit_failure=$rejected;passed=$rejected}
}
$checks+=@{name='Malformed response fails explicitly without partial selection';cases=$malformed;passed=$true}
$sizeCases=@()
foreach($case in @(
    @{name='manifest-over-limit';asset=0;size=[long]8388609;expected='v0.2.3'},
    @{name='archive-over-limit';asset=1;size=[long]536870913;expected='v0.2.3'},
    @{name='manifest-numeric-string';asset=0;size='1024';expected='v0.2.3'},
    @{name='archive-double';asset=1;size=[double]1024.0;expected='v0.2.3'},
    @{name='manifest-at-limit';asset=0;size=[long]8388608;expected='v0.2.4'},
    @{name='archive-at-limit';asset=1;size=[long]536870912;expected='v0.2.4'}
)) {
    $good=Boundary-Release '0.2.3'
    $bad=Boundary-Release '0.2.4'
    $bad.assets[$case.asset].size=$case.size
    $script:boundaryCatalogRows=@($good,$bad)
    $selected=(Boundary-Select).tag_name
    $passed=$selected -eq $case.expected
    if(-not $CaptureBaseline -and -not $passed) {throw "Invalid asset metadata hid supported complete release: $($case.name)"}
    $sizeCases+=@{case=$case.name;selected=$selected;expected=$case.expected;passed=$passed}
}
$checks+=@{name='Catalogue rejects unsupported asset size/type before semantic selection';cases=$sizeCases;passed=(@($sizeCases | Where-Object {-not $_.passed}).Count -eq 0)}
$summary=@{passed=(@($checks | Where-Object {-not $_.passed}).Count -eq 0);checks=$checks;baseline_capture=[bool]$CaptureBaseline;powershell=$PSVersionTable.PSVersion.ToString()}
if($Report) {[IO.Directory]::CreateDirectory((Split-Path -Parent $Report)) | Out-Null;[IO.File]::WriteAllText($Report,($summary | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))}
$summary | ConvertTo-Json -Depth 10
