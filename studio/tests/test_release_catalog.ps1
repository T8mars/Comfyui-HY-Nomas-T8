$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\release_catalog.ps1')
$script:pages = @{}
$script:calls = [Collections.Generic.List[string]]::new()
function Invoke-RestMethod {
    param($Uri, $TimeoutSec, $Headers, [switch]$UseBasicParsing, $Proxy)
    if ($TimeoutSec -ne 15 -or $Uri -notmatch '^https://api\.github\.com/repos/T8mars/Comfyui-HY-Nomas-T8/releases\?per_page=100&page=(\d+)$') { throw 'Unexpected catalogue request.' }
    $script:calls.Add($Uri)
    $page = [int]$Matches[1]
    if ($script:pages.ContainsKey($page)) { return ,$script:pages[$page] }
    return ,@()
}
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Asset($Name, $Version, $State='uploaded', $Size=1024) {
    return [pscustomobject]@{name=$Name;state=$State;size=$Size;
        browser_download_url="https://github.com/T8mars/Comfyui-HY-Nomas-T8/releases/download/v$Version/$Name"}
}
function Release($Version, [switch]$Incomplete, [switch]$Draft, [switch]$Prerelease) {
    $assets = @(Asset "Hy-Nomas-T8-Program-$Version-manifest.json" $Version)
    if (-not $Incomplete) { $assets += Asset "Hy-Nomas-T8-Program-$Version-win64.zip" $Version }
    return [pscustomobject]@{tag_name="v$Version";draft=[bool]$Draft;prerelease=[bool]$Prerelease;assets=$assets}
}
function Reset($Rows) {
    $script:pages = @{1=@($Rows)}
    $script:calls.Clear()
}
function Select-Program { return Get-ProgramRelease -Repository 'T8mars/Comfyui-HY-Nomas-T8' -WebOptions @{} }
$passed = [Collections.Generic.List[string]]::new()

Reset @((Release '0.2.1'), (Release '0.2.3' -Incomplete), (Release '0.2.2'))
Assert ((Select-Program).tag_name -eq 'v0.2.2') 'Incomplete higher release hid the complete release.'
$passed.Add('Incomplete higher version does not hide a complete lower version')

Reset @((Release '0.2.9'), (Release '0.2.10'), (Release '0.3.0'), (Release '0.2.99'))
Assert ((Select-Program).tag_name -eq 'v0.3.0') 'Semantic version ordering failed.'
$passed.Add('Highest supported semantic version wins, not lexical or response order')

Reset @((Release '0.2.1'), (Release '99.0.0' -Draft), (Release '98.0.0' -Prerelease))
Assert ((Select-Program).tag_name -eq 'v0.2.1') 'Draft/prerelease selected.'
$passed.Add('Draft and prerelease entries excluded')

$bad = @('01.2.3','1.2','1.2.3-beta.1','1.2.3.4','99999999999999999999999.0.0','1.02.3') | ForEach-Object { Release $_ }
Reset (@((Release '0.2.1')) + @($bad))
Assert ((Select-Program).tag_name -eq 'v0.2.1') 'Malformed/unsupported version tag broke valid discovery.'
$passed.Add('Malformed, leading-zero and unsupported-overflow version tags skipped')

$pending = Release '0.4.0'; $pending.assets[1].state = 'new'
$empty = Release '0.5.0'; $empty.assets[0].size = 0
$wrongUrl = Release '0.6.0'; $wrongUrl.assets[1].browser_download_url = 'https://example.com/payload.zip'
Reset @((Release '0.2.1'), $pending, $empty, $wrongUrl)
Assert ((Select-Program).tag_name -eq 'v0.2.1') 'Pending/empty/foreign asset selected.'
$passed.Add('Both assets must be uploaded, nonempty and in the designated repository')

$duplicate = Release '0.7.0'; $duplicate.assets += $duplicate.assets[0]
Reset @((Release '0.2.1'), $duplicate)
Assert ((Select-Program).tag_name -eq 'v0.2.1') 'Duplicate manifest candidate selected.'
$passed.Add('Ambiguous duplicate asset names excluded')

$nodes = @(1..100 | ForEach-Object { [pscustomobject]@{tag_name="v2.0.$_";draft=$false;prerelease=$false;assets=@()} })
$script:pages = @{1=$nodes;2=@((Release '0.2.1'))}; $script:calls.Clear()
Assert ((Select-Program).tag_name -eq 'v0.2.1') 'Program release beyond first 100 entries missed.'
Assert ($script:calls.Count -eq 2) 'Catalogue pagination did not occur.'
$passed.Add('Program release on second page found after 100 unrelated releases')

$first = @($nodes); $first[0] = Release '0.2.1'
$script:pages = @{1=$first;2=@((Release '0.3.0'))}; $script:calls.Clear()
Assert ((Select-Program).tag_name -eq 'v0.3.0') 'Discovery stopped before a higher version on later page.'
$passed.Add('All pages are scanned before selecting the highest version')

Reset @([pscustomobject]@{tag_name='v0.2.0';draft=$false;prerelease=$false;assets=@()})
Assert ($null -eq (Select-Program)) 'Non-program release was selected.'
$passed.Add('No program releases returns null')
Reset @()
Assert ($null -eq (Select-Program)) 'Empty catalogue was not handled.'
$passed.Add('Empty catalogue returns null')

$script:pages = @{}; $script:calls.Clear()
foreach ($page in 1..20) { $script:pages[$page] = $nodes }
$rejected = $false
try { Select-Program | Out-Null } catch { $rejected = $_.Exception.Message -like '*20-page safety limit*' }
Assert $rejected 'Safety limit silently produced an incomplete selection.'
Assert ($script:calls.Count -eq 20) 'Safety limit made an unexpected request count.'
$passed.Add('Twenty full pages fail explicitly instead of silently missing later releases')

@{passed=$true;checks=$passed.Count;tests=@($passed);powershell=$PSVersionTable.PSVersion.ToString()} | ConvertTo-Json -Depth 5
