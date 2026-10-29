"""In-process native inference shared with the shipped ComfyUI nodes."""
import copy
import gc
import json
import threading
import time
import uuid
from pathlib import Path

from .bootstrap import ROOT, configure
from .prompts import add_turn, image_start_document
from .models import ModelLocations


class Cancelled(RuntimeError):
    pass


class NativeEngine:
    def __init__(self):
        self.cancel_event = threading.Event()
        self.world = None
        self.document = None
        self.clips = []
        self.model = self.clip = self.decoder = self.encoder = None
        self.dimensions = None
        self.initialized = False
        self.load_signature = None
        self.session_id = uuid.uuid4().hex[:12]

    def initialize(self):
        if self.initialized:
            return
        configure()
        import torch
        import comfy.model_management as mm
        self.torch, self.mm = torch, mm
        self.initialized = True

    def cancel(self):
        self.cancel_event.set()
        if self.initialized:
            self.mm.interrupt_current_processing(True)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled("任务已取消，已完成片段保留。")

    def load(self, progress, matmul="int8", decoder_matmul="fp8"):
        self.initialize()
        from hy_nomas import loader, pixelumm
        import comfy.sd
        paths = ModelLocations().required_paths()
        if matmul not in ("int8", "exact") or decoder_matmul not in ("fp8", "dequant"):
            raise ValueError("不支持的模型运算模式。")
        signature = (matmul, decoder_matmul, tuple((key, path, Path(path).stat().st_mtime_ns)
                                                 for key, path in sorted(paths.items())))
        if self.model is not None and self.load_signature != signature:
            self.unload()
        if self.model is None:
            try:
                import psutil
                required = sum(Path(path).stat().st_size for path in paths.values()) + 4 * 2**30
                available = psutil.virtual_memory().available
                if available < required:
                    raise MemoryError(f"首次加载需预留约 {required/2**30:.1f} GiB 系统内存，当前可用 "
                                      f"{available/2**30:.1f} GiB。请先释放其他应用占用后重试。")
                self.check_cancel()
                progress("加载 INT8 主模型", 0)
                self.model = loader.load_dit(paths["dit"], matmul=matmul, disable_dynamic=True)
                self.check_cancel()
                progress("加载 Qwen 文本编码器", 0)
                self.clip = comfy.sd.load_clip([paths["text_encoder"]], clip_type=comfy.sd.CLIPType.QWEN_IMAGE)
                self.check_cancel()
                progress("加载 FP8 视频解码器", 0)
                self.decoder = pixelumm.load_decoder(paths["decoder"], matmul=decoder_matmul, disable_dynamic=True)
                self.check_cancel()
                self.load_signature = signature
            except BaseException:
                self.unload()
                raise
        self.model_paths = paths

    def release_working(self):
        if not self.initialized:
            return
        from hy_nomas import pixelumm
        if self.world is not None:
            self.world.release_history()
        pixelumm.clear_runtime_caches()
        gc.collect()
        self.mm.soft_empty_cache()

    def unload(self):
        if self.initialized:
            from hy_nomas import text
            self.mm.unload_all_models()
            self.model = self.clip = self.decoder = self.encoder = None
            self.load_signature = None
            text._TOKENIZERS.clear()
            self.release_working()

    def reset(self, unload=True):
        self.release_working()
        self.world = None
        self.document = None
        self.clips = []
        self.dimensions = None
        self.session_id = uuid.uuid4().hex[:12]
        if unload:
            self.unload()
        gc.collect()
        if self.initialized:
            self.mm.soft_empty_cache()

    def snapshot(self):
        result = {"session_id": self.session_id, "segments": len(self.clips),
                  "archive_gib": self.world.nbytes() / 2**30 if self.world is not None else 0,
                  "world_latent_frames": self.world.total_frames if self.world is not None else 0,
                  "clips": copy.deepcopy(self.clips), "models_loaded": self.model is not None}
        if self.initialized:
            result.update(vram_allocated_gib=self.torch.cuda.memory_allocated() / 2**30,
                          vram_reserved_gib=self.torch.cuda.memory_reserved() / 2**30)
        return result

    def generate(self, config, progress):
        started = time.perf_counter()
        self.initialize()
        self.mm.interrupt_current_processing(False)
        import dataclasses
        import psutil
        from hy_nomas import camera, dcae, pixelumm, session
        from hy_nomas.world import World
        import comfy.sample
        from .video import write_video
        width, height = int(config.get("width", 832)), int(config.get("height", 480))
        if width % 32 or height % 32 or not (32 <= width <= 1280 and 32 <= height <= 1280):
            raise ValueError("宽高需为32的倍数，本整合包支持单边最多1280像素。")
        if self.dimensions is not None and self.dimensions != (width, height):
            raise ValueError("续接必须保持原分辨率，请重置世界后更换尺寸。")
        steps, seed = int(config.get("steps", 8)), int(config.get("seed", 42))
        budget = float(config.get("cache_gib", 12))
        if not 1 <= budget <= 32 or not 1 <= steps <= 50:
            raise ValueError("缓存预算范围1–32GiB，步数范围1–50。")
        image_path = config.get("image_path")
        if image_path and self.world is not None:
            raise ValueError("起始图片只用于新世界；续接时请清除图片。")
        document = image_start_document(config) if image_path else add_turn(config, self.document)
        script = session.Script(json.dumps(document, ensure_ascii=False))
        if self.document and document["segments"][:len(self.document["segments"])] != self.document["segments"]:
            raise ValueError("续接时不能修改已生成的段落，请保留原JSON前缀或重置。")
        if self.document and any(document.get(key) != self.document.get(key)
                                 for key in ("Style", "perspective_view", "subject_list")):
            raise ValueError("续接时不能修改世界的主体、风格或视角定义，请重置后再修改。")
        start = len(self.world.records) if self.world is not None else 0
        if len(script) <= start:
            raise ValueError("结构化提示词中没有新增片段。")
        if image_path and len(script) == 1 and config.get("prompt_json", "").strip():
            raise ValueError("图片起始的 JSON 需至少两段：第1段描述图片，第2段描述要生成的动作。")
        self.check_cancel()
        self.torch.cuda.reset_peak_memory_stats()
        self.load(progress, config.get("matmul", "int8"), config.get("decoder_matmul", "fp8"))
        sampling_started = time.perf_counter()
        candidate_world = self.world or World(max_archive_gib=budget)
        candidate_world = dataclasses.replace(candidate_world, max_archive_gib=budget)
        try:
            # Comfy's quantized weights are loaded above as regular tensors. Keep their
            # version counters available for state_dict sizing and device transitions.
            with self.torch.no_grad():
                if image_path:
                    from PIL import Image
                    import numpy as np
                    progress("编码起始图片", 0)
                    if self.encoder is None:
                        self.encoder = dcae.load_encoder(self.model_paths["decoder"], disable_dynamic=True)
                    image = self.torch.from_numpy(np.array(Image.open(image_path).convert("RGB"))).float()[None] / 255
                    candidate_world = session.start_world(self.model, self.clip, self.encoder, document, image,
                                                          width, height, still_frames=script.segment_frames(0),
                                                          seed=seed, max_archive_gib=budget)
                    candidate_world = dataclasses.replace(candidate_world, max_archive_gib=budget)
                    start = 1
                for index in range(start, len(script)):
                    self.check_cancel()
                    if psutil.virtual_memory().available < 4 * 2**30:
                        raise MemoryError("可用系统内存不足4GiB，请关闭其他应用或重置世界。")
                    turn_start = time.perf_counter()
                    progress(f"第 {index + 1} 段：编码提示词", 0)
                    cam = None
                    keys = config.get("camera", "").strip()
                    if keys:
                        cam = camera.CameraPath(camera.simulate_keys(camera.parse_timeline(keys)))
                    positive, latent = session.segment_conditioning(self.clip, script, index, width, height,
                                                                    world=candidate_world, camera=cam)
                    # CFG=1 never evaluates negative conditioning.
                    negative = positive
                    turn_seed = (seed + index) & 0xffffffffffffffff
                    noise = comfy.sample.prepare_noise(latent["samples"], turn_seed)
                    def callback(step, x0, x, total):
                        self.check_cancel()
                        progress(f"第 {index + 1} 段：采样 {step + 1}/{total}", (step + 1) / total * .7)
                    try:
                        samples = comfy.sample.sample(self.model, noise, steps, 1.0, "euler", "simple", positive,
                                                      negative, latent["samples"], seed=turn_seed, callback=callback)
                        self.check_cancel()
                        progress(f"第 {index + 1} 段：写入历史", .75)
                        new_world = session.extend_world(self.model, positive, {"samples": samples}, seed=turn_seed)
                    finally:
                        candidate_world.release_history()
                    self.check_cancel()
                    progress(f"第 {index + 1} 段：解码视频", .85)
                    first = 0 if image_path and not self.clips else candidate_world.total_frames
                    images = pixelumm.decode_new_frames(self.decoder, new_world.full_latent(), first,
                                                        new_world.total_frames, seed=seed,
                                                        progress=lambda done, total: progress(
                                                            f"第 {index + 1} 段：解码窗口 {done}/{total}",
                                                            .85 + .14 * done / total))
                    if image_path and not self.clips:
                        images = session.trim_start(images, new_world)
                    self.check_cancel()
                    directory = ROOT / "outputs" / self.session_id
                    directory.mkdir(parents=True, exist_ok=True)
                    path = directory / f"segment-{len(self.clips) + 1:03d}.mp4"
                    frames = write_video(path, images)
                    del images, samples, noise, latent, positive, negative
                    clip = {"file": str(path.relative_to(ROOT / "outputs")).replace("\\", "/"),
                            "frames": frames, "seconds": frames / 24, "width": width, "height": height,
                            "seed": turn_seed, "elapsed_seconds": time.perf_counter() - turn_start}
                    # Commit only after decode and MP4 writing have both succeeded.
                    self.world = candidate_world = new_world
                    self.dimensions = (width, height)
                    self.document = copy.deepcopy(document)
                    self.document["segments"] = self.document["segments"][:index + 1]
                    self.clips.append(clip)
                    self.release_working()
                    (directory / "session.json").write_text(json.dumps({"prompt": self.document,
                        "config": {k: v for k, v in config.items() if k != "image_path"},
                        "state": self.snapshot()}, ensure_ascii=False, indent=2), encoding="utf-8")
                    progress(f"第 {index + 1} 段已完成", 1)
        finally:
            candidate_world.release_history()
            self.release_working()
        return {"elapsed_seconds": time.perf_counter() - started,
                "prepare_seconds": sampling_started - started,
                "inference_seconds": time.perf_counter() - sampling_started,
                "peak_allocated_gib": self.torch.cuda.max_memory_allocated() / 2**30,
                "peak_reserved_gib": self.torch.cuda.max_memory_reserved() / 2**30,
                "process_rss_gib": psutil.Process().memory_info().rss / 2**30, **self.snapshot()}

    def export(self):
        from .video import join_clips
        output = ROOT / "outputs" / self.session_id / "world.mp4"
        count = join_clips([ROOT / "outputs" / c["file"] for c in self.clips], output)
        return {"file": str(output.relative_to(ROOT / "outputs")).replace("\\", "/"), "frames": count}
