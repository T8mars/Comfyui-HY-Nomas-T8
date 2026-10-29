# Hy-Nomas T8 原生节点 — By T8star

本派生包基于 PedroMarinhoDev/ComfyUI-Hy-Nomas，直接运行 Python 模型和 ComfyUI 采样器；不通过外部服务桥接推理。保留上游 GPL-3.0、Tencent/PixelUMM 代码许可与 NOTICE。PixelUMM 权重另受 NVIDIA One-Way Noncommercial License 约束。

## 独立安装

完整解压本节点ZIP，将 `ComfyUI-HyNomas-T8` 目录放入现有 `ComfyUI/custom_nodes`，重启宿主。整合包已包含依赖；独立安装时，应使用启动该ComfyUI的同一个Python。Windows本机实测组合为ComfyUI 0.42.1、Python 3.12.9、PyTorch 2.10.0+cu130与 `triton-windows==3.6.0.post26`。在该匹配环境中执行：

```powershell
python -m pip install triton-windows==3.6.0.post26
```

如果宿主使用内嵌Python，将命令中的 `python` 替换为它的实际完整路径；不要安装到系统中的另一套Python。其他PyTorch/Python组合需另行核对兼容性，本包的完整环境见整合包 `requirements.lock.txt`。本发布版的 `pyproject.toml` 与 `requirements.txt` 已声明各平台 Triton 依赖；Windows 固定为上述实测版本。若宿主跳过自动依赖安装，可使用上面的手动命令。本派生版的 Registry 包名为 `hy-nomas-t8`，Publisher 为 `t8star`。

## 预算工作流

导入 `workflows/T8 - Budgeted world.json`，按本机实际文件选择 DiT、Qwen 文本编码器和 PixelUMM 解码器。默认示例为 832×480、INT8 KV、18 个历史潜帧，CPU KV 档案上限 12 GiB。

- **T8 World Budget** 创建空世界，或调整已有世界的档案上限与历史帧数。将其 `world` 接入 Segment 或 Generate World。已有档案的 INT8/BF16 格式不能在中途切换。
- **T8 World Stats** 输出并透传世界，同时给出档案占用、剩余额度和段数，可接在生成与解码之间。
- **T8 Release Caches** 释放连接世界的暂存 GPU 历史、PixelUMM 布局缓存和分词器缓存。默认保留 CPU 档案；显式勾选 `reset_drive_sessions` 才会清空 Drive 会话。
- **Hy-Nomas Drive** 的高级选项 `max_archive_gib` 采用同样的档案上限。到达上限后需导出并重置，或按实际系统内存增加上限。

12 GiB 是每个世界的 CPU KV 档案预算，不是进程总 RAM 或显存上限，也不包括模型、文本缓存、潜变量和输出帧。ComfyUI 缓存中同时保留多个世界时，整体内存仍可累积；无需续写时应清理对应执行缓存或重启服务。历史帧数仅限制送入模型的历史窗口，不会自动删除 CPU 全量档案。

模型路径继续使用 ComfyUI `folder_paths`：默认模型目录与 `extra_model_paths.yaml` 中的自定义目录均可。应使用模型加载器的下拉框选择文件，无需把路径写进节点源码。

整合包入口会优先选择显式配置的自定义目录；独立安装节点时，目录优先级由宿主 ComfyUI 配置。下拉框保留改名权重。采样和解码前通过宿主卸载闲置模型；INT8 KV 按2048 token分块量化，完整 IMAGE 使用FP32 CPU合成缓冲，重叠区保持原BF16运算语义。

完整解码会通过宿主RAM压力缓存请求输出帧及工作余量，释放不再被当前消费者使用的缓存。当前World和解码器输入保留；后续再次需要已被驱逐的节点结果时，ComfyUI可能重新计算。此功能依赖宿主提供的RAM压力缓存回调，未自行清空世界或绕过执行器。

本整合包另修复ComfyUI 0.42.1执行器在重复prompt时额外保留缓存探测结果的问题。独立安装到同版本宿主时，可从宿主根目录先用 `git apply --check <节点目录>/patches/comfyui-cache-lifetime.patch` 核对，再执行 `git apply <节点目录>/patches/comfyui-cache-lifetime.patch`；路径含空格时加引号。补丁只在生成缓存节点ID列表后释放临时引用，保留正常执行缓存和消费者输入。其他版本应先核对上下文，不自动改写宿主。

Windows外部ComfyUI在导入PyTorch之前，建议在原有启动脚本中设置以下环境变量。本整合包两个入口已自动设置默认值，已有用户设置会保留。这解决本机PyTorch 2.10回收模型对象后，底层分配器仍保留大块已空闲物理内存的问题；可能增加再次分配的开销。

```bat
set MIMALLOC_PURGE_DELAY=0
set MIMALLOC_ARENA_EAGER_COMMIT=0
```

## 失败与取消

采样器和缓存写入异常均释放已暂存的历史；PixelUMM 解码在成功、取消和异常后释放布局缓存。Drive 仅在采样、缓存写入、解码与 JPEG 编码全部成功后提交新世界，失败后可从上一轮重试。重置会话会阻止运行中的旧结果重新写回该会话。释放张量引用不等于卸载模型，也不保证 NVIDIA 工具显示的 CUDA 保留内存立即归零。

## 验证范围

`tests/run_cpu_lifecycle_tests.py` 的27项检查使用真实 CPU 张量、模拟 ComfyUI 宿主接口验证预算、弱引用生命周期、取消清理、Drive事务、数值一致性及完整输出裁切前的RAM预留；禁止实际加载模型或调用采样器。这些CPU测试本身不证明GPU推理。整合包当前版本另已实测默认1280×704、两段5秒/240帧标准KSampler工作流：整卡峰值17.53GiB，最低可用物理RAM8.15GiB，实测视频与环境摘要见仓库主说明；完整源码指纹和验收证据保留于独立整合包交付记录。此硬件为RTX 5090 Laptop 24GB / 64GB RAM，不代表任意外部宿主配置均有相同速度和峰值。

运行方式：`python tests/run_cpu_lifecycle_tests.py`，需要现有环境包含 PyTorch、NumPy、Pillow 和 pytest。
