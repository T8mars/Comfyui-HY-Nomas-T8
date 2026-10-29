"""CPU regressions for archive budgets, cancellation, and transactional Drive turns.

Run with tests/run_cpu_lifecycle_tests.py for an explicitly mocked ComfyUI host.
The tests do not load checkpoints or submit GPU operations.
"""
import dataclasses
import gc
import sys
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

PACK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK))

from hy_nomas import world as W
from hy_nomas.lifetime import sampling_cleanup


def tiny_world(limit=1.0):
    q = torch.zeros((2, 1, 2), dtype=torch.int8)
    scale = torch.ones((2, 1, 1))
    record = W.SegmentRecord(0, 0, 1, 1, 1, (q,), (q.clone(),), (scale,), (scale.clone(),))
    return W.World(records=(record,), latents=(torch.zeros(1, 32, 1, 1, 1),), max_archive_gib=limit)


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf"), True])
def test_invalid_budget(limit):
    with pytest.raises(ValueError):
        W.World(max_archive_gib=limit)


def test_world_exposes_archive_and_latents_to_host_ram_cache():
    world = tiny_world()
    tensors = world._comfy_cache_tensors()
    assert all(isinstance(tensor, torch.Tensor) and tensor.device.type == 'cpu' for tensor in tensors)
    assert sum(t.numel() * t.element_size() for t in tensors) == world.nbytes() + sum(
        t.numel() * t.element_size() for t in world.latents)


def test_budget_uses_actual_archive_geometry_and_rejects_commit():
    world = tiny_world()
    assert world.check_budget(1, 1, 1) == world.nbytes() * 2
    capped = dataclasses.replace(world, max_archive_gib=(world.nbytes() * 2 - 1) / 2**30)
    with pytest.raises(ValueError, match="budget"):
        capped.with_segment(world.records[0], world.latents[0])
    assert len(capped.records) == 1
    assert world.stats()["archive_bytes"] == world.nbytes()


def test_release_and_stale_sampler_finalizer():
    history = tiny_world().history()
    run1 = torch.tensor(1)
    history.layers("cpu", torch.bfloat16, run1)
    old_staged = next(iter(history._device_cache.values()))
    history.release()
    assert old_staged == []
    run2 = torch.tensor(2)
    history.layers("cpu", torch.bfloat16, run2)
    del run1
    gc.collect()
    assert history._device_cache  # old finalizer must not clear run2
    ref = weakref.ref(history)
    del history
    gc.collect()
    assert ref() is None  # sampler lifetime must not retain the CPU archive


@pytest.mark.parametrize("failure", [False, True])
def test_native_sampler_wrapper_cleans_up(failure, monkeypatch):
    import comfy.model_management
    events = []
    monkeypatch.setattr(comfy.model_management, "unload_all_models", lambda: events.append("offload"))
    world = tiny_world()
    history = world.history()
    history.layers("cpu", torch.bfloat16)

    class Executor:
        class_obj = SimpleNamespace(original_conds={"positive": [{"nomas": {"world": world}}]})

        def __call__(self, *args, **kwargs):
            assert events == ["offload"]
            if failure:
                raise RuntimeError("sampler cancelled")
            return "sampled"

    if failure:
        with pytest.raises(RuntimeError, match="cancelled"):
            sampling_cleanup(Executor())
    else:
        assert sampling_cleanup(Executor()) == "sampled"
    assert history._device_cache == {}


def test_sampler_offload_failure_also_releases_history(monkeypatch):
    import comfy.model_management
    world = tiny_world()
    history = world.history()
    history.layers("cpu", torch.bfloat16)
    def fail_offload():
        raise RuntimeError("offload failed")
    monkeypatch.setattr(comfy.model_management, "unload_all_models", fail_offload)
    executor = SimpleNamespace(class_obj=SimpleNamespace(original_conds={"positive":[{"nomas":{"world":world}}]}))
    with pytest.raises(RuntimeError, match="offload failed"):
        sampling_cleanup(executor)
    assert history._device_cache == {}


def test_budget_failure_precedes_model_loading_and_releases_history(monkeypatch):
    from hy_nomas import session
    world = tiny_world()
    world = dataclasses.replace(world, max_archive_gib=world.nbytes() / 2**30)
    history = world.history()
    history.layers("cpu", torch.bfloat16)
    monkeypatch.setattr(session.comfy.model_management, "load_models_gpu",
                        lambda *a, **k: pytest.fail("budget must fail before GPU loading"))
    positive = [[torch.zeros(1, 1, 2), {"nomas": {"world": world, "frames": 1, "text_tokens": 1}}]]
    with pytest.raises(ValueError, match="budget"):
        session.extend_world(None, positive, {"samples": torch.zeros(1, 32, 1, 1, 1)})
    assert history._device_cache == {}


def test_model_loading_failure_releases_history(monkeypatch):
    from hy_nomas import session
    world = tiny_world()
    history = world.history()
    history.layers("cpu", torch.bfloat16)

    def fail(*args, **kwargs):
        raise RuntimeError("loading failed")

    monkeypatch.setattr(session.comfy.model_management, "load_models_gpu", fail)
    positive = [[torch.zeros(1, 1, 2), {"nomas": {"world": world, "frames": 1, "text_tokens": 1}}]]
    with pytest.raises(RuntimeError, match="loading failed"):
        session.extend_world(None, positive, {"samples": torch.zeros(1, 32, 1, 1, 1)})
    assert history._device_cache == {}


@pytest.mark.parametrize("cancel", [False, True])
def test_decode_failure_clears_layouts(monkeypatch, cancel):
    from hy_nomas import pixelumm
    calls = []

    def interruption():
        calls.append(1)
        if cancel and len(calls) >= 2:
            raise RuntimeError("cancelled")

    class Decoder:
        local_attention_tile = 0

        def __call__(self, *args):
            raise RuntimeError("decode failed")

    monkeypatch.setattr(pixelumm.comfy.model_management, "throw_exception_if_processing_interrupted", interruption)
    pixelumm._LAYOUTS["test"] = torch.zeros(1)
    with pytest.raises(RuntimeError, match="cancelled|decode failed"):
        pixelumm.decode_clip(Decoder(), torch.zeros(1, 32, 1, 1, 1), device="cpu")
    assert pixelumm._LAYOUTS == {}


def prepared_drive(monkeypatch):
    from hy_nomas import drive
    source = (PACK / "docs/upstream/example_prompts.json").read_text(encoding="utf-8")
    active = drive.DriveSession("test", source, 32, 32, 1.0, 42, 18)
    previous = tiny_world()
    active.world = previous
    active.decoded_end = 1
    active.turns.append({"prompt": "previous"})
    candidate = previous.with_segment(previous.records[0], torch.zeros(1, 32, 6, 1, 1))
    monkeypatch.setattr(active, "encode", lambda *a: (torch.zeros(1, 1, 3584), {"text_tokens": 1}))
    monkeypatch.setattr(drive.comfy.sample, "prepare_noise", lambda latent, seed: torch.zeros_like(latent))
    monkeypatch.setattr(drive.comfy.sample, "sample", lambda *a, **k: torch.zeros(1, 32, 6, 1, 1))
    monkeypatch.setattr(drive.S, "extend_world", lambda *a, **k: candidate)
    monkeypatch.setattr(drive.comfy.model_management, "throw_exception_if_processing_interrupted", lambda: None)
    monkeypatch.setattr(drive, "send", lambda *a, **k: None)
    monkeypatch.setattr(drive, "jpeg_frames", lambda frames: ["encoded"])
    return drive, active, previous, candidate


def test_drive_decode_failure_does_not_commit_and_can_retry(monkeypatch):
    drive, active, previous, candidate = prepared_drive(monkeypatch)

    def fail(*a, **k):
        raise RuntimeError("decode failed")

    monkeypatch.setattr(drive.pixelumm, "decode_new_frames", fail)
    with pytest.raises(RuntimeError, match="decode failed"):
        active.run_turn(None, None, None, steer=False)
    assert active.world is previous and active.decoded_end == 1 and len(active.turns) == 1
    monkeypatch.setattr(drive.pixelumm, "decode_new_frames", lambda *a, **k: torch.zeros(24, 32, 32, 3, dtype=torch.uint8))
    active.run_turn(None, None, None, steer=False)
    assert active.world is candidate and active.decoded_end == 7 and len(active.turns) == 2


def test_drive_reset_during_decode_cannot_resurrect_session(monkeypatch):
    drive, active, previous, _ = prepared_drive(monkeypatch)
    previous.history().layers("cpu", torch.bfloat16)

    def reset(*a, **k):
        active.close()
        return torch.zeros(24, 32, 32, 3, dtype=torch.uint8)

    monkeypatch.setattr(drive.pixelumm, "decode_new_frames", reset)
    with pytest.raises(RuntimeError, match="reset"):
        active.run_turn(None, None, None, steer=False)
    assert active.world is None and active.turns == []
    assert previous.history()._device_cache == {}


def test_drive_jpeg_failure_does_not_commit(monkeypatch):
    drive, active, previous, _ = prepared_drive(monkeypatch)
    monkeypatch.setattr(drive.pixelumm, "decode_new_frames", lambda *a, **k: torch.zeros(1))

    def fail(frames):
        raise RuntimeError("JPEG failed")

    monkeypatch.setattr(drive, "jpeg_frames", fail)
    with pytest.raises(RuntimeError, match="JPEG failed"):
        active.run_turn(None, None, None, steer=False)
    assert active.world is previous and active.decoded_end == 1 and len(active.turns) == 1


def test_drive_text_cache_is_bounded_and_detached(monkeypatch):
    from hy_nomas import drive
    source = (PACK / "docs/upstream/example_prompts.json").read_text(encoding="utf-8")
    active = drive.DriveSession("test", source, 32, 32, 1.0, 42, 18)
    monkeypatch.setattr(drive.T, "encode_segment", lambda *a: (torch.ones(1, requires_grad=True), {}))
    for index in range(17):
        states, _ = active.encode(None, str(index))
        assert states.device.type == "cpu" and not states.requires_grad
    assert len(active._text_cache) == 16 and "0" not in active._text_cache
    active.close()
    assert active._text_cache == {}


def test_budget_workflow_has_consistent_links():
    import json
    workflow = json.loads((PACK / "workflows/T8 - Budgeted world.json").read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in workflow["nodes"]}
    assert len(nodes) == len(workflow["nodes"])
    for link_id, source, source_slot, target, target_slot, kind in workflow["links"]:
        out = nodes[source]["outputs"][source_slot]
        inp = nodes[target]["inputs"][target_slot]
        assert link_id in out["links"] and inp["link"] == link_id
        assert out["type"] == inp["type"] == kind
    assert {"T8WorldBudget", "T8WorldStats"} <= {n["type"] for n in nodes.values()}


@pytest.mark.parametrize("dtype", [torch.float32, torch.float16, torch.bfloat16])
def test_chunked_kv_quantization_preserves_full_tensor_result(dtype):
    from hy_nomas.world import quantize_kv
    x = torch.randn(4105, 3, 128, generator=torch.Generator().manual_seed(42)).to(dtype)
    x[0].zero_()
    x[2048, 0, :5] = torch.tensor([-127, -126.5, -.5, .5, 126.5], dtype=dtype)
    xf = x.float()
    peak = xf.abs().amax(dim=-1, keepdim=True)
    scale = torch.where(peak > 0, peak / 127, torch.ones_like(peak))
    scaled = xf / scale
    reference = torch.where(scaled >= 0, torch.floor(scaled + .5), torch.ceil(scaled - .5)).clamp_(-127, 127).to(torch.int8)
    actual, actual_scale = quantize_kv(x)
    assert torch.equal(actual, reference)
    assert torch.equal(actual_scale, scale)


def test_fp32_output_blender_preserves_bf16_overlap_rounding():
    from hy_nomas.pixelumm import TemporalBlender
    original = TemporalBlender(20, "cpu")
    single_buffer = TemporalBlender(20, "cpu", output_dtype=torch.float32)
    generator = torch.Generator().manual_seed(42)
    for start in (0, 5, 10):
        part = torch.randn(1, 3, 10, 4, 4, generator=generator).to(torch.bfloat16)
        original.add(part, start)
        single_buffer.add(part, start)
    assert torch.equal(original.result().float(), single_buffer.result())


def test_full_decode_reserves_uncropped_cpu_output_before_loading_decoder(monkeypatch):
    from types import SimpleNamespace
    from hy_nomas import pixelumm
    calls = []
    monkeypatch.setattr(pixelumm.comfy.model_management, "unload_all_models", lambda:calls.append("unload"))
    def release(target, free_active=False):
        calls.append(("reserve",target,free_active))
        return 10**30  # The host's estimate is deliberately not used as actual available RAM.
    monkeypatch.setattr(pixelumm.comfy.memory_management, "extra_ram_release", release)
    monkeypatch.setattr(pixelumm.comfy.model_management, "load_models_gpu", lambda *a,**k:calls.append("load"))
    def clip(*args, **kwargs):
        calls.append("decode")
        return torch.zeros(1,3,4,1,1)
    monkeypatch.setattr(pixelumm,"decode_clip",clip)
    images = pixelumm.decode(SimpleNamespace(load_device="cpu",model=object()),torch.zeros(1,32,60,22,40),pixel_frames=1)
    assert calls == ["unload",("reserve",8*2**30+240*704*1280*3*4,True),"load","decode"]
    assert images.shape == (1,1,1,3)
