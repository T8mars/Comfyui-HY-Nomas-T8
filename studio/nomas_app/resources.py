"""Observe physical GPU memory and process RAM during real acceptance runs."""
import ctypes
import os
import threading
from pathlib import Path


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class NvidiaMemory:
    def __init__(self, index=0):
        library = str(Path(os.environ["WINDIR"]) / "System32" / "nvml.dll") if os.name == "nt" else "libnvidia-ml.so.1"
        self.api = ctypes.CDLL(library)
        self.api.nvmlInit_v2.restype = ctypes.c_int
        self.api.nvmlShutdown.restype = ctypes.c_int
        self.api.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        self.api.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        self.api.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Memory)]
        self.api.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
        self._check(self.api.nvmlInit_v2())
        self.handle = ctypes.c_void_p()
        try:
            self._check(self.api.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(self.handle)))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check(code):
        if code:
            raise RuntimeError(f"NVML returned error {code}")

    def read(self):
        memory = _Memory()
        self._check(self.api.nvmlDeviceGetMemoryInfo(self.handle, ctypes.byref(memory)))
        return {"total": memory.total, "free": memory.free, "used": memory.used}

    def close(self):
        self.api.nvmlShutdown()


class ResourceMonitor:
    """NVML GPU 0 totals include other apps; RAM is for the selected PID (this process by default)."""
    def __init__(self, interval=.2, process_id=None):
        import psutil
        self.psutil = psutil
        self.process = psutil.Process(process_id)
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = None
        self.nvidia = None
        self.samples = 0
        self.peak_rss = 0
        self.min_available_ram = psutil.virtual_memory().available
        self.gpu_baseline = self.gpu_peak = self.gpu_total = None
        self.error = None
        self.stage = "startup"
        self.stages = {}

    def sample(self):
        info = self.process.memory_info()
        self.peak_rss = max(self.peak_rss, info.rss)
        available = self.psutil.virtual_memory().available
        self.min_available_ram = min(self.min_available_ram, available)
        stage = self.stages.setdefault(self.stage, {"samples": 0, "process_peak_rss_gib": 0,
                                                  "system_min_available_ram_gib": available / 2**30})
        stage["samples"] += 1
        stage["process_peak_rss_gib"] = max(stage["process_peak_rss_gib"], info.rss / 2**30)
        stage["system_min_available_ram_gib"] = min(stage["system_min_available_ram_gib"], available / 2**30)
        if self.nvidia is not None:
            memory = self.nvidia.read()
            self.gpu_total = memory["total"]
            self.gpu_peak = max(self.gpu_peak or 0, memory["used"])
            if self.gpu_baseline is None:
                self.gpu_baseline = memory["used"]
        self.samples += 1

    def _run(self):
        try:
            while not self.stop_event.wait(self.interval):
                self.sample()
            self.sample()
        except Exception as exc:
            self.error = str(exc)
        finally:
            if self.nvidia is not None:
                self.nvidia.close()

    def __enter__(self):
        try:
            self.nvidia = NvidiaMemory()
        except (OSError, RuntimeError) as exc:
            self.error = str(exc)
        self.sample()
        self.thread = threading.Thread(target=self._run, name="nomas-resource-monitor", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            self.error = "Resource sampling did not finish within two seconds"

    def result(self):
        gib = lambda value: value / 2**30 if value is not None else None
        return {"sample_interval_seconds": self.interval, "samples": self.samples,
                "process_id": self.process.pid,
                "gpu_index": 0, "gpu_scope": "physical GPU, including other processes",
                "gpu_total_gib": gib(self.gpu_total), "gpu_baseline_used_gib": gib(self.gpu_baseline),
                "gpu_peak_used_gib": gib(self.gpu_peak), "process_peak_sampled_rss_gib": gib(self.peak_rss),
                "system_min_available_ram_gib": gib(self.min_available_ram), "monitor_error": self.error,
                "stages": self.stages.copy()}
