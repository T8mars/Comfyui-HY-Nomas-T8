# Hy-Nomas Local World Studio · By T8star

[简体中文](README.zh-CN.md) | **English**

Windows program distribution with the pink-and-white studio, native nodes, ComfyUI 0.42.1 host sources and an updater. **Model weights, the Python interpreter and Python site-packages are excluded.** Program version: 0.2.1; inference code matches the GPU-validated 0.2.0 release.

## Setup

1. Download `Hy-Nomas-T8-Program-*-win64.zip` from [Releases](https://github.com/T8mars/Comfyui-HY-Nomas-T8/releases/latest) and extract it fully.
2. Full-bundle authors: place a compatible environment under `runtime/managed/cpython-3.12.9-windows-x86_64-none/` and weights in their `models/` folders. The existing 0.2.0 environment is compatible. Retain the supplied launcher and update files.
3. Users can wait for the author's complete bundle or manually run `安装环境.cmd` to install the pinned environment, then configure/download weights. Only that explicit installer downloads Python and dependencies.
4. Run `启动整合包.cmd` for the studio or `启动ComfyUI原生节点.cmd` for native ComfyUI. Use one generation interface at a time.

Tested environment: Windows, Python 3.12.9, PyTorch 2.10.0+cu130, Triton Windows 3.6.0.post26. Recommended: NVIDIA 24GB VRAM and 64GB RAM. [Models/checksums](https://huggingface.co/t8star/HY-Nomas-Comfy); [detailed usage and measurements](docs/studio-guide.zh-CN.md).

## Updates

Launchers check this repository's stable Release once every 24 hours by default. A compatible program update is downloaded, verified and installed before Python starts. A failed check keeps the current version usable. Updates defer while Python from this bundle is running; processes are never stopped.

- `检查更新.cmd`: check only.
- `更新程序.cmd`: check and install now; close the studio and bundled ComfyUI first.
- Set `{"auto_update": false}` in `config/update-settings.json` to disable launch-time updates. Optional `interval_hours`: 1–168.

Only manifest-listed program files are replaced. Models, Python under `runtime/managed/`, settings, inputs, outputs, caches and other custom nodes are retained. Incompatible dependency locks require a matching full bundle. Old program files are backed up under `cache/updates/backups/`; installation failures roll back automatically and retain the backup.

For old full bundles without this updater, merge this program distribution into the existing root once, then use its launchers. The program ZIP and GitHub's generated “Source code” archives do not include a ready-to-run Python environment.

## License and links

Code retains GPL-3.0 and upstream ComfyUI notices. Model licenses remain separate; PixelUMM is restricted to noncommercial research and evaluation.

[Bilibili](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [Free gallery](https://www.openzhenzhen.com) · [Online AI apps](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI bundle](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
