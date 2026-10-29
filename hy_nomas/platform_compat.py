"""T8 Windows path compatibility for Triton's typed cache settings."""
import os
import ctypes
import hashlib
import tempfile
from pathlib import Path


def _short_path(path):
    api = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    api.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    api.restype = ctypes.c_uint
    size = api(str(path), None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    return buffer.value if api(str(path), buffer, size) else None


def _ascii_directory(value):
    path = Path(value).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        short = _short_path(path)
        if short and short.isascii():
            return short
    except OSError:
        pass
    key = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:16]
    roots = [Path(path.anchor) / "T8star-HyNomas-cache"]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "T8star" / "HyNomasCache")
    for root in roots:
        candidate = root / key
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            rendered = str(candidate) if str(candidate).isascii() else _short_path(candidate)
            if rendered and rendered.isascii():
                return rendered
        except OSError:
            continue
    raise RuntimeError("CUDA编译缓存需要可写的英文路径，请将整合包放到可写磁盘或配置英文缓存目录。")


def configure_triton_unicode_paths():
    if os.name != "nt":
        return {}
    home = os.environ.get("TRITON_HOME", str(Path.home()))
    candidates = {"TRITON_CACHE_DIR": os.environ.get("TRITON_CACHE_DIR", str(Path(home) / ".triton" / "cache")),
                  "TORCHINDUCTOR_CACHE_DIR": os.environ.get("TORCHINDUCTOR_CACHE_DIR",
                      str(Path(tempfile.gettempdir()) / ("torchinductor_" + Path.home().name)))}
    changed = {}
    for name, value in candidates.items():
        if not value.isascii():
            os.environ[name] = changed[name] = _ascii_directory(value)
    values = {name: os.environ.get(name) for name in
              ("TRITON_CACHE_DIR", "TRITON_HOME", "TRITON_DUMP_DIR", "TRITON_OVERRIDE_DIR")}
    if not changed and not any(value and not value.isascii() for value in values.values()):
        return {}
    try:
        from triton import knobs
    except ImportError:  # Older Triton versions read the corrected environment directly.
        return changed
    for name, attribute in (("TRITON_CACHE_DIR", "dir"), ("TRITON_HOME", "home_dir"),
                            ("TRITON_DUMP_DIR", "dump_dir"), ("TRITON_OVERRIDE_DIR", "override_dir")):
        value = values[name]
        if value:
            # The Windows native getenv returns ACP bytes, while its binding decodes UTF-8.
            # An explicit Python knob keeps the Unicode path without replacing Triton's getter.
            setattr(knobs.cache, attribute, value)
    return changed
