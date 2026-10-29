"""Fetch pinned ComfyUI template packages with checksummed resumable transfers."""
import concurrent.futures
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.range_download import download
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import parse_wheel_filename, canonicalize_name
from pip._vendor.packaging.version import Version


def metadata(name, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        return json.load(response)


def main():
    requirements = (ROOT / "runtime" / "ComfyUI" / "requirements.txt").read_text(encoding="utf-8")
    parent = Requirement(next(line for line in requirements.splitlines()
                              if line.startswith("comfyui-workflow-templates==")))
    version = next(iter(parent.specifier)).version
    parent_data = metadata(parent.name, version)
    packages = {parent.name: version}
    for raw in parent_data["info"]["requires_dist"]:
        req = Requirement(raw)
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        pins = list(req.specifier)
        if len(pins) != 1 or pins[0].operator != "==":
            raise ValueError(f"Unpinned template dependency: {raw}")
        packages[req.name] = pins[0].version
    supported = {tag: rank for rank, tag in enumerate(sys_tags())}

    def fetch(item):
        name, version = item
        data = parent_data if name == parent.name else metadata(name, version)
        wheels = []
        for entry in data["urls"]:
            if not entry["filename"].endswith(".whl") or entry.get("yanked"):
                continue
            try:
                wheel_name, wheel_version, _, tags = parse_wheel_filename(entry["filename"])
            except ValueError:
                continue
            if canonicalize_name(wheel_name) != canonicalize_name(name) or wheel_version != Version(version):
                continue
            ranks = [supported[tag] for tag in tags if tag in supported]
            if ranks:
                wheels.append((min(ranks), entry))
        if not wheels:
            raise ValueError(f"No compatible wheel for {name} {version}")
        entry = min(wheels, key=lambda item: item[0])[1]
        path = ROOT / "downloads" / "wheels" / entry["filename"]
        print(f"Pinned {name} {version}: {entry['size']:,} bytes", flush=True)
        download(entry["url"], path, entry["size"], entry["digests"]["sha256"], workers=4)
        return {"package": name, "version": version, "file": str(path.relative_to(ROOT)),
                "size": entry["size"], "sha256": entry["digests"]["sha256"], "url": entry["url"]}

    receipts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, item) for item in packages.items()]
        for future in concurrent.futures.as_completed(futures):
            receipts.append(future.result())
    folder = ROOT / "evidence"
    folder.mkdir(exist_ok=True)
    (folder / "template-wheels.json").write_text(json.dumps(receipts, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
