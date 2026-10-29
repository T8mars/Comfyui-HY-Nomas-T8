"""Build the program-only release from the tested host and public studio sources."""
import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
REPO = STUDIO.parent
VERSION = json.loads((STUDIO / "versions.json").read_text(encoding="utf-8"))["version"]
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", VERSION):
    raise ValueError("Invalid studio version")
SKIP = {".git", ".github", "__pycache__", ".pytest_cache", "models", "user", "input", "output", "inputs", "outputs", "temp", "cache", "logs", "downloads", "custom_nodes", "tests", "tests-unit", ".ci"}
ROOT_FILES = {"LICENSE", "NOTICE", "README.zh-CN.md", "README.en.md", "BUILD.md", "versions.json", "app-version.json",
              "program-files.json", "models.manifest.json", "requirements.txt", "requirements.lock.txt", "tools/uv.exe",
              "启动整合包.cmd", "启动ComfyUI原生节点.cmd", "安装环境.cmd", "环境诊断.cmd", "下载模型.cmd", "校验模型.cmd", "更新程序.cmd", "检查更新.cmd"}


def validate_payload_name(name):
    """Keep the producer inside the existing updater's managed-file contract."""
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError(f"Invalid program path: {name}")
    parts=name.split("/")
    if any(part in {"", ".", ".."} or re.search(r'[<>"|?*\x00-\x1f]|[. ]$',part) or
           re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)",part,re.I) for part in parts):
        raise ValueError(f"Unsafe program path: {name}")
    lower=name.lower()
    if name not in ROOT_FILES and not lower.startswith(("nomas_app/","scripts/","licenses/","docs/","custom_nodes/comfyui-hynomas-t8/","runtime/comfyui/")):
        raise ValueError(f"Not an updater-managed path: {name}")
    if re.search(r"\.(safetensors|ckpt|pth|pt|gguf|onnx|bin|h5|hdf5|pkl|pyc|pyo|dll|pyd)$",lower) or \
       (lower.endswith(".exe") and name!="tools/uv.exe") or \
       any(part.lower() in {"models","managed","python_embeded","python_embedded","site-packages","user","input","output","inputs","outputs","config","cache","logs","downloads","temp",".git"} for part in parts) or \
       lower.startswith("runtime/comfyui/custom_nodes/") or lower=="runtime/comfyui/extra_model_paths.yaml":
        raise ValueError(f"Protected data/runtime path: {name}")


def read_tracked_source(path, source_root):
    source=Path(path).absolute()
    base=Path(source_root).absolute()
    source.relative_to(base)
    if not source.resolve(strict=True).is_relative_to(base.resolve(strict=True)):
        raise ValueError(f"Tracked source resolves outside its source root: {path}")
    current=source
    while current!=base:
        attrs=current.lstat()
        if current.is_symlink() or getattr(attrs,"st_file_attributes",0) & getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",1024):
            raise ValueError(f"Linked tracked source refused: {path}")
        current=current.parent
    return source.read_bytes()


def publish_assets(staged, destination, names):
    """Preserve previous build outputs on ordinary publication errors."""
    backups=staged/"previous";backups.mkdir()
    existing=set()
    for name in names:
        target=destination/name
        if target.exists():
            if not target.is_file(): raise ValueError(f"Build output is a directory: {target}")
            shutil.copyfile(target,backups/name)
            existing.add(name)
    changed=[]
    try:
        for name in names:
            (staged/name).replace(destination/name)
            changed.append(name)
    except BaseException:
        failures=[]
        for name in reversed(changed):
            try:
                if name in existing: (backups/name).replace(destination/name)
                else: (destination/name).unlink(missing_ok=True)
            except OSError: failures.append(name)
        if failures: raise RuntimeError(f"Build output rollback incomplete; backups retained at {staged}")
        raise


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build(comfy, uv, destination):
    comfy, uv, destination = Path(comfy).resolve(), Path(uv).resolve(), Path(destination).resolve()
    if digest(uv) != "311ba603f935e850ffea25cbd798d7437ca1498f30128795823f0ff4778239ea":
        raise ValueError("Expected the pinned uv 0.5.31 Windows binary")
    revision = subprocess.check_output(["git", "-C", str(comfy), "rev-parse", "HEAD"], text=True).strip()
    if revision != "63be2f9b82ad0dac88a7ceaa28a9cf3154125a18":
        raise ValueError("Expected ComfyUI v0.42.1")
    dirty = subprocess.check_output(["git", "-C", str(comfy), "diff", "HEAD", "--name-only", "-z"]).split(b"\0")
    if [name for name in dirty if name] != [b"execution.py"]:
        raise ValueError("Unexpected changes in pinned ComfyUI sources")
    if digest(comfy / "execution.py") != "996dfb64bc000cfc29a2f54105d1680b9ffaab069523eedd253911731917507a":
        raise ValueError("Executor does not match the accepted cache-reference fix")
    files = {}
    seen=set()
    def add(path, name, source_root):
        validate_payload_name(name)
        if name.casefold() in seen:
            raise ValueError(f"Duplicate payload path: {name}")
        files[name] = read_tracked_source(path,source_root)
        seen.add(name.casefold())
    native_names = subprocess.check_output(["git", "-C", str(REPO), "ls-files", "-z"], text=False).split(b"\0")
    studio_root_files = {"LICENSE", "NOTICE", "README.zh-CN.md", "README.en.md", "BUILD.md", "versions.json",
                         "requirements.txt", "requirements.lock.txt", "models.manifest.json"}
    for encoded in native_names:
        name = encoded.decode("utf-8")
        if not name.startswith("studio/"):
            continue
        relative = name.removeprefix("studio/")
        if any(part.startswith('.') or part == '__pycache__' for part in Path(relative).parts):
            continue
        if relative.startswith(("nomas_app/", "scripts/", "licenses/", "docs/")) or relative in studio_root_files or ("/" not in relative and relative.endswith(".cmd")):
            add(STUDIO / relative, relative, STUDIO)
    excluded_native = {".git", ".github", "studio", "tests"}
    for encoded in native_names:
        if not encoded:
            continue
        name = encoded.decode("utf-8")
        rel = Path(name)
        if any(part in excluded_native for part in rel.parts) or name.startswith('.') or name in {"pytest.ini"}:
            continue
        if name.startswith(("docs/program",)):
            continue
        add(REPO / rel, "custom_nodes/ComfyUI-HyNomas-T8/" + name, REPO)
    host_files = subprocess.check_output(["git", "-C", str(comfy), "ls-files", "-z"]).split(b"\0")
    for encoded in host_files:
        if not encoded:
            continue
        name = encoded.decode("utf-8")
        path = Path(name)
        if any(part in SKIP for part in path.parts) or path.suffix in {".pyc", ".pyo"} or path.name == "AGENTS.md" or name.startswith('.'):
            continue
        add(comfy / path, "runtime/ComfyUI/" + name, comfy)
    files["tools/uv.exe"] = uv.read_bytes()
    source_lock = json.loads((STUDIO / "docs/inference-source-lock.json").read_text(encoding="utf-8"))
    for name, expected in source_lock.items():
        if name not in files or hashlib.sha256(files[name]).hexdigest() != expected:
            raise ValueError(f"Inference source differs from accepted release: {name}")
    files["app-version.json"] = (json.dumps({"app":"hy-nomas-t8-studio", "version":VERSION, "updater_schema":1}, indent=2) + "\n").encode()
    files["program-files.json"] = (json.dumps({"version":VERSION, "files":sorted(files)}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for name in files:
        validate_payload_name(name)
        if any('/'.join(name.split('/')[:index]).casefold() in seen for index in range(1,len(name.split('/')))):
            raise ValueError(f"Payload file is also a parent directory: {name}")
    destination.mkdir(parents=True, exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.program-build-',dir=destination))
    retain=False
    try:
        archive = stage / f"Hy-Nomas-T8-Program-{VERSION}-win64.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for name,data in sorted(files.items()): z.writestr("Hy-Nomas-By-T8star/"+name,data)
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None: raise ValueError('Build archive CRC failed')
        manifest = {"schema":1, "app":"hy-nomas-t8-studio", "version":VERSION, "updater_schema":1,
                "runtime_lock_sha256":hashlib.sha256(files["requirements.lock.txt"]).hexdigest(),
                "archive":{"name":archive.name, "bytes":archive.stat().st_size, "sha256":digest(archive)},
                "files":[{"path":name, "bytes":len(data), "sha256":hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
        manifest_path = stage / f"Hy-Nomas-T8-Program-{VERSION}-manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sums = archive.with_suffix(".zip.sha256")
        sums.write_text(manifest["archive"]["sha256"] + "  " + archive.name + "\n", encoding="utf-8")
        try: publish_assets(stage,destination,[archive.name,manifest_path.name,sums.name])
        except RuntimeError:
            retain=True
            raise
        archive=destination/archive.name;manifest_path=destination/manifest_path.name
    finally:
        if not retain: shutil.rmtree(stage)
    print(json.dumps({"archive":str(archive),"manifest":str(manifest_path),"files":len(files),"expanded_bytes":sum(map(len,files.values())),**manifest["archive"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfyui-root", required=True)
    parser.add_argument("--uv-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build(args.comfyui_root, args.uv_file, args.output)
