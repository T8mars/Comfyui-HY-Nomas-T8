"""Loopback-only UI server. Inference executes in this process on one worker."""
import argparse
import asyncio
import concurrent.futures
import json
import logging
import threading
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web
from PIL import Image
from .bootstrap import ROOT
from .engine import NativeEngine, Cancelled
from .models import ModelLocations
from .models import LABELS


class Studio:
    def __init__(self):
        self.engine = NativeEngine()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="nomas-gpu")
        self.lock = threading.RLock()
        self.job = {"state": "idle", "message": "准备就绪", "progress": 0}
        self.task = None
        self.locations = ModelLocations()

    def progress(self, message, value):
        with self.lock:
            self.job.update(message=message, progress=value)

    def models(self):
        try:
            return self.locations.status()
        except ValueError as exc:
            return [{"id": item["id"], "label": LABELS[item["id"]], "file": Path(item["local"]).name,
                     "path": str(self.locations.config_file), "ready": False, "error": str(exc), "source": "custom"}
                    for item in self.locations.entries]

    def status(self):
        import psutil
        with self.lock:
            return {"brand": "By T8star", "job": dict(self.job), "models": self.models(),
                    "session": self.engine.snapshot(), "ram_available_gib": psutil.virtual_memory().available / 2**30}

    async def perform(self, action, payload):
        loop = asyncio.get_running_loop()
        try:
            if action == "generate":
                result = await loop.run_in_executor(self.pool, self.engine.generate, payload, self.progress)
            elif action == "export":
                result = await loop.run_in_executor(self.pool, self.engine.export)
            else:
                await loop.run_in_executor(self.pool, self.engine.reset)
                result = self.engine.snapshot()
            with self.lock:
                self.job.update(state="done", message="完成", progress=1, result=result)
            (ROOT / "evidence").mkdir(exist_ok=True)
            (ROOT / "evidence" / f"job-{self.job['id']}.json").write_text(
                json.dumps({"action": action, "job": self.job}, ensure_ascii=False, indent=2), encoding="utf-8")
        except BaseException as exc:
            cancelled = self.engine.cancel_event.is_set() or isinstance(exc, Cancelled)
            logging.exception("Job %s failed", action)
            with self.lock:
                self.job.update(state="cancelled" if cancelled else "error",
                                message="已取消，已完成片段保留" if cancelled else str(exc), progress=0)
        finally:
            self.task = None


@web.middleware
async def local_requests(request, handler):
    host = request.host.split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        raise web.HTTPForbidden(text="Local use only")
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        raise web.HTTPForbidden(text="Cross-origin requests are not allowed")
    return await handler(request)


def make_app(studio=None):
    studio = studio or Studio()
    app = web.Application(client_max_size=25 * 1024**2, middlewares=[local_requests])
    app["studio"] = studio

    async def index(request):
        return web.FileResponse(ROOT / "nomas_app" / "static" / "index.html")

    async def status(request):
        return web.json_response(studio.status())

    async def model_paths(request):
        if request.method == "GET":
            try:
                return web.json_response({"settings": studio.locations.preferences(), "models": studio.models()})
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc))
        if studio.task is not None:
            raise web.HTTPConflict(text="请等待当前任务结束后修改模型路径。")
        if studio.engine.world is not None:
            raise web.HTTPConflict(text="更换模型前请先重置世界，以免将旧模型的历史用于新模型。")
        try:
            payload = await request.json()
            if studio.task is not None:
                raise web.HTTPConflict(text="请等待当前任务结束后修改模型路径。")
            if studio.engine.world is not None:
                raise web.HTTPConflict(text="更换模型前请先重置世界，以免将旧模型的历史用于新模型。")
            settings, rows = studio.locations.save(payload)
        except (ValueError, OSError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"settings": settings, "models": rows})

    async def action(request):
        name = request.match_info["name"]
        if name == "cancel":
            studio.engine.cancel()
            return web.json_response({"ok": True})
        if name not in ("generate", "reset", "export"):
            raise web.HTTPNotFound()
        if studio.task is not None:
            raise web.HTTPConflict(text="当前任务仍在运行，请等待或取消。")
        try:
            payload = await request.json() if request.can_read_body else {}
        except ValueError as exc:
            raise web.HTTPBadRequest(text="Invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise web.HTTPBadRequest(text="Expected an object")
        if studio.task is not None:
            raise web.HTTPConflict(text="当前任务仍在运行，请等待或取消。")
        image_id = payload.pop("image_id", None)
        payload.pop("image_path", None)
        if image_id is not None:
            if not isinstance(image_id, str) or len(image_id) != 32 or any(c not in "0123456789abcdef" for c in image_id):
                raise web.HTTPBadRequest(text="Invalid image ID")
            path = ROOT / "inputs" / f"{image_id}.png"
            if not path.is_file():
                raise web.HTTPBadRequest(text="起始图片不存在，请重新上传。")
            payload["image_path"] = str(path)
        studio.engine.cancel_event.clear()
        with studio.lock:
            studio.job = {"id": uuid.uuid4().hex[:12], "state": "running", "message": "准备任务", "progress": 0}
        studio.task = asyncio.create_task(studio.perform(name, payload))
        return web.json_response({"id": studio.job["id"]}, status=202)

    async def upload(request):
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "image":
            raise web.HTTPBadRequest(text="请选择图片。")
        data = bytearray()
        while chunk := await field.read_chunk():
            data.extend(chunk)
            if len(data) > 20 * 1024**2:
                raise web.HTTPRequestEntityTooLarge(max_size=20*1024**2, actual_size=len(data))
        import io
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 40_000_000:
                    raise ValueError("图片超过4000万像素。")
                image_id = uuid.uuid4().hex
                folder = ROOT / "inputs"
                folder.mkdir(exist_ok=True)
                image.convert("RGB").save(folder / f"{image_id}.png")
        except (OSError, ValueError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        return web.json_response({"id": image_id})

    async def output(request):
        base = (ROOT / "outputs").resolve()
        path = (base / request.match_info["path"]).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.suffix not in (".mp4", ".json"):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def cleanup(app):
        studio.engine.cancel()
        if studio.task:
            await studio.task
        await asyncio.get_running_loop().run_in_executor(studio.pool, studio.engine.reset)
        studio.pool.shutdown(wait=True)

    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/model-paths", model_paths)
    app.router.add_post("/api/model-paths", model_paths)
    app.router.add_post("/api/{name:generate|reset|cancel|export}", action)
    app.router.add_post("/api/upload", upload)
    app.router.add_get("/outputs/{path:.*}", output)
    app.router.add_static("/static", ROOT / "nomas_app" / "static")
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7866)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(ROOT / "logs" / "studio.log", encoding="utf-8"), logging.StreamHandler()])
    app = make_app()
    if args.open:
        async def open_browser(app):
            import webbrowser
            asyncio.get_running_loop().call_later(1, webbrowser.open, f"http://127.0.0.1:{args.port}")
        app.on_startup.append(open_browser)
    web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)


if __name__ == "__main__":
    main()
