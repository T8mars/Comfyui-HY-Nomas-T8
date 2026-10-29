"""Bind pending real acceptance runs to their code, workflow and extracted archive."""
import hashlib
import json
import math
import zipfile
import zlib
from pathlib import Path


SOURCE_FILES = (
    "models.manifest.json", "requirements.lock.txt", "scripts/run_comfy.py",
    "scripts/benchmark.py", "scripts/benchmark_comfy.py", "scripts/verify_portable.py",
    "scripts/acceptance_evidence.py", "runtime/ComfyUI/execution.py",
    "runtime/ComfyUI/comfy/model_management.py", "runtime/ComfyUI/comfy/memory_management.py",
    "runtime/ComfyUI/comfy/model_patcher.py", "runtime/ComfyUI/comfy_execution/graph.py",
    "runtime/ComfyUI/comfy_execution/caching.py",
)
SOURCE_GLOBS = ("nomas_app/*.py", "custom_nodes/ComfyUI-HyNomas-T8/hy_nomas/**/*.py",
                "custom_nodes/ComfyUI-HyNomas-T8/nodes.py")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(16 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_snapshot(root):
    root = Path(root)
    names = set(SOURCE_FILES)
    for pattern in SOURCE_GLOBS:
        names.update(path.relative_to(root).as_posix() for path in root.glob(pattern))
    return {name: sha256(root / name) for name in sorted(names)}


def verify_sources(root, report):
    current = source_snapshot(root)
    require(report.get("source_files_before") == current,
            "Acceptance does not cover the current inference code and memory policy")
    require(report.get("source_files_after") == current, "Sources changed during acceptance")


def verify_extracted_archive(root, archive_path):
    """Read every shipped file; generated caches/outputs are outside this comparison."""
    root, archive_path = Path(root).resolve(), Path(archive_path).resolve()
    checked, total, archived = 0, 0, set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = member.filename.split("/")
            require(parts[0] == "Hy-Nomas-By-T8star" and len(parts) > 1,
                    f"Unexpected archive member: {member.filename}")
            path = root.joinpath(*parts[1:]).resolve()
            require(path.is_relative_to(root), f"Archive path escaped bundle: {member.filename}")
            require(path.is_file() and path.stat().st_size == member.file_size,
                    f"Missing or modified extracted file: {member.filename}")
            crc = 0
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(16 * 1024**2), b""):
                    crc = zlib.crc32(chunk, crc)
            require(crc == member.CRC, f"Extracted file CRC differs: {member.filename}")
            checked += 1
            total += member.file_size
            archived.add(Path(*parts[1:]).as_posix())
    # An overlay extraction may leave an importable module, .pth or native
    # library behind even though every archived member still matches.
    for directory in ("nomas_app", "custom_nodes", "scripts", "tests", "runtime", "tools", "licenses"):
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and "__pycache__" not in relative.parts:
                require(relative.as_posix() in archived, f"Unarchived file in extracted runtime/code: {relative}")
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in (".py", ".pyc", ".pyd", ".pth", ".dll", ".exe", ".cmd", ".bat", ".ps1"):
            require(path.name in archived, f"Unarchived executable file in extracted bundle: {path.name}")
    require(checked > 0, "Empty candidate archive")
    return {"path": str(archive_path), "sha256": sha256(archive_path),
            "checked_files": checked, "checked_bytes": total}


def verify_candidate_payload(root, archive_path, files):
    """The formal build may update reports/docs/status, but must ship the tested code/runtime."""
    root = Path(root)
    def payload(name):
        return not (name.startswith("evidence/") or name.endswith(".md") or
                    name in ("package-files.json", "versions.json"))
    current = {relative.as_posix(): source for source, relative in files if payload(relative.as_posix())}
    with zipfile.ZipFile(archive_path) as archive:
        members = {member.filename.removeprefix("Hy-Nomas-By-T8star/"): member
                   for member in archive.infolist() if not member.is_dir()}
        require(set(current) == {name for name in members if payload(name)},
                "Formal package payload differs from the tested candidate file list")
        for name, source in current.items():
            member, crc = members[name], 0
            require(source.stat().st_size == member.file_size, f"Untested package file: {name}")
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(16 * 1024**2), b""):
                    crc = zlib.crc32(chunk, crc)
            require(crc == member.CRC, f"Package code/runtime changed after candidate acceptance: {name}")
        recorded = json.loads(archive.read("Hy-Nomas-By-T8star/versions.json"))
    current_versions = json.loads((root / "versions.json").read_text(encoding="utf-8"))
    for values in (recorded, current_versions):
        values.pop("version", None)
        values.pop("status", None)
    require(recorded == current_versions, "Source/runtime version pins changed after candidate acceptance")


def verify_resources(resources):
    require(resources.get("monitor_error") is None and resources.get("samples", 0) >= 2,
            "Missing or failed real resource monitoring")
    for key in ("gpu_peak_used_gib", "gpu_total_gib", "system_min_available_ram_gib"):
        value = resources.get(key)
        require(type(value) in (int, float) and math.isfinite(value), f"Invalid resource metric: {key}")
    require(resources["system_min_available_ram_gib"] >= 4, "Less than 4 GiB physical RAM remained")
    require(0 < resources["gpu_peak_used_gib"] <= resources["gpu_total_gib"] <= 24,
            "Resource evidence does not cover a physical GPU of at most 24 GiB")


def verify_video(video, frames, width, height):
    require((video.get("frames"), video.get("fps"), video.get("width"), video.get("height"))
            == (frames, 24, width, height), "Video does not cover the required frames/fps/dimensions")
    motion = video.get("mean_adjacent_frame_mae")
    require(type(motion) in (int, float) and math.isfinite(motion) and motion > .01,
            "Missing actual decoded video motion measurement")


def verify_default_graph(root, graph):
    expected = json.loads((Path(root) / "evidence/comfy-per-segment-api.json").read_text(encoding="utf-8"))["output"]
    allowed = {"HyNomasLoadDiT":"dit_name", "CLIPLoader":"clip_name",
               "HyNomasLoadDecoder":"decoder_name", "SaveVideo":"filename_prefix"}
    def canonical(nodes):
        result = {}
        for key, node in nodes.items():
            kind, inputs = node["class_type"], dict(node["inputs"])
            inputs.pop(allowed.get(kind), None)
            result[key] = {"class_type":kind, "inputs":inputs}
        return result
    require(canonical(graph) == canonical(expected), "Executed native graph differs from the default frontend workflow")
    samplers = [node["inputs"] for node in graph.values() if node["class_type"] == "KSampler"]
    require(len(samplers) == 2 and all((node.get("steps"), node.get("cfg"), node.get("sampler_name"),
            node.get("scheduler"), node.get("denoise")) == (8, 1, "euler", "simple", 1) for node in samplers),
            "Actual native KSampler inputs do not use 8 steps / CFG 1 / euler / simple")
    segments = [node["inputs"] for node in graph.values() if node["class_type"] == "HyNomasSegment"]
    require(len(segments) == 2 and all((node.get("width"), node.get("height")) == (1280, 704) for node in segments),
            "Actual native segment dimensions were reduced")
    scripts = [json.loads(node["inputs"]["world_json"])["segments"] for node in graph.values()
               if node["class_type"] == "HyNomasWorldPrompt"]
    require(len(scripts) == 1 and [(segment["start_time"], segment["end_time"]) for segment in scripts[0]] ==
            [("00:00.000", "00:05.000"), ("00:05.000", "00:10.000")], "Actual native script is not two 5-second segments")


def verify_native_report(root, report):
    require(report.get("passed") is True, "Native workflow acceptance has not passed")
    verify_sources(root, report)
    host = report.get("host_startup", {})
    server = report.get("server", {})
    require(type(host.get("pid")) is int and host["pid"] > 0 and
            type(host.get("process_create_time")) in (int, float) and
            math.isfinite(host["process_create_time"]) and host["process_create_time"] > 0 and
            host.get("pid") == server.get("pid") and
            host.get("process_create_time") == server.get("process_create_time") and
            host.get("source_files") == report["source_files_before"],
            "Native host did not start with the accepted inference code")
    require(report.get("shipped_default_prompt_and_dimensions") is True,
            "Native acceptance used a reduced workflow")
    require(report.get("config") == {"width": 1280, "height": 704, "segment_seconds": 5,
            "segments": 2, "steps": 8, "cfg": 1, "sampler": "euler/simple"},
            "Native acceptance must cover 1280x704, two 5-second segments")
    exported = Path(root) / "evidence/comfy-per-segment-api.json"
    require(report.get("frontend_export_sha256") == sha256(exported), "Native workflow export changed")
    history = report.get("history", {})
    prompt = history.get("prompt", [])
    require(len(prompt) >= 3 and isinstance(prompt[2], dict), "Missing actual executed native graph")
    verify_default_graph(root, prompt[2])
    require(history.get("status", {}).get("completed") is True and
            history.get("status", {}).get("status_str") == "success", "Native history does not confirm completion")
    require(len(report.get("videos", [])) == 1, "Missing native output video")
    verify_video(report["videos"][0], 240, 1280, 704)
    verify_resources(report.get("resources", {}))
    require(report.get("execution_observer_error") is None, "Native execution observer failed")
    stages = [event.get("class_type") for event in report.get("execution_stages", [])]
    require(stages.count("KSampler") == 2 and "HyNomasDecode" in stages and "SaveVideo" in stages,
            "Native full workflow was not observed executing")


def verify_hd_report(root, report):
    require(report.get("passed") is True, "Standalone HD acceptance has not passed")
    verify_sources(root, report)
    config = report.get("config", {})
    require(report.get("scenario") == "hd" and
            (config.get("width"), config.get("height"), config.get("seconds")) == (1280, 704, 5),
            "Standalone HD acceptance was reduced")
    require(len(report.get("videos", [])) == 1 and len(report.get("runs", [])) == 1,
            "Missing standalone HD run/video")
    verify_video(report["videos"][0], 120, 1280, 704)
    verify_resources(report["runs"][0].get("resources", {}))


def verify_selected_paths(expected, resolved, loaded):
    normalize = lambda paths: {key: str(Path(path).resolve()) for key, path in paths.items()}
    require(set(expected) == {"dit", "decoder", "text_encoder"}, "Missing expected model paths")
    require(normalize(expected) == normalize(resolved) == normalize(loaded),
            "Actual engine model paths differ from the selected path mode")


def verify_portable_report(root, report):
    require(report.get("passed") is True, "Portable acceptance has not passed")
    verify_sources(root, report)
    bundle = Path(report.get("root", "")).resolve()
    require(bundle != Path(root).resolve() and " " in str(bundle) and
            any("\u4e00" <= char <= "\u9fff" for char in str(bundle)), "Missing fresh Chinese/space extraction")
    before, after = report.get("archive_before", {}), report.get("archive_after", {})
    require(before == after and before.get("checked_files", 0) > 0 and
            before.get("checked_bytes", 0) > 0, "Extracted candidate was not verified before and after inference")
    require(Path(before.get("path", "")).is_file() and sha256(before["path"]) == before.get("sha256"),
            "Tested candidate archive is missing or changed")
    cases = report.get("cases", [])
    require([case.get("name") for case in cases] == ["default", "custom-root", "individual-renamed"],
            "Missing real inference for one or more model path modes")
    entries = json.loads((Path(root) / "models.manifest.json").read_text(encoding="utf-8"))["models"]
    for case in cases:
        name, paths = case["name"], case.get("expected_model_paths", {})
        if name == "default":
            expected = {entry["id"]: str(bundle / entry["local"]) for entry in entries}
        elif name == "custom-root":
            expected = {entry["id"]: str(bundle.parent / "自定义 模型库" /
                        Path(entry["local"]).relative_to("models")) for entry in entries}
        else:
            expected = paths
            require(case.get("image_start") is True, "Missing real image-start inference")
            require(all(Path(path).is_relative_to(bundle.parent / "独立 文件路径") for path in paths.values()),
                    "Renamed weights were not selected individually")
            require(all(Path(paths.get(entry["id"], "")).name != Path(entry["local"]).name for entry in entries),
                    "Individual-file acceptance did not use renamed checkpoints")
        verify_selected_paths(expected, case.get("model_paths", {}), case.get("loaded_model_paths", {}))
        verify_video(case.get("video", {}), 25 if name == "individual-renamed" else 24, 640, 384)
        verify_resources(case.get("resources", {}))
    origins = report.get("module_origins", {})
    require(set(origins) == {"python", "prefix", "torch", "triton", "numpy", "av", "comfy", "hy_nomas", "nomas_app"}
            and all(Path(path).resolve().is_relative_to(bundle) for path in origins.values()),
            "Missing bundled Python/module origins, or a development-directory import")
