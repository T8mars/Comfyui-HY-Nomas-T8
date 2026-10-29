"""Run lifecycle regressions with real CPU tensors and mocked ComfyUI host services.

This does not validate CUDA kernels or ComfyUI installation compatibility. It avoids
importing Triton's GPU autotuner while testing cancellation and state transitions.
"""
import contextlib
import os
import sys
import types
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

import torch
import pytest


def module(name, **attributes):
    value = types.ModuleType(name)
    value.__dict__.update(attributes)
    sys.modules[name] = value
    if "." in name:
        parent, child = name.rsplit(".", 1)
        setattr(sys.modules[parent], child, value)
    return value


def forbidden(*args, **kwargs):
    raise AssertionError("CPU lifecycle tests must not load models or execute a real sampler")


module("comfy", __path__=[])
module("comfy.model_management", load_models_gpu=forbidden,
       unload_all_models=lambda: None,
       throw_exception_if_processing_interrupted=lambda: None,
       cuda_device_context=lambda device: contextlib.nullcontext())
module("comfy.sample", sample=forbidden, prepare_noise=forbidden)
module("comfy.utils")
module("comfy.memory_management", extra_ram_release=lambda *args, **kwargs: 0)


class ManualCast:
    Linear = torch.nn.Linear
    Conv3d = torch.nn.Conv3d


module("comfy.ops", manual_cast=ManualCast)
module("comfy.quant_ops", QuantizedTensor=type("QuantizedTensor", (), {}))
sys.modules["comfy_kitchen.tensor"] = None
import hy_nomas
module("hy_nomas.fused", available=lambda value: False)

raise SystemExit(pytest.main(["-q", str(PACK / "tests/test_t8_lifetime.py"),
                              str(PACK / "tests/test_drive.py")]))
