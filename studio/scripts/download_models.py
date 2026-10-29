"""Explicit, resumable download of the three pinned model files; no inference network access."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.models import ModelLocations
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(16 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--model", choices=["dit", "decoder", "text_encoder"])
    parser.add_argument("--model-root", help="本次下载的模型目录；省略时读取界面保存的路径设置")
    parser.add_argument("--transport", choices=["ranges", "hub"], default="ranges")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "models.manifest.json").read_text(encoding="utf-8"))
    receipts = []
    locations = ModelLocations()
    values = {"model_root": args.model_root} if args.model_root else locations.preferences()
    targets = locations.resolve(values)
    for model in manifest["models"]:
        if args.model and args.model != model["id"]:
            continue
        target = targets[model["id"]]
        if not target.exists():
            if args.verify_only:
                raise FileNotFoundError(target)
            print(f"Downloading {model['id']} from {model['repo']} at {model['revision']}", flush=True)
            if args.transport == "ranges":
                from scripts.range_download import download
                url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{model['remote']}"
                download(url, target, model["size"], model["sha256"], max(1, min(args.workers, 16)))
            else:
                from huggingface_hub import hf_hub_download
                source = Path(hf_hub_download(model["repo"], model["remote"], revision=model["revision"],
                                             local_dir=ROOT / "downloads" / "models" / model["id"], token=False))
                if source.stat().st_size != model["size"] or digest(source) != model["sha256"]:
                    raise RuntimeError(f"Integrity check failed: {model['id']}")
                target.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.move(str(source), str(target))
        else:
            print(f"Verifying {model['id']}", flush=True)
            if target.stat().st_size != model["size"] or digest(target) != model["sha256"]:
                raise RuntimeError(f"Integrity check failed; preserve and inspect file: {target}")
        receipts.append({"id": model["id"], "path": str(target), "size": model["size"],
                         "sha256": model["sha256"], "verified": True})
        print(f"Verified {model['id']}: {model['sha256']}", flush=True)
    (ROOT / "evidence").mkdir(exist_ok=True)
    suffix = f"-{args.model}" if args.model else ""
    (ROOT / "evidence" / f"models-verified{suffix}.json").write_text(
        json.dumps(receipts, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
