# Hy-Nomas 本地世界工作室 · By T8star

**简体中文** | [English](README.en.md)

Windows 程序版，包含粉白界面、原生节点、ComfyUI 0.42.1 宿主代码与自动更新器。**不包含模型、Python 解释器及其第三方环境。** 当前程序版本为 0.2.1，推理代码沿用已完成 GPU 验收的 0.2.0。

## 使用

1. 从 [Releases](https://github.com/T8mars/Comfyui-HY-Nomas-T8/releases/latest) 下载 `Hy-Nomas-T8-Program-*-win64.zip` 并完整解压。
2. 完整版制作者：将兼容环境放在 `runtime/managed/cpython-3.12.9-windows-x86_64-none/`，将三个权重放在 `models/` 对应目录；已有的 0.2.0 完整运行环境可直接复用。保留程序版的启动和更新文件。
3. 普通使用者可等待作者的完整版；也可自行运行 `安装环境.cmd` 安装固定环境，再配置或下载模型。安装环境仅在手动执行时下载 Python 和依赖。
4. 双击 `启动整合包.cmd` 打开工作室，或 `启动ComfyUI原生节点.cmd` 打开原生 ComfyUI。两个入口共用模型，生成时只运行一个。

环境：Windows、Python 3.12.9、PyTorch 2.10.0+cu130、Triton Windows 3.6.0.post26。建议 NVIDIA 24GB 显存、64GB RAM。[模型下载与校验](https://huggingface.co/t8star/HY-Nomas-Comfy)；模型路径、操作与实测范围见 [完整指南](docs/studio-guide.zh-CN.md)。

## 自动更新

启动入口默认每 24 小时检查一次本仓库的稳定 Release；发现兼容程序更新后，自动下载、核验并在启动 Python 前安装。检查失败时继续使用当前版本；已有本目录 Python 运行时会延后更新，不结束进程。

- `检查更新.cmd`：只检查版本，不安装。
- `更新程序.cmd`：立即检查并安装。更新前先关闭工作室和本包 ComfyUI。
- 在 `config/update-settings.json` 写入 `{"auto_update": false}` 可关闭启动自动更新；`interval_hours` 可设置 1–168 小时。

更新只替换清单中的程序文件。`models/`、`runtime/managed/`、`config/`、`inputs/`、`outputs/`、缓存和其他自装节点保留。Python/依赖锁不兼容时停止更新并提示使用匹配完整版。更新前备份原程序到 `cache/updates/backups/`；写入失败自动回滚，备份保留。

以前不含更新器的完整版，先手动将本程序版合并到同一根目录一次，后续从新启动入口运行即可自动更新。不要把程序版当作自带环境的一键完整版；本仓库自带的 GitHub “Source code” 压缩包也不是程序发行包。

## 许可与链接

代码保留 GPL-3.0、ComfyUI/上游许可与 NOTICE。权重单独沿用原许可，PixelUMM 仅限非商业研究与评估。

[B站](https://space.bilibili.com/385085361) · [YouTube](https://www.youtube.com/@T8star-Aix/) · [API](https://api.seedance.nz/sign-up?aff=5f4w) · [免费画廊](https://www.openzhenzhen.com) · [在线 AI 应用](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) · [ComfyUI 整合包](https://pan.quark.cn/s/264edb7e36bd) · [Hugging Face](https://huggingface.co/t8star)
