# Rebuilding the program ZIP

The program release includes all application/host Python source, native node source and applicable notices, but excludes the interpreter, site-packages, user data and weights.

Use Python 3.12+ and the fixed upstream inputs. Program version comes from `studio/versions.json`; only Git-tracked program inputs ship. `docs/inference-source-lock.json` pins the reviewed inference/API files; unexpected vendor changes are rejected:

Tracked inputs must remain regular files inside their source root. Paths forbidden by the updater are rejected before packaging. Assets are prepared and checked in a temporary build directory; ordinary build/publication errors preserve previous outputs. If restoring outputs fails, the error reports the retained backup directory.

- ComfyUI commit `63be2f9b82ad0dac88a7ceaa28a9cf3154125a18` (v0.42.1), with `patches/comfyui-cache-lifetime.patch` applied from the ComfyUI root.
- Windows x86_64 `uv.exe` from [uv 0.5.31](https://github.com/astral-sh/uv/releases/tag/0.5.31); SHA256 `311ba603f935e850ffea25cbd798d7437ca1498f30128795823f0ff4778239ea`. uv's Apache/MIT licenses ship under `licenses/`.

```powershell
python studio/build_program_release.py --comfyui-root C:/build/ComfyUI --uv-file C:/build/uv.exe --output C:/build/dist
powershell.exe -NoProfile -ExecutionPolicy Bypass -File studio/tests/test_updater.ps1
```

Publish the generated program ZIP, its `.zip.sha256`, and matching `*-manifest.json` together on a stable `v<program-version>` GitHub Release. The updater uses those explicit asset names; it does not install the native-node ZIP or GitHub's generated source archives. Keep the dependency lock unchanged for program-only updates. When changing the runtime dependency lock, distribute a matching full environment as well; existing incompatible environments refuse that automatic update.
