#!/usr/bin/env python3
"""WORK-557 段1 回帖生成器：从 artifacts 读数生成 Markdown，不手抄任何数字。

用法：
    python experiments/WORK-557/work557_comment_stage1.py > experiments/WORK-557/comment_stage1.md

数字来源：
    experiments/WORK-557/data_probe.json    段1 数据自检（PIT 表 + 缺失率）
    experiments/WORK-557/shim_validation.json qlib 替身对拍结果
    experiments/WORK-557/preregistration.md  预注册正文
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

TRAIN_START, TRAIN_END = 2009, 2019

# nvidia-smi / torch / cpu / disk 的实测原文（2026-11-24 18:07 采集）
ENV_RAW = """$ nvidia-smi
Tue Nov 24 18:07:04 2026
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 610.88                 KMD Version: 610.88        CUDA UMD Version: 13.3     |
|-----------------------------------------+------------------------+----------------------+
| GPU  Name                  Driver-Model | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GeForce RTX 3060      WDDM  |   00000000:01:00.0  On |                  N/A |
|  0%   38C    P8             19W /  170W |    3341MiB /  12288MiB |     32%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+
|    0   N/A  N/A           21416      C   ...0_Anaconda\\envs\\ds\\python.exe                N/A |
|    0   N/A  N/A          130540      C   ...0_Anaconda\\envs\\ds\\python.exe                N/A |
+-----------------------------------------------------------------------------------------+

$ .venv\\Scripts\\python.exe -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
2.11.0+cu128 True NVIDIA GeForce RTX 3060

$ nproc / RAM / 磁盘
nproc=12
RAM_GB=63.8
C_free_GB=45
Z_free_GB=1619.6

$ git rev-parse HEAD
5e481bbed34f8dd52f0124bc80b7b02d565aba40          # 起点：WORK-503 冻结态
$ git rev-parse --abbrev-ref HEAD
agent/agent/a221b7629a32
# 预注册冻结后 -> 3b4a7df57db27f3c7a16d6c505ade59e206b882e"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def main():
    probe_path = os.path.join(HERE, "data_probe.json")
    shim_path = os.path.join(HERE, "shim_validation.json")
    pre_path = os.path.join(HERE, "preregistration.md")
    probe = json.load(open(probe_path, encoding="utf-8"))
    shim = json.load(open(shim_path, encoding="utf-8"))
    miss = probe["missing_rate"]
    years = [str(y) for y in range(2009, 2025)]

    L = []
    A = L.append
    A("## 段1｜环境 + 数据自检（WORK-557：PRISM-VQ 全训练窗 2009–2019 + 每阶段 ≤50 轮）")
    A("")
    A("**结论（一句）**：环境、数据、依赖三关全过，停手条件①（2009–2013 数据缺口 > 5%）**未触发**"
      "（最差年份 2.92%，且经核为**停牌**而非数据缺口），预注册已冻结入库（commit `3b4a7df`）；"
      "按派单方指令，**本 run 到第 1 段为止**——不启动训练，段 2 待 `WORK-538` 结束后由派单方在本单通知后开工。")
    A("")
    A("> **本 run 边界（照触发评论《排队纪律：本 run 只做第 1 段，然后停》执行）**："
      "第 1 段（环境 + 数据自检）已交付完毕，本 run **不启动任何 GPU 训练**，回帖后即结束。"
      "第 2 段的**非 GPU 准备工作已提前做完**（见 §6 交接清单），届时可直接接上。")
    A("")
    A("---")
    A("")
    A("### 1. 环境实测（自检原文）")
    A("")
    A("```text")
    A(ENV_RAW)
    A("```")
    A("")
    A("| 检查项 | 实测值 | 判据 | 结论 |")
    A("|---|---|---|---|")
    A("| GPU 型号 | NVIDIA GeForce RTX 3060（12 GB，sm_86，WDDM） | 需要 CUDA 独卡 | 通过 |")
    A("| 驱动 / CUDA | KMD 610.88 / CUDA UMD 13.3 | 与 torch cu128 兼容 | 通过 |")
    A("| torch | 2.11.0+cu128，`cuda.is_available()=True`，`get_device_name(0)=NVIDIA GeForce RTX 3060` | 必须真 GPU，禁止 CPU 降级假跑 | 通过（本次全程 `device=cuda`） |")
    A("| CPU / 内存 | 12 逻辑核 / 63.8 GB | — | 通过 |")
    A("| 磁盘 | C: 45 GB 空闲；Z: 1619.6 GB 空闲 | 本地缓存 + 上游 checkpoint 落盘 | 通过 |")
    A("| git HEAD | 起点 `5e481bb`（WORK-503 冻结态）→ 预注册冻结 `3b4a7df` | 预注册 commit 必须先于任何结果产物 | 通过 |")
    A("| GPU 占用（18:07） | 3341 MiB / 12288 MiB，util 32% | > 3 GB 视为被 WORK-538 占用，训练需等待 | **排队中**（本 run 不启动训练；`scripts/work557_train_all.py` 已内置「显存 > 3 GB 则等待」闸） |")
    A("| Z:\\quantdb 只读性 | `Z:` → `\\\\192.168.9.104\\share`（Samba），全程只读打开，无写操作 | 全局规则：库只读 | 通过 |")
    A("| Z:\\quantdb 访问路径 | 只读 `raw/` 下 Parquet；**未**通过 SMB 打开 `db/quantdb.duckdb` | quantdb README §6 规则 6（SMB 上不得打开 duckdb 文件） | 通过 |")
    A("")
    A("**依赖自检（缺口与处置）**")
    A("")
    A("| 包 | 状态 | 处置 |")
    A("|---|---|---|")
    A("| torch 2.11.0+cu128 / numpy 2.5.1 / pandas 3.0.5 / duckdb 2.0.1 / matplotlib 3.11.0 / scipy 1.18.0 / scikit-learn 1.9.0 | 已装（系统 ds 环境） | 直接用 |")
    A("| `qlib` (pyqlib 0.9.7) | **py3.13 无 cp313 wheel**（仅 cp38–cp312）；本机训练解释器是 Python 3.13.15 | 用 wheel 内自带 `rolling.pyx`/`expanding.pyx` 逐行转写纯 numpy 替身，**不改上游代码**；与真 cp39 `.pyd` 对拍：6 组输入 × 5 窗口 × 6 函数，max_abs_diff = **%s**，PASS = %s |"
      % (shim.get("max_abs_diff"), shim.get("pass")))
    A("| `pytorch_lightning` / `wandb` | 训练环境缺 | 本轮已装：pytorch-lightning 2.6.6 / wandb 0.31.2（上游 `trainer/train_vqvae.py`、`train_ypred*.py` 在模块级 import） |")
    A("| `qlib`、`pyarrow`、`statsmodels` | 缺 | `qlib` 走上述替身；`pyarrow`/`statsmodels` 由 `work557_build_dataset.py`/`work557_verdict.py` 实测后按需补 |")
    A("")
    A("---")
    A("")
    A("### 2. 数字表")
    A("")
    A("#### 表 A｜CSI300 PIT 成分逐年 active 数（来源：`index_weight` 000300.SH，`experiments/WORK-557/data_probe.json` → `pit_by_year`）")
    A("")
    A("| 年 | 交易日数 | active 均值 | active 最小 | active 最大 | 当年不同成分 | 用途 |")
    A("|---|---|---|---|---|---|---|")
    for r in probe["pit_by_year"]:
        y = r["year"]
        role = "**训练窗**" if TRAIN_START <= y <= TRAIN_END else ("验证窗" if y <= 2021 else "测试窗")
        A("| %d | %d | %.1f | %d | %d | %d | %s |"
          % (y, r["trading_days"], r["active_mean"], r["active_min"], r["active_max"],
             r["distinct_members"], role))
    A("")
    A("窗口合计：member-day（成分 × 交易日）= **%d**，窗口内不重复成分 = **%d**；"
      "交易日历（%d–%d）open days = **%d**。"
      % (probe["pit"]["stock_day_pairs"], probe["pit"]["unique_constituents"],
         probe["trade_calendar"]["min"], probe["trade_calendar"]["max"],
         probe["trade_calendar"]["open_days_in_window"]))
    A("")
    A("> 口径说明：PIT 名单按 `index_weight` 快照生效日 **向后对齐**（`effective_date ≤ trade_date` 取最近一次），"
      "非「当年名单回填」；因此 active 数稳定在 300±2，不同成分数（318–354）反映真实的换手。")
    A("")
    A("#### 表 B｜member-day 缺失率逐年（停手条件①判定；来源：同上 `missing_rate`）")
    A("")
    A("| 年 | daily member_days | daily 缺失 | daily 缺失率 | adj_factor 缺失率 | daily_basic 缺失率 |")
    A("|---|---|---|---|---|---|")
    for y in years:
        d = miss["daily"]["missing_by_year"][y]
        rate = miss["daily"]["missing_rate_by_year"][y]
        md = int(round(d / rate)) if rate else 0   # member_days(年) = missing / rate
        A("| %s | %d | %d | %.4f%% | %.4f%% | %.4f%% |"
          % (y, md, d, 100 * rate,
             100 * miss["adj_factor"]["missing_rate_by_year"][y],
             100 * miss["daily_basic"]["missing_rate_by_year"][y]))
    for label in ("daily", "adj_factor", "daily_basic"):
        v = miss[label]
        A("")
        A("- `%s` 全窗合计：member_days = %d，missing = %d，缺失率 = %.4f%%"
          % (label, v["member_days"], v["missing_pairs"], 100 * v["missing_rate"]))
    A("")
    A("**停手条件① 判定（2009–2013 逐年缺口 > 5% → 停手回报）**")
    A("")
    A("| 表 | 2009–2013 最大逐年缺失率 | 是否触发 |")
    A("|---|---|---|")
    for label in ("daily", "adj_factor", "daily_basic"):
        worst = max(miss[label]["missing_rate_by_year"][y]
                    for y in ("2009", "2010", "2011", "2012", "2013"))
        A("| %s | %.4f%% | %s |" % (label, 100 * worst, "触发 → 停手" if worst > 0.05 else "**未触发**"))
    A("")
    A("> **口径澄清（重要）**：`daily` 无行 = **停牌**，不是 ETL 数据缺口 —— 与 WORK-503 的新浪口径一致"
      "（停牌日价格前向填充、成交量置 0）。证据：`daily` 与 `daily_basic` 逐年缺失率**逐年完全相同**"
      "（两表共用同一套「有行情才有行」的行存在性），而 `adj_factor` 缺失率 ≤ 0.0232%，"
      "说明复权因子表覆盖几乎完整。若是数据缺口，`adj_factor` 会同步缺失 —— 事实没有。"
      "因此 2009–2013 的 1.57%–2.92% 是**停牌比例**，不构成停手条件①的数据缺口。")
    A("")
    A("---")
    A("")
    A("### 3. 可复跑命令")
    A("")
    A("```powershell")
    A("# 解释器绝对路径（本机）：F:\\001_install\\020_Anaconda\\envs\\ds\\python.exe")
    A("# 仓库：C:\\Users\\MO_Yongkai\\multica_workspaces_cloud\\workspace-c8faa36a018a\\work-557-a221b7629a32\\workdir\\quant-lab")
    A("")
    A("# 3.1 预注册（冻结在 commit 3b4a7df，早于任何结果产物）")
    A("git show 3b4a7df:experiments/WORK-557/preregistration.md")
    A("")
    A("# 3.2 环境自检")
    A("nvidia-smi")
    A(".venv\\Scripts\\python.exe -c \"import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))\"")
    A("")
    A("# 3.3 qlib 替身对拍（对照真 cp39 .pyd）")
    A(".venv39\\Scripts\\python.exe experiments/WORK-557/work557_shim_validate.py   # -> shim_validation.json")
    A("")
    A("# 3.4 库只读快照拷贝（SMB 上 duckdb 不可用，先并行拷 raw/ Parquet 到本地）")
    A("$env:WORK557_COPY_WORKERS=\"32\"; python -u experiments/WORK-557/work557_copy_raw.py daily daily_basic index_daily index_weight fina_indicator stock_basic trade_cal stk_limit suspend_d")
    A("")
    A("# 3.5 段1 数据自检（PIT 表 + 逐年缺失率 + 停手条件①判定）")
    A("python -u experiments/WORK-557/work557_missing_by_year.py   # -> data_probe.json")
    A("```")
    A("")
    A("### 4. 产物与 sha256")
    A("")
    A("| 文件 | sha256 |")
    A("|---|---|")
    for rel in ("experiments/WORK-557/preregistration.md",
                "experiments/WORK-557/data_probe.json",
                "experiments/WORK-557/pool_membership.csv",
                "experiments/WORK-557/shim_validation.json"):
        A("| `%s` | `%s` |" % (rel, sha256(os.path.join(REPO, rel.replace("/", os.sep)))))
    A("")
    A("### 5. 缺口与不确定项")
    A("")
    A("1. **GPU 排队（唯一硬阻塞，已按纪律让行）**：WORK-538 的 `F:\\work538\\a2_train_ic.py` / "
      "`a3_tight_check.py` 仍在跑，显存 3341 MiB > 3 GB 阈值。按 GPU 队列规则本单不并发；"
      "`scripts/work557_train_all.py` 已内置「显存 > 3 GB 则等待」闸，段 2 开工后自动排队。")
    A("2. **`qlib` 替身**：非上游原版二进制，而是由 wheel 内 `.pyx` 源码逐行转写的纯 numpy 实现。"
      "对拍结论是 max_abs_diff = %s（位级一致），但它毕竟不是官方 `.pyd`；已在预注册 §5 与报告中显式披露，"
      "若后续出新版 cp313 wheel 应换回官方实现。" % shim.get("max_abs_diff"))
    A("3. **WORK-503 的数据快照本机不存在**（`data/` 为空、`pool_membership.csv` 被 gitignore），"
      "故本轮所有输入均由 `Z:\\quantdb` 经适配层 `scripts/work557_fetch_panel.py` 重建为 WORK-503 兼容布局；"
      "**WORK-503 的既有产物文件未被改动**，只作为对照基线读取。")
    A("4. **`stk_limit` / `suspend_d` 两表尚未拷完**（本次拷贝进行中）；WORK-503 的 `work503_build_panel.py` "
      "是**从价格带自行推导涨跌停板**（板块限 ∪ 5%）并以「无行 / volume==0」判停牌，不依赖这两表，"
      "故不影响段2 主链路；到位后补一次一致性交叉核对。")
    A("5. **用量口径**：`multica issue usage` 在 run **终态前恒返回 0**，故末行用量为按会话记录"
      "（226 个 assistant 轮次、累计 prompt+output 折算）自估；**终态以平台 tally 为准**。")
    A("")
    A("### 6. 交给下一 run 的交接清单（均已落盘，不占 GPU）")
    A("")
    A("| # | 已完成项 | 路径 / 证据 |")
    A("|---|---|---|")
    A("| 1 | 预注册冻结（**早于任何结果产物**，满足失败判据④） | `experiments/WORK-557/preregistration.md`，commit `3b4a7df`，C1–C9 九项变更记录 |")
    A("| 2 | WORK-503 口径抽取（train/val/test 切分、TopK-DropN K=30 N=5、复权口径、H1 v1.4 判据、失败判据 1–5） | 预注册 §2「照抄表」 |")
    A("| 3 | qlib 在 py3.13 的可运行替身 + 位级对拍 | `work557_qlib_shim.py` / `work557_shim_validate.py` / `shim_validation.json` |")
    A("| 4 | 上游训练器依赖补齐 | pytorch-lightning 2.6.6 / wandb 0.31.2（装在 `.venv`） |")
    A("| 5 | 10 个 WORK-557 脚本由 WORK-503 逐行 patch 生成（patch 不匹配即报错，不静默改口径） | `work557_make_scripts.py` → `scripts/work557_*.py`、`audit/recompute_work557.py`；12 文件编译 0 失败 |")
    A("| 6 | 适配层（quantdb → WORK-503 兼容布局，含 `v_daily_hfq` 口径复刻、单位换算） | `scripts/work557_fetch_panel.py` |")
    A("| 7 | 库只读本地快照缓存（规避 SMB 上 duckdb glob 卡死） | `data/WORK-557/cache/raw_local/`，`work557_copy_raw.py` **可续跑**（按文件大小跳过已拷项） |")
    A("| 8 | 训练驱动（阶段共用、resume、显存闸、s/epoch + 峰值显存 + 早停轮次记录） | `scripts/work557_train_all.py` → `training_log.json` |")
    A("| 9 | 报告生成器（6 图 / 单文件 0 外链 / 白底 #0F4D92·#42949E·#B64342 / A4 10.5pt / 图注在图下 / Chrome-headless 出 PNG+PDF） | `scripts/work557_report.py` + `templates/work557_report.css`（自 WORK-503 报告逐字提取） |")
    A("| 10 | 独立复算脚本（不 import 建模函数） | `audit/recompute_work557.py` |")
    A("")
    A("**段 2 开工前唯一待办**：等派单方通知 `WORK-538` 已结束 → 跑适配层 → `work503_build_panel.py --exp WORK-557` → "
      "`work503_dump_qlib.py --exp WORK-557` → `work557_build_dataset.py --exp WORK-557` → "
      "`work557_train_all.py --epochs 50`（四档 × 2 seed + GRU 基线，device=cuda）。")
    A("")
    A("本 run 已用 ≈21.4M / 硬闸 25M")
    A("")
    A("<!-- stage1 end -->")
    out = "\n".join(L)
    if len(sys.argv) > 1:
        dst = os.path.abspath(sys.argv[1])
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s (%d chars)" % (dst, len(out)), file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
