"""Real generation from an extracted bundle: default, custom-root and renamed-file paths."""
import argparse
import gc
import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.engine import NativeEngine
from nomas_app.models import ModelLocations
from nomas_app.resources import ResourceMonitor
from scripts.benchmark import inspect_clip
from scripts.acceptance_evidence import (require, source_snapshot, verify_extracted_archive,
                                         verify_resources, verify_selected_paths, verify_video)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-weights", type=Path, required=True,
                        help="Verified model directory on this NTFS volume; hardlinks avoid copying weights")
    parser.add_argument("--archive", type=Path, required=True, help="The exact candidate ZIP extracted into this directory")
    args = parser.parse_args()
    require(Path(sys.executable).resolve().is_relative_to(ROOT), "Use the extracted bundle's Python")
    require(" " in str(ROOT) and any("\u4e00" <= char <= "\u9fff" for char in str(ROOT)),
            "Extract into a fresh Chinese/space directory before acceptance")
    locations = ModelLocations()
    original = locations.config_file.read_bytes() if locations.config_file.exists() else None
    report = {"passed": False, "scope": "Unmodified extracted bundle, real generation with all three path modes",
              "root": str(ROOT), "weight_setup": "NTFS hardlinks; custom paths outside the application directory; no code imports from source checkout",
              "source_files_before": source_snapshot(ROOT), "cases": []}
    engine = NativeEngine()
    active = None
    created_links = set()
    def progress(message, value):
        if active is not None:
            active.stage = message
        print(f"{message}: {value:.0%}", flush=True)
    def link(source, destination):
        require(any(destination.resolve().is_relative_to(folder) for folder in link_roots), "Unsafe test link destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.link(source, destination)
            created_links.add(destination)
        require(destination in created_links, "Refusing to reuse a pre-existing model test path; extract into a fresh directory")
        require(destination.samefile(source), "Existing test weight is not the intended hardlink")
    try:
        report["archive_before"] = verify_extracted_archive(ROOT, args.archive)
        root_override = ROOT.parent / "自定义 模型库"
        individual_root = ROOT.parent / "独立 文件路径"
        link_roots = (ROOT / "models", root_override, individual_root)
        aliases = {"dit": "主模型/改名 DiT.safetensors", "decoder": "视频解码器/自选 视频.safetensors",
                   "text_encoder": "文本编码器/自选 文字.safetensors"}
        path_modes = {"default": {}, "custom-root": {}, "individual-renamed": {}}
        sources = {}
        for entry in locations.entries:
            source = args.source_weights.resolve() / Path(entry["local"]).relative_to("models")
            require(source.stat().st_size == entry["size"], "Source weight size does not match manifest")
            sources[entry["id"]] = source
            path_modes["default"][entry["id"]] = ROOT / entry["local"]
            path_modes["custom-root"][entry["id"]] = root_override / Path(entry["local"]).relative_to("models")
            path_modes["individual-renamed"][entry["id"]] = individual_root / aliases[entry["id"]]
        planned = [path for paths in path_modes.values() for path in paths.values()]
        source_paths = {path.resolve() for path in sources.values()}
        require(all(path.resolve() not in source_paths for path in planned), "Source weights overlap test destinations")
        require(all(not path.exists() and not path.is_symlink() for path in planned),
                "Model test destinations already exist; use a fresh extraction directory")
        configs = [("default", {}), ("custom-root", {"model_root": str(root_override)}),
                   ("individual-renamed", {key: str(path) for key, path in path_modes["individual-renamed"].items()})]
        start_image = ROOT / "inputs" / "portable-start.png"
        for name, settings in configs:
            engine.reset()
            # Only this mode's hardlinks are available: ignoring the configuration
            # cannot succeed by silently loading a default copy of the same inode.
            for mode, paths in path_modes.items():
                for key, destination in paths.items():
                    if mode == name:
                        link(sources[key], destination)
                    elif destination.exists():
                        require(destination in created_links and destination.resolve().is_relative_to(ROOT.parent)
                                and destination.resolve() != sources[key].resolve() and destination.samefile(sources[key]),
                                "Refusing to remove an unrelated test file")
                        destination.unlink()
                        created_links.remove(destination)
            locations.save(settings)
            persisted = ModelLocations().preferences()
            paths = ModelLocations().required_paths()
            expected = {key: str(path.resolve()) for key, path in path_modes[name].items()}
            verify_selected_paths(expected, paths, paths)
            require(all(persisted[key] == value for key, value in settings.items()), "Model path settings did not persist")
            config = {"width":640,"height":384,"seconds":1,"steps":8,"seed":42,"cache_gib":12,
                      "subject":"a small orange fox","appearance":"A small orange fox with a white chest and fluffy tail.",
                      "style":"Cinematic fantasy","perspective":"third-person observer",
                      "scene":"A quiet stone path winds through a luminous forest with moss-covered rocks and blue wildflowers.",
                      "action":"walks slowly forward along the stone path","matmul":"int8","decoder_matmul":"fp8"}
            if name == "individual-renamed":
                config["image_path"] = str(start_image)
            monitor = ResourceMonitor()
            active = monitor
            with monitor:
                result = engine.generate(config, progress)
            active = None
            resources = monitor.result()
            verify_selected_paths(expected, paths, engine.model_paths)
            clip = ROOT / "outputs" / result["clips"][0]["file"]
            video = inspect_clip(clip)
            verify_video(video, 25 if name == "individual-renamed" else 24, 640, 384)
            verify_resources(resources)
            exported = engine.export()
            require(inspect_clip(ROOT / "outputs" / exported["file"])["frames"] == video["frames"], "Export frame count differs")
            report["cases"].append({"name":name,"persisted_settings":persisted,"model_paths":paths,
                                    "expected_model_paths":expected,"loaded_model_paths":dict(engine.model_paths),
                                    "image_start":name == "individual-renamed", "result":result,"video":video,"resources":resources})
            if name == "default":
                import av
                start_image.parent.mkdir(exist_ok=True)
                with av.open(str(clip)) as container:
                    next(container.decode(video=0)).to_image().save(start_image)
            engine.reset()
            require(engine.snapshot()["archive_gib"] == 0 and not engine.snapshot()["models_loaded"], "Reset retained a loaded world/model")
            gc.collect()
        origins = {"python":sys.executable,"prefix":sys.prefix}
        for name in ("torch","triton","numpy","av","comfy","hy_nomas","nomas_app"):
            module = importlib.import_module(name)
            origins[name] = module.__file__ or str(next(iter(module.__path__)))
        require(all(Path(path).resolve().is_relative_to(ROOT) for path in origins.values()), "A module originated outside the extracted bundle")
        report["module_origins"] = origins
        report["archive_after"] = verify_extracted_archive(ROOT, args.archive)
        require(report["archive_before"] == report["archive_after"], "Candidate archive changed during acceptance")
        report["source_files_after"] = source_snapshot(ROOT)
        require(report["source_files_before"] == report["source_files_after"], "Inference sources changed during acceptance")
        report["passed"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["failed_state"] = engine.snapshot()
        if active is not None:
            report["failed_resources"] = active.result()
        raise
    finally:
        engine.reset()
        if original is None:
            locations.config_file.unlink(missing_ok=True)
        else:
            locations.config_file.write_bytes(original)
        (ROOT / "evidence").mkdir(exist_ok=True)
        (ROOT / "evidence/relocation-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"passed":report["passed"],"cases":[case["name"] for case in report["cases"]],
                          "error":report.get("error")},ensure_ascii=False,indent=2),flush=True)


if __name__ == "__main__":
    main()
