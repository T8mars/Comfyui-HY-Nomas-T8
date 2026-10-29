"""Real GPU acceptance scenarios. No report is marked passed without generated, decoded MP4s."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.engine import NativeEngine
from nomas_app.resources import ResourceMonitor
from scripts.acceptance_evidence import require, source_snapshot


def inspect_clip(path):
    import av
    import numpy as np
    count, stds, first, last, previous, motion = 0, [], None, None, None, []
    with av.open(str(path)) as container:
        for frame in container.decode(video=0):
            array = frame.to_ndarray(format="rgb24")
            if first is None:
                first = array
            if previous is not None:
                motion.append(float(np.abs(array.astype(np.int16) - previous.astype(np.int16)).mean()))
            previous = array
            last = array
            stds.append(float(array.std()))
            count += 1
        rate = float(container.streams.video[0].average_rate)
    if not count or min(stds) < 1:
        raise AssertionError("Output is empty or nearly uniform")
    return {"frames": count, "fps": rate, "seconds": count/rate,
            "width": first.shape[1], "height": first.shape[0],
            "min_frame_std": min(stds), "mean_adjacent_frame_mae": float(np.mean(motion)) if motion else 0,
            "first_last_mae": float(np.abs(first.astype(float)-last).mean())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["short", "hd", "continuation", "image"], default="short")
    parser.add_argument("--image")
    args = parser.parse_args()
    if sys.flags.optimize:
        parser.error("Run acceptance without -O: all frame/resource assertions must remain enabled")
    if args.scenario == "image" and not args.image:
        parser.error("--scenario image requires --image")
    engine = NativeEngine()
    config = {"width": 640, "height": 384, "seconds": 1, "steps": 8, "seed": 42, "cache_gib": 12,
              "subject": "a small orange fox", "appearance": "A small orange fox with white chest and fluffy tail.",
              "style": "Cinematic fantasy", "perspective": "third-person observer",
              "scene": "A quiet stone path winds through a luminous forest. Soft morning light filters through tall trees. Moss-covered rocks and blue wildflowers line the path.",
              "action": "walks slowly forward along the path", "matmul": "int8", "decoder_matmul": "fp8"}
    if args.scenario == "hd":
        config.update(width=1280, height=704, seconds=5)
    elif args.scenario == "image":
        config["image_path"] = str(Path(args.image).resolve())
    report = {"scenario": args.scenario, "config": config, "passed": False, "runs": [],
              "source_files_before": source_snapshot(ROOT)}
    current_monitor = None
    def progress(message, value):
        if current_monitor is not None:
            current_monitor.stage = message
        print(f"{time.strftime('%H:%M:%S')} {message} {value:.0%}", flush=True)
    def run_turn(turn):
        nonlocal current_monitor
        monitor = ResourceMonitor()
        current_monitor = monitor
        try:
            with monitor:
                result = engine.generate(turn, progress)
        except BaseException:
            report["failed_run_resources"] = monitor.result()
            raise
        result["resources"] = monitor.result()
        current_monitor = None
        resources = result["resources"]
        if (resources["monitor_error"] is not None or resources["samples"] < 2
                or resources["gpu_peak_used_gib"] is None or resources["system_min_available_ram_gib"] < 4):
            report["failed_run_resources"] = resources
            report["resource_margin_result"] = result
            raise RuntimeError("Resource verification failed or less than 4 GiB physical RAM remained; inspect resource evidence before retrying")
        return result
    try:
        report["runs"].append(run_turn(config))
        if args.scenario == "continuation":
            report["runs"].append(run_turn({**config, "camera": "D+Left 1",
                "action": "turns its head to look at the camera"}))
        report["videos"] = [inspect_clip(ROOT / "outputs" / clip["file"]) for clip in engine.clips]
        assert len(report["videos"]) == (2 if args.scenario == "continuation" else 1)
        for video in report["videos"]:
            expected_frames = round(config["seconds"] * 24) + (1 if args.scenario == "image" else 0)
            assert video["frames"] == expected_frames and video["fps"] == 24, video
            assert (video["width"], video["height"]) == (config["width"], config["height"]), video
            assert video["mean_adjacent_frame_mae"] > .01, video
        report["export"] = engine.export()
        merged = inspect_clip(ROOT / "outputs" / report["export"]["file"])
        assert merged["frames"] == sum(video["frames"] for video in report["videos"])
        report["merged_video"] = merged
        report["before_reset"] = engine.snapshot()
        engine.reset()
        report["after_reset"] = engine.snapshot()
        assert report["after_reset"]["archive_gib"] == 0 and not report["after_reset"]["models_loaded"]
        report["source_files_after"] = source_snapshot(ROOT)
        require(report["source_files_after"] == report["source_files_before"], "Inference sources changed during acceptance")
        report["passed"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        engine.reset()
        folder = ROOT / "evidence"
        folder.mkdir(exist_ok=True)
        (folder / f"benchmark-{args.scenario}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
