"""Use a tiny sleeping Python module to test launch leases without any GPU/server."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent


def copy_test_runtime(python, runtime):
    """Copy the real interpreter and standard library only; never site-packages/models."""
    runtime.mkdir(parents=True)
    shutil.copy2(python, runtime / "python.exe")
    for path in python.parent.glob("*.dll"):
        shutil.copy2(path, runtime / path.name)
    shutil.copytree(python.parent / "Lib", runtime / "Lib",
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test", "tests", "ensurepip"))
    if (python.parent / "DLLs").is_dir():
        shutil.copytree(python.parent / "DLLs", runtime / "DLLs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-path", default=sys.executable)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--report")
    args = parser.parse_args()
    base = Path(tempfile.gettempdir()).resolve()
    root = base / ("HyNomas-launch-test-" + uuid.uuid4().hex) / "中文 安装目录"
    root.mkdir(parents=True)
    first = None
    result = {}
    try:
        names = ["启动整合包.cmd", "启动ComfyUI原生节点.cmd", "scripts/update_app.ps1", "scripts/update_core.ps1"]
        if not args.baseline_ref:
            names += ["scripts/launch_app.ps1", "scripts/release_catalog.ps1"]
        for name in names:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if args.baseline_ref:
                data = subprocess.check_output(["git", "show", f"{args.baseline_ref}:studio/{name}"], cwd=REPO)
            else:
                data = (STUDIO / name).read_bytes()
            destination.write_bytes(data)
        (root / "config").mkdir()
        (root / "config/update-settings.json").write_text('{"auto_update":false}', encoding="utf-8")
        python = Path(args.python_path).resolve()
        runtime = root / "runtime/managed/cpython-3.12.9-windows-x86_64-none"
        copy_test_runtime(python, runtime)
        (root / "nomas_app").mkdir()
        (root / "nomas_app/__init__.py").write_text("", encoding="utf-8")
        ready = root / "studio-ready"
        second_ready = root / "comfy-ready"
        (root / "nomas_app/server.py").write_text(f"import time\nopen({str(ready)!r}, 'w').write('ready')\ntime.sleep(8)\n", encoding="utf-8")
        (root / "scripts/run_comfy.py").write_text(f"open({str(second_ready)!r}, 'w').write('ready')\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(PYTHONHOME=str(python.parent), PYTHONUTF8="1")
        first = subprocess.Popen(["cmd.exe", "/d", "/c", str(root / "启动整合包.cmd")], env=environment,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 15
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        if not ready.exists():
            raise AssertionError("Studio test child did not start: " + first.communicate("\n", timeout=15)[0])
        second = subprocess.run(["cmd.exe", "/d", "/c", str(root / "启动ComfyUI原生节点.cmd")], env=environment,
                                input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        result = {"second_exit_code":second.returncode, "overlap_occurred":second_ready.exists(),
                  "baseline_ref":args.baseline_ref, "no_gpu_or_real_server":True}
        if args.baseline_ref:
            assert second.returncode == 0 and second_ready.exists(), result
            result["bug_reproduced"] = True
        else:
            assert second.returncode == 32 and not second_ready.exists(), (result, second.stdout)
            first.communicate("\n", timeout=15)
            assert first.returncode == 0
            after = subprocess.run(["cmd.exe", "/d", "/c", str(root / "启动ComfyUI原生节点.cmd")], env=environment,
                                   input="\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            assert after.returncode == 0 and second_ready.exists(), after.stdout
            result.update(passed=True, launch_allowed_after_exit=True)
    finally:
        if first is not None and first.poll() is None:
            first.communicate("\n", timeout=20)
        resolved = root.parent.resolve()
        assert resolved.parent == base and resolved.name.startswith("HyNomas-launch-test-")
        shutil.rmtree(resolved)
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
