# WORK-637 失败记录与口径变更

> 只记事实与实测值；不写「已改进」「再优化」这类没有测量的自述。

## 1. 环境与工具链

| # | 尝试 | 实测结果 | 处理 |
|---|---|---|---|
| F1 | `uv pip install torch --index-url https://download.pytorch.org/whl/cpu` | 下载 199.8 MiB，7 分 28 秒；第二次尝试 3 分 46 秒后仍未落盘，缓存停在 416 MB 不动 | 改从阿里云 `pytorch-wheels/cpu` 镜像取 `torch-2.9.1+cpu-cp311-cp311-manylinux_2_28_x86_64.whl`（184,452,625 B），本地 `--find-links` 安装，11.7 s 完成 |
| F2 | `huggingface.co` 直连取 DINOv2-S 权重 | 不可达（`curl -sSI` 无响应） | 改 `hf-mirror.com`：`model.safetensors` 88,249,960 B，sha256 `ae1e99fc…d14be1` |
| F3 | `hf-mirror` 上的 `dinov2_vits14_pretrain.pth` | 返回 15 字节文本 `Entry not found` | 改用 HF 仓库实际文件名（`model.safetensors` + `config.json`），以 `transformers.Dinov2Model` 加载（22.06 M 参数） |
| F4 | `mplfinance` 渲染 | 27.8 ms/张（224×224），且每次调用新建 figure | 改用 matplotlib 原语直接绘制：140×140 时 21.9 ms/张（单进程）、8 进程并行 170.9 张/秒。**mplfinance 仅作依赖保留，未进入渲染路径**——这是「复用优先」在第 2 步（少量二开）上的取舍，理由是吞吐量差 1.27 倍且 mplfinance 无法复用 figure |
| F5 | CPU 分辨率扫描（ViT-S/14，8 线程） | 224→22.9 张/秒；168→41.5；140→58.7；112→84.8 | 选 140（185,729 张 ≈ 55 min/种子），见预注册 D9 |

## 2. 代码缺陷（运行中暴露并修掉）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| F6 | `ValueError: operands could not be broadcast together with shapes (660,932) (660,)` | `eligibility()` 里 `ok_e` 是周维度向量，未广播到股票维 | `ok_e[:, None]` |
| F7 | `FileNotFoundError: …/seed42/2014/20140103_000001.SZ.png` | 年目录创建用了 `dir.split('/')[-2]`，取到的是 `seed42` 不是年份 | 改从 `idx["date"]` 取前 4 位 |
| F8 | `Import pyarrow failed`（落 index parquet） | venv 未装 parquet 引擎 | `pyarrow==26.0.1` |

## 3. 协议层面的偏离与披露

| # | 项 | 事实 |
|---|---|---|
| F9 | 预注册估算周数 650，实测 **660** | 预注册写的是「257 / 103 / 290 = 650」，是开工前用探针脚本估的；实际按 SSE 日历取「每周最后一个交易日」得 257 / 103 / 300 = 660（测试段 2021-01-04 ~ 2026-11-13）。**日期区间一个没动，只更正计数**，预注册 §1 的表格保留原值以便追溯 |
| F10 | 预注册写「图数 195,000」 | 实测可出图 185,729 张（差异来自「39 日窗口逐日有行」+「上市 ≥250 交易日」的过滤），预注册该数是估算值 |
| F11 | 阶段 B（FT-DINO） | **未跑**。CPU 下 1 个 epoch（66,807 张，前向+反向）约 55 min，加 Fine-tuned 模型重新编码库 55 min，加 10 个检查点的验证 KL ≈ 2 h；预注册 §10 规定「阶段 A 全流程完成且剩余 ≥ 3 小时」才启动，本轮不满足。H4 无结论 |
| F12 | 周度持有与日历 | 持有固定 5 个交易日（`t+1 → t+6` 开盘），但相邻信号日间隔在节假日周只有 3–4 个交易日，因此相邻两周的持有区间有 1–2 日重叠；净值按周复利，与论文同口径，已在报告 §7 披露 |
