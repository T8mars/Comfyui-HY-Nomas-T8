"""Launch the bundled ComfyUI directly, with native nodes and configured model folders."""
import runpy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.bootstrap import prepare_environment
from nomas_app.models import ModelLocations
from scripts.acceptance_evidence import source_snapshot


def configured_model_paths(locations):
    values = locations.preferences()
    for row in locations.status(values, inspect=True):
        if row["source"] == "custom" and not row["ready"]:
            raise FileNotFoundError(
                f"自定义模型路径未就绪，已停止启动：{row['label']}：{row['error']}。路径：{row['path']}"
            )
    # An empty default installation may still open ComfyUI to configure models.
    return locations.resolve(values)


def model_paths_config(paths):
    data = {"base_path": str(ROOT), "is_default": True, "custom_nodes": "custom_nodes"}
    for key, category in [("dit", "diffusion_models"), ("decoder", "vae"), ("text_encoder", "text_encoders")]:
        # ComfyUI prepends each is_default path: add the user's directory last
        # so an explicitly selected file wins over a same-named bundled file.
        directories = list(dict.fromkeys([str(ROOT / "models" / category), str(paths[key].parent)]))
        data[category] = "\n".join(directories)
    return {"hy_nomas_t8": data}


def main():
    prepare_environment()
    import yaml
    paths = configured_model_paths(ModelLocations())
    config = ROOT / "config" / "comfy_model_paths.generated.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(yaml.safe_dump(model_paths_config(paths), allow_unicode=True), encoding="utf-8")
    comfy = ROOT / "runtime" / "ComfyUI"
    sys.path.insert(0, str(comfy))
    sys.argv = [str(comfy / "main.py"), "--listen", "127.0.0.1", "--port", "8189",
                "--disable-dynamic-vram", "--disable-pinned-memory", "--disable-async-offload",
                "--use-pytorch-cross-attention", "--reserve-vram", "2",
                "--offline", "--extra-model-paths-config", str(config),
                "--output-directory", str(ROOT / "outputs" / "comfy"), "--auto-launch", *sys.argv[1:]]
    import psutil
    receipt = {"pid": os.getpid(), "process_create_time": psutil.Process().create_time(),
               "source_files": source_snapshot(ROOT)}
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "comfy-host-source.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    runpy.run_path(str(comfy / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
