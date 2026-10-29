"""Portable model-location preferences; all files are opened in place."""
import json
import os
import struct
from pathlib import Path

from .bootstrap import ROOT

LABELS = {"dit": "DiT 主模型", "decoder": "视频解码器", "text_encoder": "文本编码器"}
KEYS = ("model_root", *LABELS)


class ModelLocations:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config_file = self.root / "config" / "model_paths.json"
        self.entries = json.loads((self.root / "models.manifest.json").read_text(encoding="utf-8"))["models"]

    def preferences(self):
        values = dict.fromkeys(KEYS, "")
        if self.config_file.exists():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or any(not isinstance(data.get(key, ""), str) for key in KEYS):
                    raise ValueError("路径配置应为文本键值对象")
            except (OSError, ValueError) as exc:
                raise ValueError(f"无法读取模型路径设置，请点击恢复默认路径或修复 {self.config_file}：{exc}") from exc
            values.update({key: data.get(key, "") for key in KEYS})
        return values

    def path(self, value):
        value = os.path.expandvars(str(value).strip().strip('"')).strip()
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.root / path).resolve()

    def resolve(self, values=None):
        values = self.preferences() if values is None else values
        paths = {}
        for entry in self.entries:
            if values.get(entry["id"]):
                paths[entry["id"]] = self.path(values[entry["id"]])
                continue
            if values.get("model_root"):
                folder = self.path(values["model_root"])
                relative = Path(entry["local"]).relative_to("models")
                # Accept both a ComfyUI models directory and an official HF checkout.
                candidates = [folder / relative, folder / relative.name,
                              folder / entry["remote"], folder / Path(entry["remote"]).name]
                paths[entry["id"]] = next((p for p in candidates if p.is_file()), candidates[0])
            else:
                paths[entry["id"]] = self.root / entry["local"]
        return paths

    def status(self, values=None, inspect=False):
        values = self.preferences() if values is None else values
        paths = self.resolve(values)
        rows = []
        for entry in self.entries:
            path = paths[entry["id"]]
            error, size = "", 0
            try:
                if not path.is_file():
                    raise ValueError("文件不存在")
                size = path.stat().st_size
                # The supported 24GB preset uses these exact three checkpoints. Renaming is fine.
                if size != entry["size"]:
                    raise ValueError(f"文件大小不匹配：需要 {entry['size']:,} 字节，实际 {size:,} 字节")
                if inspect:
                    with path.open("rb") as handle:
                        header = handle.read(8)
                        if len(header) != 8:
                            raise ValueError("Safetensors 文件头损坏")
                        length = struct.unpack("<Q", header)[0]
                        if not 2 <= length <= min(32 * 1024**2, size - 8):
                            raise ValueError("不是有效的 Safetensors 模型")
                        tensors = json.loads(handle.read(length))
                        if not isinstance(tensors, dict) or not any(k != "__metadata__" for k in tensors):
                            raise ValueError("Safetensors 文件中没有权重")
            except (OSError, ValueError, struct.error) as exc:
                error = str(exc)
            rows.append({"id": entry["id"], "label": LABELS[entry["id"]], "file": path.name,
                         "path": str(path), "size": entry["size"], "actual_size": size,
                         "ready": not error, "error": error,
                         "source": "custom" if values.get(entry["id"]) or values.get("model_root") else "default"})
        return rows

    def save(self, data):
        if not isinstance(data, dict) or any(not isinstance(data.get(k, ""), str) for k in KEYS):
            raise ValueError("模型路径必须是文本。")
        values = {k: data.get(k, "").strip().strip('"').strip() for k in KEYS}
        if values["model_root"] and not self.path(values["model_root"]).is_dir():
            raise ValueError("模型根目录不存在，请填写本机已有目录。")
        rows = self.status(values, inspect=True)
        for row in rows:
            if values[row["id"]] and not row["ready"]:
                raise ValueError(f"{row['label']}：{row['error']}\n{row['path']}")
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_file)
        return values, rows

    def required_paths(self):
        rows = self.status(inspect=True)
        for row in rows:
            if not row["ready"]:
                raise FileNotFoundError(f"{row['label']}未就绪：{row['error']}。路径：{row['path']}")
        return {row["id"]: row["path"] for row in rows}
