# Hy-Nomas T8 · By T8star

[简体中文](README.md) | **English**

Run the Hy-Nomas video world model natively in ComfyUI: text/image starts, segment continuation, camera paths and keyboard interaction. The standard KSampler samples the model directly; ComfyUI manages model residency and history.

[Models](https://huggingface.co/t8star/HY-Nomas-Comfy) · [89-second showcase](https://huggingface.co/t8star/HY-Nomas-Comfy/blob/main/samples/hy-nomas-15-worlds.mp4) · [Workflows](workflows) · [Registry](https://registry.comfy.org/nodes/hy-nomas-t8)

[![15 newly generated characters and settings](assets/showcase.jpg)](https://huggingface.co/t8star/HY-Nomas-Comfy/blob/main/samples/hy-nomas-15-worlds.mp4)

## Installation

Search **Hy-Nomas T8** in ComfyUI-Manager (publisher: `t8star`), install and restart. While a Registry version awaits review, install from the Git URL:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/T8mars/Comfyui-HY-Nomas-T8.git
```

Install dependencies with the Python that runs this ComfyUI: `python -m pip install -r Comfyui-HY-Nomas-T8/requirements.txt`. ComfyUI and PyTorch are supplied by the host; this package does not replace PyTorch automatically.

**Tested:** Windows, ComfyUI 0.42.1, Python 3.12.9, PyTorch 2.10.0+cu130 and Triton Windows 3.6.0.post26. Recommended: NVIDIA **24GB VRAM, 64GB RAM**, and about 28GB of disk for weights. Keep at least 30GiB RAM available for initial loading. Other environments require their own compatibility/performance checks.

On Windows, set `MIMALLOC_PURGE_DELAY=0` and `MIMALLOC_ARENA_EAGER_COMMIT=0` before starting ComfyUI. Memory details, the optional matching-host patch and dependencies are documented in the [advanced guide](docs/advanced.zh-CN.md).

## Models

| File | Size | Destination |
| --- | ---: | --- |
| `hy_nomas_distil_int8.safetensors` | 9.06 GB | `ComfyUI/models/diffusion_models/` |
| `hy_nomas_pixelumm_fp8.safetensors` | 9.13 GB | `ComfyUI/models/vae/` |
| `qwen_2.5_vl_7b_fp8_scaled.safetensors` | 9.38 GB | `ComfyUI/models/text_encoders/` |

Download from [t8star/HY-Nomas-Comfy](https://huggingface.co/t8star/HY-Nomas-Comfy). Existing checkpoints can be reused in place through `extra_model_paths.yaml`. Select the actual filename in each loader; renamed files retain their `.safetensors` extension.

## Quick start

1. Drag a canvas JSON from `workflows/` into ComfyUI.
2. Select the DiT, Qwen text encoder (CLIP type `qwen_image`) and PixelUMM decoder.
3. Edit the scene/actions in the world JSON and run. Recommended sampling: **8 steps, CFG 1, euler / simple**.

| Workflow | Purpose |
| --- | --- |
| `T8 - Budgeted world.json` | CPU archive budget and statistics; recommended starting point |
| `Hy-Nomas world (per segment).json` | Standard KSampler graph and continuation |
| `Hy-Nomas world (one node).json` | Chained generation in one node |
| `Hy-Nomas start from image.json` | Image start |
| `Hy-Nomas camera control.json` | Camera paths and key controls |
| `Hy-Nomas drive (interactive).json` | Keyboard-driven generation |

Includes **15 native nodes and 6 canvas workflows**. T8 adds CPU KV archive budgets, usage statistics, cache release and transactional cancellation/failure cleanup. The default 12GiB archive budget covers CPU history only, not total RAM or VRAM; export/reset when appropriate during long continuations.

## Local measurements

RTX 5090 Laptop 24GB / 64GB RAM, INT8 DiT, FP8 decoder and text encoder:

| Case | Output | Physical GPU peak | Minimum available RAM |
| --- | --- | ---: | ---: |
| Native default two-segment workflow | 1280×704, 10 seconds / 240 frames | 17.53GiB | 8.15GiB |
| 15 independent new scenes | Each 1280×704, 6 seconds / 144 frames | Up to 16.28GiB | 9.03GiB |

Measurements use the startup settings above and the ComfyUI 0.42.1 cache-reference patch. The 89-second edit adds titles, short dissolves and post-production music. Results describe this tested machine, not every host configuration.

## Credits and license

Based on [PedroMarinhoDev/ComfyUI-Hy-Nomas](https://github.com/PedroMarinhoDev/ComfyUI-Hy-Nomas); model and original algorithms by [Tencent-Hunyuan/Hy-Nomas](https://github.com/Tencent-Hunyuan/Hy-Nomas). The T8 derivative retains **GPL-3.0**, upstream licenses and [NOTICE](NOTICE). Install only one Hy-Nomas implementation to avoid duplicate node IDs.

Weights are distributed separately under their original licenses. **The PixelUMM decoder is for non-commercial research and evaluation only**; see the [model license](https://huggingface.co/t8star/HY-Nomas-Comfy/blob/main/LICENSE).

## T8star

[Bilibili](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [Free gallery](https://www.openzhenzhen.com) · [Online AI apps](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI bundle](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
