Set-StrictMode -Version 2.0

function Get-ProgramRelease([string]$Repository, [hashtable]$WebOptions) {
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or
        @($Repository.Split('/') | Where-Object { $_ -in @('.', '..') }).Count -gt 0) {
        throw 'Invalid update repository.'
    }
    $best = $null
    $bestVersion = $null
    for ($page = 1; $page -le 20; $page++) {
        $response = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases?per_page=100&page=$page" -TimeoutSec 15 @WebOptions
        $batch = @($response)
        foreach ($release in $batch) {
            if ($null -eq $release) { continue }
            $properties = @($release.PSObject.Properties.Name)
            if (@('draft', 'prerelease', 'tag_name', 'assets' | Where-Object { $_ -notin $properties }).Count -gt 0) {
                throw 'Malformed release catalogue response.'
            }
            if ($release.draft -or $release.prerelease -or $release.tag_name -notmatch '^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') { continue }
            $versionText = $release.tag_name.Substring(1)
            $version = [version]'0.0.0'
            if (-not [version]::TryParse($versionText, [ref]$version)) { continue }
            $manifestName = "Hy-Nomas-T8-Program-$versionText-manifest.json"
            $archiveName = "Hy-Nomas-T8-Program-$versionText-win64.zip"
            $manifest = @($release.assets | Where-Object { $_.name -ceq $manifestName })
            $archive = @($release.assets | Where-Object { $_.name -ceq $archiveName })
            if ($manifest.Count -ne 1 -or $archive.Count -ne 1) { continue }
            $complete = $true
            foreach ($asset in @($manifest[0], $archive[0])) {
                $assetProperties = @($asset.PSObject.Properties.Name)
                if (@('state', 'size', 'browser_download_url' | Where-Object { $_ -notin $assetProperties }).Count -gt 0) {
                    $complete = $false
                    break
                }
                [long]$size = 0
                if ($asset.state -cne 'uploaded' -or -not [long]::TryParse([string]$asset.size, [ref]$size) -or $size -le 0 -or
                    $asset.browser_download_url -cne "https://github.com/$Repository/releases/download/v$versionText/$($asset.name)") {
                    $complete = $false
                    break
                }
            }
            if ($complete -and ($null -eq $bestVersion -or $version.CompareTo($bestVersion) -gt 0)) {
                $best = $release
                $bestVersion = $version
            }
        }
        if ($batch.Count -lt 100) { return $best }
    }
    throw 'Release catalogue reached the 20-page safety limit; a complete scan could not be confirmed. No update selected.'
}
