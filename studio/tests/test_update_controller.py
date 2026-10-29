"""Exercise controller caching with offline GitHub responses, without installing code."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
BASELINE = None
PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
DRIVER = r'''
param([string]$Mode)
Import-Module Microsoft.PowerShell.Utility -Force
$global:fixtureRoot = $PSScriptRoot
# Supply the legacy cmdlet faithfully when HTTP is mocked and cannot autoload it.
function global:Get-FileHash {
    param($LiteralPath,$Algorithm)
    $hash=[Security.Cryptography.SHA256]::Create()
    $stream=[IO.File]::OpenRead($LiteralPath)
    try { return [pscustomobject]@{Hash=[BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-','')} }
    finally { $stream.Dispose(); $hash.Dispose() }
}
function global:Invoke-RestMethod {
    param($Uri,$TimeoutSec,$Headers,$Proxy,[switch]$UseBasicParsing)
    Add-Content -LiteralPath (Join-Path $global:fixtureRoot 'calls.log') -Value ('GET '+$Uri)
    return Get-Content -LiteralPath (Join-Path $global:fixtureRoot 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
}
function global:Invoke-WebRequest {
    param($Uri,$OutFile,$TimeoutSec,$Headers,$Proxy,[switch]$UseBasicParsing)
    Add-Content -LiteralPath (Join-Path $global:fixtureRoot 'calls.log') -Value ('FILE '+$Uri)
    if ($Uri.EndsWith('-manifest.json')) { Copy-Item -LiteralPath (Join-Path $global:fixtureRoot 'manifest.json') -Destination $OutFile; return }
    throw 'SIMULATED_ARCHIVE_DOWNLOAD_FAILURE'
}
if ($Mode -eq 'check') { & (Join-Path $PSScriptRoot 'scripts/update_app.ps1') -CheckOnly }
else { & (Join-Path $PSScriptRoot 'scripts/update_app.ps1') -Automatic }
exit $LASTEXITCODE
'''


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.gettempdir()).resolve()
        self.root = Path(tempfile.mkdtemp(prefix="HyNomas-controller-test-", dir=self.base)).resolve()
        (self.root / "scripts").mkdir()
        for name in ['update_app.ps1','update_core.ps1','release_catalog.ps1']:
            if BASELINE and name != 'release_catalog.ps1':
                data = subprocess.check_output(['git','show',f'{BASELINE}:studio/scripts/{name}'], cwd=STUDIO.parent)
            else:
                data = (STUDIO / 'scripts' / name).read_bytes()
            (self.root / 'scripts' / name).write_bytes(data)
        (self.root / 'requirements.lock.txt').write_bytes(b'fixed runtime')
        (self.root / 'app-version.json').write_text('{"version":"0.2.0"}', encoding='utf-8')
        required = ['app-version.json','program-files.json','requirements.lock.txt','nomas_app/server.py',
                    'scripts/update_app.ps1','scripts/update_core.ps1','scripts/launch_app.ps1',
                    'scripts/release_catalog.ps1','启动整合包.cmd','启动ComfyUI原生节点.cmd']
        manifest = dict(schema=1,app='hy-nomas-t8-studio',version='0.2.1',updater_schema=1,
                        runtime_lock_sha256=hashlib.sha256(b'fixed runtime').hexdigest(),
                        archive=dict(name='Hy-Nomas-T8-Program-0.2.1-win64.zip',bytes=1,sha256='0'*64),
                        files=[dict(path=name,bytes=0,sha256='0'*64) for name in required])
        (self.root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        prefix = 'https://github.com/T8mars/Comfyui-HY-Nomas-T8/releases/download/v0.2.1/'
        names = ['Hy-Nomas-T8-Program-0.2.1-manifest.json','Hy-Nomas-T8-Program-0.2.1-win64.zip']
        release = dict(tag_name='v0.2.1', draft=False, prerelease=False,
                       assets=[dict(name=n,state='uploaded',size=1,browser_download_url=prefix+n) for n in names])
        release['assets'][0]['size'] = (self.root / 'manifest.json').stat().st_size
        (self.root / 'release.json').write_text(json.dumps([release]), encoding='utf-8')
        (self.root / 'driver.ps1').write_text(DRIVER, encoding='utf-8-sig')

    def tearDown(self):
        assert self.root.parent == self.base and self.root.name.startswith('HyNomas-controller-test-')
        shutil.rmtree(self.root)

    def run_mode(self, mode):
        result = subprocess.run([PS,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'driver.ps1'),mode],
                                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def state(self, latest='0.2.0', days=0):
        path = self.root / 'cache/updates/state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(checked_at=(datetime.now(timezone.utc)+timedelta(days=days)).isoformat(),latest_version=latest)), encoding='utf-8')

    def test_check_only_does_not_delay_a_pending_automatic_install(self):
        self.run_mode('check')
        self.run_mode('auto')
        log = (self.root/'calls.log').read_text(encoding='utf-8-sig')
        self.assertIn('win64.zip',log,'Known available update was suppressed by the check-only cache')

    def test_fresh_current_version_cache_avoids_network(self):
        self.state()
        self.run_mode('auto')
        self.assertFalse((self.root/'calls.log').exists())

    def test_corrupt_check_cache_is_discarded(self):
        self.state()
        (self.root/'cache/updates/state.json').write_text('{broken',encoding='utf-8')
        self.run_mode('auto')
        self.assertTrue((self.root/'calls.log').exists(),'Corrupt cache permanently disabled update checks')

    def test_future_check_timestamp_does_not_suppress_updates(self):
        self.state(days=365)
        self.run_mode('auto')
        self.assertTrue((self.root/'calls.log').exists(),'Future cache date suppressed all checks for a year')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-ref')
    parser.add_argument('--report')
    args = parser.parse_args()
    BASELINE = args.baseline_ref
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControllerTests))
    if args.report:
        path = Path(args.report); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(passed=result.wasSuccessful(),tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),baseline=BASELINE),indent=2),encoding='utf-8')
    raise SystemExit(not result.wasSuccessful())
