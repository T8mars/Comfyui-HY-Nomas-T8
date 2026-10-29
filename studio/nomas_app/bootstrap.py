import os
import sys
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = ROOT / "runtime" / "ComfyUI"
NODES = ROOT / "custom_nodes" / "ComfyUI-HyNomas-T8"


def prepare_environment():
    if os.name == "nt":
        # PyTorch's bundled mimalloc otherwise retains large freed model pages.
        # These options must be present before torch/c10 loads in this process.
        os.environ.setdefault("MIMALLOC_PURGE_DELAY", "0")
        os.environ.setdefault("MIMALLOC_ARENA_EAGER_COMMIT", "0")
    for name, subdir in {"HF_HOME": "huggingface", "TRITON_CACHE_DIR": "triton",
                         "TORCHINDUCTOR_CACHE_DIR": "inductor", "TEMP": "tmp", "TMP": "tmp"}.items():
        path = ROOT / "cache" / subdir
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if os.name == "nt":
        helper = NODES / "hy_nomas" / "platform_compat.py"
        spec = importlib.util.spec_from_file_location("t8_platform_paths", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        changes = module.configure_triton_unicode_paths()
        if changes:
            (ROOT / "cache" / "compiler-paths.json").write_text(
                json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")


def configure():
    prepare_environment()
    for path in (COMFY, NODES):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from comfy.cli_args import args
    args.disable_dynamic_vram = True
    args.disable_pinned_memory = True
    args.disable_async_offload = True
    args.use_pytorch_cross_attention = True
    args.reserve_vram = 2.0
    import folder_paths
    for category in ("diffusion_models", "vae", "text_encoders"):
        path = ROOT / "models" / category
        path.mkdir(parents=True, exist_ok=True)
        folder_paths.add_model_folder_path(category, str(path), is_default=True)
    for category in ("output", "temp", "input"):
        path = ROOT / {"output": "outputs", "temp": "cache/comfy", "input": "inputs"}[category]
        path.mkdir(parents=True, exist_ok=True)
        getattr(folder_paths, f"set_{category}_directory")(str(path))
