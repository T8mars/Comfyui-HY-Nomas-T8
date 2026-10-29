"""Read-only environment/model diagnostics, safe to run before installing GPU packages."""
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.models import ModelLocations


def main():
    report = {"root": str(ROOT), "python": sys.version, "executable": sys.executable,
              "platform": platform.platform(), "disk_free_gib": shutil.disk_usage(ROOT).free / 2**30,
              "packages": {}, "models": ModelLocations().status(inspect=True)}
    cache_record = ROOT / "cache" / "compiler-paths.json"
    if cache_record.is_file():
        report["last_configured_compiler_cache_paths"] = json.loads(cache_record.read_text(encoding="utf-8"))
    for package in ("torch", "torchvision", "triton-windows", "transformers", "comfy-kitchen", "comfy-aimdo", "av", "aiohttp"):
        try:
            report["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][package] = "MISSING"
    try:
        import psutil
        report["ram_available_gib"] = psutil.virtual_memory().available / 2**30
        report["ram_total_gib"] = psutil.virtual_memory().total / 2**30
    except ImportError:
        pass
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used,compute_cap",
                                 "--format=csv"], capture_output=True, text=True, timeout=15)
        report["nvidia_smi"] = result.stdout.strip() or result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["nvidia_smi"] = str(exc)
    try:
        import torch
        report["cuda"] = {"available": torch.cuda.is_available(), "runtime": torch.version.cuda}
        if torch.cuda.is_available():
            report["cuda"].update(device=torch.cuda.get_device_name(), capability=torch.cuda.get_device_capability())
    except Exception as exc:
        report["cuda"] = {"error": str(exc)}
    directory = ROOT / "evidence"
    directory.mkdir(exist_ok=True)
    path = directory / "diagnostics.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n诊断结果：{path}")


if __name__ == "__main__":
    main()
