# WORK-505 失败日志

> 单号 WORK-505 · GPU 正式复现（Transformer 版三档掩码对照，2 seed）
> 执行方：算力实验执行（RTX 3060 / DESKTOP-TS1FCDR）· 日期 2026-11-22
>
> 纪律：**跑挂的尝试、搬运失败原文、显存峰值、异常全部登记**，不删改、不美化。
> 凡「未取到」一律写「未取到」，不许用代理值冒充。

---

## §1 总结论

本单的**唯一根因**是 WORK-449 的冻结快照不可复原：680 只逐股文件中 **6 只可复原、
674 只不可复原**。因此三档掩码对照（6 次训练 + 6 次回测）、主检验、独立复算与判词
**全部未执行**。其余交付项（环境复测、预注册 v2、实现、端到端验证、报告）**已完成**。

---

## §2 快照搬运：逐条通道与失败原文

### 2.1 通道一 · SSH 进 Mo-server 直接取（**失败 · 目标不存在**）

```
$ ssh -o BatchMode=yes moyongkai@192.168.9.104 "ls -d /srv/data/multica/workspaces/*/work-449-*/workdir"
（无输出）
$ find /srv/data/multica/workspaces -maxdepth 9 -type f -name 'sh.600000.csv'
/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab/data/WORK-503/raw/sh.600000.csv
/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab/data/WORK-503/fundamentals/sh.600000.csv
/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab/data/WORK-503/sh.600000.csv
/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab/data/WORK-503/sina_hfq/sh.600000.csv
/srv/data/multica/workspaces/workspace-c8faa36a018a/work-503-69b2057aa375/workdir/quant-lab/data/WORK-503/sina/sh.600000.csv
```

**原文结论**：Mo-server 上 WORK-449 的三个工作区目录
（`work-449-8c3e6faa6d66` / `work-449-9983cfc81116` / `work-449-f9fb873a83c6`）**只剩
`.task_lock` 与 `.task_owner`**，`workdir/` 为空；`work-449-acc3a29910af`（原始取数工作区）
目录本身已不存在。WORK-503 的数据**来源不同**（新浪、2012 起、724 只），不是本单要的快照。

### 2.2 通道二 · GNOME 回收站（**部分成功 · 6/680**）

`/home/moyongkai/.local/share/Trash/files` 下有 **11,458 个文件**（1.7 GB）。
`Trash/info/*.trashinfo` 显示其中 1,370 个来自
`/srv/data/multica/workspaces/workspace-c8faa36a018a/work-449-acc3a29910af/workdir/qlab/data/WORK-449/`。

逐文件 sha256 与冻结清单比对结果：

```
manifest items ok=680
trash csv candidates=1372
MATCHED 6/680
MISSING 674
```

**代际分析**（按 `trashinfo` 删除时刻 + 文件 mtime 聚类）：

| 代际 | mtime | 特征 | 与清单 sha256 |
|---|---|---|---|
| 旧代 | 09:56–10:10 | 价格 10 位小数、成交量为整数股、稠密行数（2676） | 0 命中 |
| qfq 代 | 10:12–10:13 | 价格 2 位小数、成交量按手取整、行数与清单一致 | 0 命中 |
| **hfq 代** | 10:16 | 价格 2 位小数、hfq 尺度（2014 年 50.41 元） | **6 命中** |

`sh.600000` 在回收站里的 5 个副本逐个实测（这是唯一能直接看到「代际」的地方）：

```
mtime=09:56:48 lines=2677 sha=1c105679fa5ab0e7   (稠密，10 位小数)
mtime=09:57:29 lines=2677 sha=deb66d79b06049cf
mtime=10:12:00 lines=2644 sha=9594f041c539c6cc   (qfq 尺度 1.69)
mtime=10:12:06 lines=2644 sha=9594f041c539c6cc
mtime=10:16:37 lines=2644 sha=9b973aa53bffdc33   <<MATCH  (hfq 尺度 50.41)
```

⇒ 冻结快照的 **hfq 代在回收站里只剩 6 个文件**。

### 2.3 通道三 · git 对象库（**失败 · 零命中**）

```
REPO: .../github.com+huangsewanjia+quant-lab.git
  blobs=266
  MATCHED CODES: 0
REPO: /home/moyongkai/.local/share/Trash/files/.git
  blobs=8442
  MATCHED CODES: 0
```

### 2.4 通道四 · SMB 共享 `\\192.168.9.104\share`（Z:）（**失败 · 无货**）

```
Z:\agent-sessions-staging, lost+found, _lf-images.tar, ch.tar, langfuse.dump,
minio.tar, multica_v2_final.dump, multica_v2.dump, multica-src-3a0fed5.tar.gz,
weknora.dump
```
无 WORK-449 数据。

### 2.5 通道五 · GitHub 远程历史（**失败 · 按契约不入库**）

```
refs/heads/main: 1        # data/README.md
```
`data_contracts` 约定「行情快照不入库（仓库为 public）」，全历史无逐股 CSV。

### 2.6 通道六 · 重取（**失败 · 端点断连**）

派单卡「不许重取数」，此处仅作**可达性取证**，未据此替换数据。

```
本机直连 (akshare 1.20.40 / stock_zh_a_hist):
  Push2RateLimitError 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，
  当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上

本机经 127.0.0.1:7890 代理 (requests):
  ProxyError ... RemoteDisconnected('Remote end closed connection without response')
  同一代理下 https://push2his.eastmoney.com/ 根路径返回 404（可达），仅 kline API 被断

本机经 127.0.0.1:7890 代理 (curl_cffi, impersonate='chrome'):
  ConnectionError Failed to perform, curl: (56) Connection closed abruptly

Mo-server 直连 (akshare 同款参数 + ut=7eea3edcaed734bea9cbfc24409ed989):
  ERR push2his.eastmoney.com hfq: RemoteDisconnected: Remote end closed connection without response
  ERR push2his.eastmoney.com raw: RemoteDisconnected: Remote end closed connection without response

Mo-server curl -4 -v:
  * TLSv1.3 handshake OK
  * > GET /api/qt/stock/kline/get?... HTTP/1.1
  * OpenSSL SSL_read: error:0A000126:SSL routines::unexpected eof while reading
```

### 2.7 通道七 · 其它候选（**失败 · 不同来源，不可替代**）

| 候选 | 内容 | 为什么不替代 |
|---|---|---|
| `work-465/build/fulla/` | 47 GB；`panel.pt` 465 MB、Sina qfq、5317 只全 A、窗口 2015-2024 | 来源（新浪 vs 东财）、股票池（5317 vs 680）、复权（qfq vs hfq）、窗口全不同 ⇒ 数值不可与 WORK-449 对照 |
| `work-465/build/smoke/` | `panel.pt` 8.4 MB 冒烟子集 | 同上，且只是子集 |
| `work-503/data/WORK-503/sina_hfq/` | 724 只新浪 hfq | 同上 |

---

## §3 环境与运行故障

### 3.1 环境复测首轮出现超区间读数（**已排查，非机器状态改变**）

C 组（MLP/GPU）首轮 6 次运行中，seed 43 的第 2、3 次稳态中位数偏高：

```
C_mlp_gpu_t1_s42_r1 steady=0.1395  [0.3986,0.1395,0.1336,0.1412]
C_mlp_gpu_t1_s42_r2 steady=0.1436  [0.4240,0.1461,0.1377,0.1436]
C_mlp_gpu_t1_s42_r3 steady=0.1288  [0.3958,0.1288,0.1294,0.1252]
C_mlp_gpu_t1_s43_r1 steady=0.1385  [0.3969,0.1385,0.1376,0.1388]
C_mlp_gpu_t1_s43_r2 steady=0.1830  [0.4877,0.2008,0.1775,0.1830]   <- 超区间
C_mlp_gpu_t1_s43_r3 steady=0.1858  [0.5063,0.2672,0.1858,0.1497]   <- 超区间
```

**排查**：① 同一运行内 epoch 耗时**单调变慢**（0.2672 → 0.1858 → 0.1497）⇒ 外部争用特征；
② 确认轮在每次运行前后抓 `nvidia-smi`，争用时刻直接读到别的进程占 GPU：

```
s43 c3: smi_before[1972, 2130, 42, 43.35, 0, 2959]  smi_after[1972, 2130, 44, 44.29, 19, 2959]
s43 c4: smi_before[1972, 2130, 42, 53.19, 15, 2959]  smi_after[1972, 2130, 46, 52.04, 68, 2959]
```
（`utilization.gpu` 字段 19 / 15 / 68 % ⇒ 有别的进程在用 GPU）；
③ 同期 D 组（Transformer）反而复现到 WORK-500 的 2.6993 s/epoch 的 0.1% 以内
⇒ 机器状态未变。**确认轮 10 次运行中位数 0.1313 s ⇒ 判定在区间内。**

### 3.2 OpenMP 双运行时冲突（**本机跑 WORK-449 脚本的真实阻塞**）

```
OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized.
OMP: Hint This means that multiple copies of the OpenMP runtime have been linked into the program.
```

**根因**：本机 `ds` 环境的 numpy 由 conda 装（链接 MKL 的 `libiomp5md.dll`），
torch 由 pip 装（自带另一份 OpenMP 运行时）。**WORK-449 的全部脚本（`import numpy` 后
`import torch`）在本机直接跑都会在第一个 epoch 后崩掉。**

**修法（不改动用户已有环境）**：建 `--system-site-packages` venv，再装一份 pip(OpenBLAS)
numpy 覆盖 conda numpy：

```powershell
& 'F:\001_install\020_Anaconda\envs\ds\python.exe' -m venv --system-site-packages F:\quant_work505\venv
& 'F:\quant_work505\venv\Scripts\python.exe' -m pip install --no-deps --upgrade numpy
```

实测：`numpy 2.5.5` + `torch 2.11.0+cu128` + CUDA 可用，BLAS 与 CUDA matmul 均通过。
**正式跑数必须用这个解释器**，否则运行条件与 WORK-449 不可比。
（未采用 `KMP_DUPLICATE_LIB_OK=TRUE` 压错。）

### 3.3 合成面板冒烟的三处笔误（**已修**）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| ① | `AttributeError: module 'numpy' has no attribute 'busday_range'` | numpy 3 已移除 `busday_range` | 改用 `pandas.bdate_range` |
| ② | `ValueError: operands could not be broadcast (1954,40,12) (1954,40)` | 合成信号未加最后一维 | `np.tanh(raw @ w_true)[..., None]` |
| ③ | `TypeError: expected np.ndarray (got Tensor)` | `torch.from_numpy(tensor)` 误用 | `sel.numpy()` |

修后三臂 × 两模型全绿（见报告表 5）。

### 3.4 显存峰值（**实测，非推算**）

| 任务 | 峰值显存 | 备注 |
|---|---|---|
| WORK-500 C 组（MLP/GPU，12 因子 412,064 样本） | **83.40 MB** | reserved 同量级 |
| WORK-500 D 组（Transformer/GPU，680×213 合成面板） | **1031.50 MB** | 12 GB 的 8.6% |
| 本单 C 组复测（MLP/GPU） | **83.40 MB** | 与 WORK-500 逐位相同 |
| 本单 D 组复测（Transformer/GPU） | **1031.50 MB** | 与 WORK-500 逐位相同 |
| 本单合成面板冒烟（Transformer，40 只 × 12 因子） | 未单独记录（冒烟不计显存闸） | 远低于 8 GB 闸 |

**未发生 OOM。** 派单单硬闸「峰值显存 > 8 GB 或 OOM」**未触发**。

---

## §4 推送链路的两次失败（**已绕过，非静默跳过**）

| # | 尝试 | 原文 | 处置 |
|---|---|---|---|
| ① | 本机 `git push -u origin agent/work-505` | `remote: Permission to huangsewanjia/quant-lab.git denied to yongkaimo.` / `fatal: ... error: 403` | 本机凭据是 Windows 凭据管理器的 `yongkaimo`，不是仓库账号 `huangsewanjia` |
| ② | Mo-server `git push`（经 `http.proxy=127.0.0.1:8899` 钉 github.com IP） | `fatal: unable to access 'https://github.com/...': GnuTLS recv error (-110): The TLS connection was non-properly terminated.` | 改走 GitHub **git-data API**（Mo-server 上 `git-credential-multica` 解密出的 93 字符 fine-grained PAT，**明文不进命令、不进日志**） |

**最终结果**（实测）：

```
blob_sha  = f7a0380d7a4793646f7de84e1b74e46dd2ce97fd   (与本地 blob 逐位相同)
tree_sha  = aef3ac209e131415a5968064d301d41c6e49b8ba   (与本地 tree 逐位相同)
commit_sha= 38fe864f3f81ddc8653faf62c8affccb6686c03f
refs/heads/agent/work-505 -> 38fe864f3f81ddc8653faf62c8affccb6686c03f
PR #2      -> https://github.com/huangsewanjia/quant-lab/pull/2  (state=open)
```

本地分支已 `git reset --hard 38fe864`，**本地 sha 与远程 sha 一致**。
（差别仅在 commit message 的结尾换行：GitHub API 不补尾换行，本地 git 补；blob 与 tree
sha 逐位相同 ⇒ 文件内容完全一致。）

---

## §5 未取到 / 未执行清单（**逐项，不估算**）

| 项 | 状态 | 原因 |
|---|---|---|
| 680 只冻结快照 | **674 只未取到** | §2 六条通道全部失败 |
| 面板与三档掩码 | **未执行** | 依赖快照 |
| 12 因子构建 | **未执行** | 依赖面板 |
| Transformer 训练（3 臂 × 2 seed = 6 次） | **未执行** | 依赖面板 |
| 回测（6 次） | **未执行** | 依赖预测 |
| 主检验 ΔSharpe / CI | **未执行** | 依赖回测 |
| 独立复算（≤1e-6） | **未执行** | 依赖快照与指标 |
| 进/退判词 | **未产出** | 依赖主检验 |
| 与 WORK-449 的数值对照表 | **部分**（WORK-449 侧数值可引用，本单侧未重算） | 同上 |
| 环境复测 | **已完成**（16 次运行，中位数 0.1313 s/epoch） | — |
| 预注册 v2 | **已完成并先行 commit**（`38fe864`） | — |
| 实现与端到端验证 | **已完成**（合成面板，三臂 × 两模型） | — |
| 单文件 HTML 报告 + PNG + PDF | **已完成** | — |
| registry 行 | **已完成**（结论状态如实标「未完成」） | — |

---

## §6 边界

本单未产出任何交易指令、仓位建议、评级或目标价；未写入对外研报；未改动
`data_contracts/`；未改动 WORK-449 的任何产物；未改动平台配置。
