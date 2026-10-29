"""Execute the frontend-exported native workflow and inspect its actual saved video."""
import argparse
import asyncio
import copy
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nomas_app.models import ModelLocations
from nomas_app.prompts import add_turn
from nomas_app.resources import ResourceMonitor
from scripts.benchmark import inspect_clip
from scripts.acceptance_evidence import require, source_snapshot, verify_default_graph

BASE = "http://127.0.0.1:8189"


class ExecutionTrace:
    """Associate resource samples with the actual native node execution events."""
    def __init__(self, client_id, monitor, prompt):
        self.client_id, self.monitor, self.prompt = client_id, monitor, prompt
        self.events = []
        self.error = None
        self.ready, self.stopped = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            asyncio.run(self.listen())
        except Exception as error:
            self.error = str(error)
            self.ready.set()

    async def listen(self):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(BASE.replace("http:", "ws:") + "/ws?clientId=" + self.client_id) as socket:
                self.ready.set()
                while not self.stopped.is_set():
                    try:
                        message = await socket.receive(timeout=.5)
                    except asyncio.TimeoutError:
                        continue
                    if message.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(message.data)
                        if event.get("type") == "executing":
                            node = event["data"].get("node")
                            kind = self.prompt.get(str(node), {}).get("class_type", "finished")
                            self.monitor.stage = f"{node}: {kind}"
                            self.events.append({"node":node,"class_type":kind,"time_unix":time.time()})
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        if not self.stopped.is_set():
                            raise RuntimeError("ComfyUI execution event connection closed")
                        break

    def start(self):
        self.thread.start()
        if not self.ready.wait(15) or self.error:
            self.stopped.set()
            raise RuntimeError("Could not observe native node stages: " + str(self.error))

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            self.error = "Execution event observer did not stop"


def api(route, payload=None):
    request = urllib.request.Request(BASE + route,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"ComfyUI {error.code}: {error.read().decode('utf-8', errors='replace')}") from error


def prepare(width, height, seconds, shipped_defaults=False):
    source = ROOT / "evidence" / "comfy-per-segment-api.json"
    if not source.is_file():
        raise RuntimeError("Run tests/comfy_workflow_import.cjs against the bundled ComfyUI first")
    exported = json.loads(source.read_text(encoding="utf-8"))
    prompt = copy.deepcopy(exported["output"])
    config = {"seconds": seconds, "subject": "a small orange fox",
              "appearance": "A small orange fox with a white chest and a fluffy tail.",
              "style": "Cinematic fantasy", "perspective": "third-person observer",
              "scene": "A quiet stone path in a luminous forest. Morning light filters through tall trees and moss-covered rocks.",
              "action": "walks slowly forward along the path"}
    document = add_turn({**config, "action": "turns its head toward the camera"}, add_turn(config))
    paths = ModelLocations().resolve()
    for node in prompt.values():
        inputs, kind = node["inputs"], node["class_type"]
        if kind == "HyNomasWorldPrompt" and not shipped_defaults:
            inputs["world_json"] = json.dumps(document, ensure_ascii=False)
        elif kind == "HyNomasSegment" and not shipped_defaults:
            inputs.update(width=width, height=height)
        elif kind == "HyNomasLoadDiT":
            inputs["dit_name"] = paths["dit"].name
        elif kind == "CLIPLoader":
            inputs["clip_name"] = paths["text_encoder"].name
        elif kind == "HyNomasLoadDecoder":
            inputs["decoder_name"] = paths["decoder"].name
        elif kind == "SaveVideo":
            inputs["filename_prefix"] = "hy_nomas/acceptance"
    assert sum(node["class_type"] == "KSampler" for node in prompt.values()) == 2
    if shipped_defaults:
        verify_default_graph(ROOT, prompt)
    return prompt, {"frontend_export_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "source_workflow": "Hy-Nomas world (per segment).json",
                    "shipped_default_prompt_and_dimensions": shipped_defaults,
                    "config": {"width": width, "height": height, "segment_seconds": seconds,
                               "segments": 2, "steps": 8, "cfg": 1, "sampler": "euler/simple"}}


def server_process():
    import psutil
    candidates = {connection.pid for connection in psutil.net_connections(kind="tcp")
                  if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 8189 and connection.pid}
    if len(candidates) != 1:
        raise RuntimeError("Cannot identify the local ComfyUI process for RAM measurements")
    process = psutil.Process(candidates.pop())
    if not Path(process.exe()).resolve().is_relative_to(ROOT / "runtime") or not any("run_comfy.py" in value for value in process.cmdline()):
        raise RuntimeError("Port 8189 is not the bundled ComfyUI process")
    return process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--seconds", type=float, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--shipped-defaults", action="store_true", help="Execute the unchanged 1280x704, two 5-second segments and multi-action prompt")
    args = parser.parse_args()
    if sys.flags.optimize:
        parser.error("Run acceptance without -O: all frame/resource assertions must remain enabled")
    if args.shipped_defaults:
        args.width, args.height, args.seconds = 1280, 704, 5
    prompt, report = prepare(args.width, args.height, args.seconds, args.shipped_defaults)
    (ROOT / "evidence" / "comfy-run-prompt.json").write_text(json.dumps(prompt, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.prepare_only:
        print(json.dumps({"prepared": True, "executed": False, **report}, indent=2))
        return
    report.update(passed=False, scope="Two-segment stock KSampler native workflow, actual MP4 and host process resources",
                  source_files_before=source_snapshot(ROOT))
    monitor = trace = None
    started = time.perf_counter()
    try:
        ModelLocations().required_paths()
        queue = api("/queue")
        if queue["queue_running"] or queue["queue_pending"]:
            raise RuntimeError("ComfyUI is busy; do not enqueue a concurrent acceptance run")
        try:
            with urllib.request.urlopen("http://127.0.0.1:7866/api/status", timeout=10) as response:
                studio = json.load(response)
                if studio["job"]["state"] == "running":
                    raise RuntimeError("Local Studio is busy; do not start concurrent GPU work")
                report["studio_server_present"] = True
        except urllib.error.URLError as error:
            if not isinstance(error.reason, ConnectionRefusedError) and getattr(error.reason, "winerror", None) != 10061:
                raise
            report["studio_server_present"] = False
        process = server_process()
        report["server"] = {"pid": process.pid, "executable": process.exe(), "command": process.cmdline(),
                            "process_create_time": process.create_time()}
        receipt = ROOT / "evidence/comfy-host-source.json"
        require(receipt.is_file(), "ComfyUI host has no startup source receipt; use the current launcher")
        report["host_startup"] = json.loads(receipt.read_text(encoding="utf-8"))
        require(report["host_startup"] == {"pid": process.pid, "process_create_time": process.create_time(),
                "source_files": report["source_files_before"]}, "ComfyUI host was started with different sources")
        monitor = ResourceMonitor(process_id=process.pid)
        client_id = uuid.uuid4().hex
        trace = ExecutionTrace(client_id, monitor, prompt)
        trace.start()
        with monitor:
            submitted = api("/prompt", {"prompt": prompt, "client_id": client_id})
            report["prompt_id"] = submitted["prompt_id"]
            print(f"Queued native workflow {report['prompt_id']}", flush=True)
            while True:
                record = api("/history/" + report["prompt_id"]).get(report["prompt_id"])
                if record is not None:
                    break
                if time.perf_counter() - started > args.timeout:
                    report["observation_timeout"] = True
                    raise TimeoutError("Workflow may still be running; inspect prompt_id and /queue before taking any further action")
                time.sleep(2)
            report["history"] = record
            if record["status"]["status_str"] != "success" or not record["status"]["completed"]:
                raise RuntimeError("ComfyUI execution failed; see recorded history messages")
            videos = []
            output_root = (ROOT / "outputs" / "comfy").resolve()
            for output in record["outputs"].values():
                for item in output.get("images", []):
                    if item.get("type") != "output":
                        continue
                    path = (output_root / item["subfolder"] / item["filename"]).resolve()
                    if not path.is_relative_to(output_root) or path.suffix.lower() not in (".mp4", ".webm", ".mkv"):
                        raise ValueError("Unexpected video output location")
                    videos.append({"path": str(path), **inspect_clip(path)})
            assert len(videos) == 1, videos
            assert videos[0]["frames"] == round(2 * args.seconds * 24), videos
            assert videos[0]["fps"] == 24, videos
            assert (videos[0]["width"], videos[0]["height"]) == (args.width, args.height), videos
            assert videos[0]["mean_adjacent_frame_mae"] > .01, videos
            report["videos"] = videos
        report["resources"] = monitor.result()
        assert report["resources"]["monitor_error"] is None, report["resources"]
        assert report["resources"]["samples"] >= 2 and report["resources"]["gpu_peak_used_gib"] is not None
        assert report["resources"]["system_min_available_ram_gib"] >= 4, report["resources"]
        assert trace.error is None and any(event["class_type"] == "KSampler" for event in trace.events)
        report["source_files_after"] = source_snapshot(ROOT)
        require(report["source_files_after"] == report["source_files_before"], "Inference sources changed during acceptance")
        report["passed"] = True
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - started
        if monitor is not None:
            report["resources"] = monitor.result()
        if trace is not None:
            trace.close()
            report["execution_stages"] = trace.events
            report["execution_observer_error"] = trace.error
            if trace.error:
                report["passed"] = False
                report.setdefault("error", "Native execution observer failed: " + trace.error)
        (ROOT / "evidence" / "comfy-workflow.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "history"}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
