"""Build the program-only release from the tested host and public studio sources."""
import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parent
REPO = STUDIO.parent
VERSION = json.loads((STUDIO / "versions.json").read_text(encoding="utf-8"))["version"]
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", VERSION):
    raise ValueError("Invalid studio version")
SKIP = {".git", ".github", "__pycache__", ".pytest_cache", "models", "user", "input", "output", "inputs", "outputs", "temp", "cache", "logs", "downloads", "custom_nodes", "tests", "tests-unit", ".ci"}


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
    def add(path, name):
        if name in files:
            raise ValueError(f"Duplicate payload path: {name}")
        files[name] = Path(path).read_bytes()
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
            add(STUDIO / relative, relative)
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
        add(REPO / rel, "custom_nodes/ComfyUI-HyNomas-T8/" + name)
    host_files = subprocess.check_output(["git", "-C", str(comfy), "ls-files", "-z"]).split(b"\0")
    for encoded in host_files:
        if not encoded:
            continue
        name = encoded.decode("utf-8")
        path = Path(name)
        if any(part in SKIP for part in path.parts) or path.suffix in {".pyc", ".pyo"} or path.name == "AGENTS.md" or name.startswith('.'):
            continue
        add(comfy / path, "runtime/ComfyUI/" + name)
    files["tools/uv.exe"] = uv.read_bytes()
    source_lock = json.loads((STUDIO / "docs/inference-source-lock.json").read_text(encoding="utf-8"))
    for name, expected in source_lock.items():
        if name not in files or hashlib.sha256(files[name]).hexdigest() != expected:
            raise ValueError(f"Inference source differs from accepted release: {name}")
    files["app-version.json"] = (json.dumps({"app":"hy-nomas-t8-studio", "version":VERSION, "updater_schema":1}, indent=2) + "\n").encode()
    files["program-files.json"] = (json.dumps({"version":VERSION, "files":sorted(files)}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"Hy-Nomas-T8-Program-{VERSION}-win64.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, data in sorted(files.items()):
            if name.lower().startswith(("runtime/managed/", "models/")) or 'site-packages' in Path(name).parts or \
                name.lower().endswith((".safetensors", ".ckpt", ".pth", ".pt", ".gguf", ".onnx", ".bin", ".h5", ".hdf5", ".pkl", ".dll", ".pyd")) or \
                (name.lower().endswith('.exe') and name != 'tools/uv.exe'):
                raise ValueError(f"Python/weight binary in program payload: {name}")
            z.writestr("Hy-Nomas-By-T8star/" + name, data)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    manifest = {"schema":1, "app":"hy-nomas-t8-studio", "version":VERSION, "updater_schema":1,
                "runtime_lock_sha256":hashlib.sha256(files["requirements.lock.txt"]).hexdigest(),
                "archive":{"name":archive.name, "bytes":archive.stat().st_size, "sha256":digest(archive)},
                "files":[{"path":name, "bytes":len(data), "sha256":hashlib.sha256(data).hexdigest()} for name,data in sorted(files.items())]}
    manifest_path = destination / f"Hy-Nomas-T8-Program-{VERSION}-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = archive.with_suffix(".zip.sha256")
    sums.write_text(manifest["archive"]["sha256"] + "  " + archive.name + "\n", encoding="utf-8")
    print(json.dumps({"archive":str(archive),"manifest":str(manifest_path),"files":len(files),"expanded_bytes":sum(map(len,files.values())),**manifest["archive"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfyui-root", required=True)
    parser.add_argument("--uv-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build(args.comfyui_root, args.uv_file, args.output)
