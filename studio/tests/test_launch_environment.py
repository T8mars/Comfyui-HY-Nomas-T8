"""Real bundled Python, isolated tiny modules, deliberately conflicting child env only."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from test_launch_lifetime import copy_test_runtime, STUDIO, REPO


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-ref')
    parser.add_argument('--report')
    args=parser.parse_args()
    base=Path(tempfile.gettempdir()).resolve()
    root=Path(tempfile.mkdtemp(prefix='HyNomas-env-test-',dir=base)).resolve()
    checks=[]
    try:
        for name in ['update_core.ps1','release_catalog.ps1','update_app.ps1','launch_app.ps1']:
            path=root/'scripts'/name;path.parent.mkdir(exist_ok=True)
            data=subprocess.check_output(['git','show',f'{args.baseline_ref}:studio/scripts/{name}'],cwd=REPO) if args.baseline_ref else (STUDIO/'scripts'/name).read_bytes()
            path.write_bytes(data)
        (root/'config').mkdir()
        (root/'config/update-settings.json').write_text('{"auto_update":false}',encoding='utf-8')
        runtime=root/'runtime/managed/cpython-3.12.9-windows-x86_64-none'
        copy_test_runtime(Path(sys.executable).resolve(),runtime)
        marker=root/'started.json'
        (root/'nomas_app').mkdir()
        (root/'nomas_app/__init__.py').write_text('',encoding='utf-8')
        code=f"import json,sys\nopen({str(marker)!r},'w').write(json.dumps({{'isolated_env':bool(sys.flags.ignore_environment),'no_user_site':bool(sys.flags.no_user_site)}}))\n"
        (root/'nomas_app/server.py').write_text(code,encoding='utf-8')
        (root/'scripts/run_comfy.py').write_text(code,encoding='utf-8')
        (root/'scripts/download_models.py').write_text(code,encoding='utf-8')
        (root/'scripts/diagnose.py').write_text(code,encoding='utf-8')
        foreign=root/'unrelated python path';foreign.mkdir()
        (foreign/'json.py').write_text("raise RuntimeError('Foreign PYTHONPATH shadowed pinned stdlib')\n",encoding='utf-8')
        for name,mode,changes in [
            ('valid environment','Studio',{}),
            ('foreign PYTHONHOME','Studio',{'PYTHONHOME':str(root/'missing python 3.11')}),
            ('foreign PYTHONPATH','ComfyUI',{'PYTHONPATH':str(foreign)}),
        ]:
            marker.unlink(missing_ok=True)
            env=os.environ.copy();env.pop('PYTHONHOME',None);env.pop('PYTHONPATH',None);env.update(changes)
            result=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'scripts/launch_app.ps1'),'-Mode',mode],
                                  env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
            checks.append(dict(name=name,exit_code=result.returncode,started=marker.exists(),flags=json.loads(marker.read_text()) if marker.exists() else None,
                               passed=result.returncode==0 and marker.exists()))
        for name in ['下载模型.cmd','校验模型.cmd','环境诊断.cmd']:
            data=subprocess.check_output(['git','show',f'{args.baseline_ref}:studio/{name}'],cwd=REPO) if args.baseline_ref else (STUDIO/name).read_bytes()
            path=root/name;path.write_bytes(data);marker.unlink(missing_ok=True)
            env=os.environ.copy();env.update(PYTHONHOME=str(root/'missing python 3.11'),PYTHONPATH=str(foreign))
            result=subprocess.run(['cmd.exe','/d','/c',str(path)],env=env,input='\n',capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
            checks.append(dict(name='Helper '+name,exit_code=result.returncode,started=marker.exists(),flags=json.loads(marker.read_text()) if marker.exists() else None,
                               passed=result.returncode==0 and marker.exists()))
        (root/'scripts/download_models.py').write_text(code+'raise SystemExit(7)\n',encoding='utf-8')
        (root/'scripts/diagnose.py').write_text(code+'raise SystemExit(7)\n',encoding='utf-8')
        for name in ['下载模型.cmd','校验模型.cmd','环境诊断.cmd']:
            marker.unlink(missing_ok=True)
            env=os.environ.copy();env.pop('PYTHONHOME',None);env.pop('PYTHONPATH',None)
            result=subprocess.run(['cmd.exe','/d','/c',str(root/name)],env=env,input='\n',capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
            checks.append(dict(name='Helper failure exit '+name,exit_code=result.returncode,started=marker.exists(),passed=result.returncode==7 and marker.exists()))
        value=dict(passed=all(row['passed'] for row in checks),baseline=args.baseline_ref,checks=checks,no_gpu_or_real_server=True)
        if args.report:
            path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2),encoding='utf-8')
        print(json.dumps(value,indent=2))
        return 0 if value['passed'] else 1
    finally:
        assert root.parent==base and root.name.startswith('HyNomas-env-test-')
        shutil.rmtree(root)


if __name__=='__main__': raise SystemExit(main())
