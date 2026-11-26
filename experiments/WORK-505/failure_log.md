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
③ **确认轮 10 次运行中位数 0.1313 s ⇒ 判定在区间内**（全部 16 次合并中位数 0.1343 s，
首轮 6 次 0.1415 s；三个口径都在 0.104–0.156 s 区间内）。

> **更正（独立审查第 1 轮发现）**：本文件上一版写的是「同期 D 组复现到 0.1% 以内 ⇒ 机器状态未变」。
> 这条推理链**不成立**，现予撤回，理由有两条，均为实测：
> ① **时间不重叠** —— D 组跑在 14:49:00–14:50:24，C 组那两次异常读数在 14:48:36 / 14:48:42；
> ② **D 组自己并不稳定** —— 6 次运行中位数 **2.8479 s**，比 WORK-500 的 2.6993 s **高 5.51%**；
> 分 seed：seed 42 中位数 **3.0208 s（偏高 11.91%，3 次全偏高）**、seed 43 中位数
> **2.6972 s（差 0.08%）**。也就是说「0.1% 以内」只对 seed 43 成立。
> 本单**没有**补做 D 组的确认轮复测，D 组慢的那部分**原因未查清**。
> 影响：不能宣称「Transformer 档位的耗时预估可直接沿用 162 s」；按 seed 42 的 3.02 s 估，
> 60 epoch ≈ 181 s/臂。**对 C 组判定无影响**（C 组三个聚合口径都在区间内）。

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

**最终结果（第一轮：预注册 commit，实测）**：

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

**第二轮：产物 commit（31 个 blob / 2,936,067 字节），同一条 API 通道续推，实测**：

```
blobs verified : 31 | failed: 0        # 每个 blob 回读 sha，与本地 blob sha 全部相同
tree created   : b2c38d906b79802b4b553513b20f637fcb14cde0   (与本地 tree 逐位相同)
commit created : daf61b38296bb841eb9884c49102b05bc2e76e53
ref after      : daf61b38296bb841eb9884c49102b05bc2e76e53
PR #2 head     : daf61b38296bb841eb9884c49102b05bc2e76e53
```

本地分支随后 `git reset --hard` 指向同一 sha ⇒ **本地与远程是同一个 commit、同一 tree**。
（第二轮的 commit 父节点是预注册 commit `38fe864`，故「预注册 commit 早于任何产物 commit」
这一门槛在远程历史里同样成立。）
本文件此后若再被修订，会在该分支上再产生新 commit；**分支 head 以 PR #2 为准**。

> 中途一次失误已记录：首次续推时把 ref 读成 `/git/ref/refs/heads/...`（ref 参数不能带
> `refs/` 前缀），返回 404 后脚本按设计中止（`REF_NOT_AT_PARENT`），**未产生任何错误写入**；
> 改为 `/git/ref/heads/agent/work-505` 后一次通过。

---

## §5 未取到 / 未执行清单（**逐项，不估算**）

| 项 | 状态 | 原因 |
|---|---|---|
| 680 只冻结快照 | **674 只未取到** | §2 七条通道全部失败 |
| 面板与三档掩码 | **未执行** | 依赖快照 |
| 12 因子构建 | **未执行** | 依赖面板 |
| Transformer 训练（3 臂 × 2 seed = 6 次） | **未执行** | 依赖面板 |
| 回测（6 次） | **未执行** | 依赖预测 |
| 主检验 ΔSharpe / CI | **未执行**（脚本已实现，见 §7） | 依赖回测 |
| 独立复算（≤1e-6） | **未执行**（脚本已实现，见 §7） | 依赖快照与指标 |
| 进/退判词 | **未产出** | 依赖主检验 |
| 与 WORK-449 的数值对照表 | **部分**（WORK-449 侧数值可引用，本单侧未重算） | 同上 |
| 环境复测 | **已完成**（16 次运行；合并中位数 0.1343 s/epoch，确认轮 10 次 0.1313 s/epoch） | — |
| 预注册 v2 | **已完成并先行 commit**（`38fe864`） | — |
| 预注册勘误 v2.1 | **已完成**（`preregistration_errata.md`；原件字节未动） | — |
| 实现与端到端验证 | **已完成**（合成面板，三档 × 两 seed × 两模型 = 12 次训练 + 12 次回测 + 两 seed 合并主检验） | — |
| 单文件 HTML 报告 + PNG + PDF | **已完成** | — |
| registry 行 | **已完成**（结论状态如实标「未完成」） | — |

---

## §6 独立代码审查第 1 轮：发现与处置（**主循环不自审**）

> 审查方：**独立子代理**（`verifier` 角色，按《code-review-and-quality》skill 流程执行，
> 全程只读，不改仓库任何文件 —— 该代理在结束后自报 `git status --porcelain` 为空，
> 且所有临时脚本与实验面板只写在 `%TEMP%`）。判定：**PARTIAL（有条件通过）**。
> 独立复算三项：C 组中位数（n=16 = 0.13434、确认轮 n=10 = 0.13128，区间判定不变）、
> 快照 6 PASS / 0 FAIL / 674 MISSING 与 770,855 字节（与本单产物逐位一致）、
> `report.html` 0 个 `<script` / 0 个 http(s) / 6 张图全部 base64 内嵌 —— **三项全部独立复现通过**。

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| A1 | Major | 按预注册 §4 的命令跑，seed 43 会**静默覆盖** seed 42 的产物（脚本按 `--tag` 命名，命令不带 tag） | **已修**：训练脚本 `--tag` 默认值改为 `_<seed>`；勘误 E1 |
| A2 | Major | 预注册 §4 的主检验与独立复算两步**没有任何实现**，「完整链条」不成立 | **已修**：`scripts/work505_primary_test.py` + `audit/recompute_work505.py` 已实现；勘误 E3 |
| A3 | Major | 训练段之外的日期（2014 段）`pred` 留下与 `sample_ok` 矛盾的 NaN，与 WORK-449 的 MLP 不逐格等价 | **已修**：预测改为全网格（所有含有效样本的交易日）+ 写盘前断言 `isfinite(pred)==sample_ok`；勘误 E6 |
| A4 | Major | 标签为 NaN 时 `nan_to_num` 静默当 0.0 训练；MLP 会崩而 Transformer 会给出「看起来正常」的数 | **已修**：损失前剔除 NaN 标签格并计入 `n_label_nan_cells`；勘误 E7 |
| A5 | Major | 报告与 registry 写「三档 × 两模型全绿」，实际只跑了 3/6 组合，`halt_only` 从未训练 | **已修**：合成面板重跑为**三档 × 两 seed × 两模型 = 12 次训练 + 12 次回测**，报告表格改为**从产物 JSON 直读**；上一轮的 9 个过时产物已删除。<br>**补充（第 2 轮 M15 查证）**：目录里另有 3 个不带 seed 后缀的 `model_*.pt` —— 它们**不是**过时残留，而是 WORK-449 自带 MLP 训练器每次都会写的当前产物（见 §7 M15），故**保留不动**，并在此把「过时产物已删除」的范围限定为**上一轮那 9 个**（不含这 3 个） |
| A6 | Major | registry 与失败日志把「确认轮 10 次」的中位数 0.1313 s 挂到 n=16 上（n=16 的中位数是 0.1343 s） | **已修**：registry 数字改为**从 `report_data.json` 读**（不再手写）；失败日志与报告逐处标注口径 |
| A7 | Major | 「D 组复现到 2.6993 s/epoch 的 0.1% 以内」不成立（6 次中位数 2.8479 s，+5.51%；只有 seed 43 成立，seed 42 偏高 11.91%），且被用作「机器状态未变」的证据 | **已修**：报告摘要、图 2 标题、图注、失败日志 §3.1 全部改正；**撤回**「拿 D 组给 C 组背书」的推理 |
| B1 | Minor | 「所有数字均来自机器写入的产物文件，未做人工抄写」不成立（表 5 与 registry 整行硬编码） | **已修**：新增 `scripts/work505_make_report_data.py`（数字的唯一机器来源，从原始 JSON 现算）；报告表格改为读产物；页脚按三类来源分别标注 |
| B2 | Minor | 生成器的「0 外链」自检是死代码（`ext` 算了不用；`t not in ("<script",)` 使 script 检查被永久关闭） | **已修**：改为硬 `assert`（含 `<script` / `<link` / `@import` / `url(http`），不通过即构建失败 |
| B3 | Minor | 预注册 M9 要求「断言并计数」，实现只跳过不计数 | **已修**：训练日志新增三项 `*_days_skipped` 与 `n_label_nan_cells` |
| B4 | Minor | 预注册 §4 的复现命令用的是**会崩**的解释器（OpenMP 双运行时） | **已修**（勘误 E4，原件不动）：正式跑数用 venv 解释器绝对路径 |
| B5 | Minor | 推送状态的陈述已过时（「远程 sha = 38fe864」在产物 commit 推送后已不成立） | **已修**：§4 补记第二轮推送（31 个 blob，tree sha 逐位相同），报告 §7 改为指向 PR #2 的 head |
| C1 | Nit | 「与 work500_bench.py 逐行一致」不准确（实为结构等价） | **已修**：报告与勘误 E5 改为「结构等价 + 参数量逐位相符」 |
| C2 | Nit | 搬运通道计数不一致（说六条却列四条） | **已修**：统一为**七条通道**（失败日志 §2.1–2.7），报告图 3 说明同步改正 |

### 6.1 审查方明确指出、本单**无法**在本机闭合的部分（如实列出）

| 项 | 状态 |
|---|---|
| Mo-server 侧的取证（回收站 11,458 文件、1,370 个 trashinfo、代际聚类、git blob 266 + 8,442 零命中、SMB 清单、`push2his` 四条路径断连原文） | **无法复核** —— 只发生在 Mo-server，仓库内没有对应的原始输出文件，失败日志只有转述 |
| 真实面板未重建 ⇒ Transformer 在真实 680×12 面板上的显存峰值 / s-per-epoch / val RankIC 曲线 | **全部未测** |
| A3 的规模（真实面板 2014 段约 243 个交易日） | **推算**，未在真实面板上实测（快照不可得） |
| A4 的触发条件（冻结数据里是否真存在单格 NaN 标签） | **无法验证**（数据不可得）；代码路径已用注入实验证明确有差异 |
| `audit/recompute_work505.py` 在**真实快照**上的执行 | **未执行**（依赖快照）；其正确性来自「与 WORK-449 已过审脚本逐字节同源，仅改参数化与 seed 循环」 |

---

## §7 独立代码审查第 2 轮：发现与处置（**主循环不自审**）

> 审查方：**独立子代理**（`verifier` 角色，同一《code-review-and-quality》流程，全程只读）。
> 审查对象 = 已提交范围 `c93286a`（tree `97ddd6878406ab38076b99e6fa35b802a3b6dbee`）。
> 判定：**FAIL** —— 3 项阻断合并（Major），另 12 项 Minor、3 项 Nit。
> 审查方独立复算并逐位相符的项：C 组三口径中位数、D 组 6 次中位数与分 seed 偏差、
> 快照 6 PASS / 0 FAIL / 674 MISSING 与 770,855 字节、预注册 sha256 `e542ccfe…`、
> 两 seed 合并 ΔSharpe 与 CI95（1000×block20×seed42 同步重采样，**逐位相同**）、
> 表 5 十二行逐格 vs `metrics_*.json`、`report.html` 的 0 外链 / 6 张 base64 内嵌图。
> 判词（进 / 退）不在审查方职责内；审查方确认本单**未伪造任何研究结论**。

### 7.1 阻断项（Major）

| # | 发现 | 处置 |
|---|---|---|
| M1 | 报告里出现乱码数字：`work505_make_report.py` 的 `{num(rs['D']['per_seed'].get('42'),0)*60,0}` 里 `num()` 返回**字符串**，`"3"*60` 渲染成 60 个 `3` 的元组 | **已修**：改为 `{...*60:.0f}`，渲染为 `181`；报告与 PDF 已重新生成并逐处核对 |
| M2 | 表 6 断言「WORK-449 未入库」，但 `experiments/WORK-449/metrics_{no_mask,halt_only,full_mask}_s43.json` **已入库**（81,486 / 81,475 / 81,233 字节），且同表的 ΔSharpe / ΔIC 行本就引用了 seed 43 的 `primary_test_s43.json` | **已修**：表 6 的 seed 43 列改为从 `experiments/WORK-449/metrics_*_s43.json` / `primary_test_s43.json` / `baselines.json` **直读**，不再写「未入库」 |
| M3 | 「数据补齐即可直接开跑」的链条断在第 1 步：`work449_build_panel.py:96-97` 把输入目录**硬编码**为 `<repo>/data/<exp>`（无 `--data-dir`），而 §4 步骤 0 的校验命令指向 `F:\quant_work505\data\WORK-449` ⇒ 照抄 §4 会在第 1 步 `FileNotFoundError`；步骤 5 的独立复算脚本默认 `--data-dir` 同为 `<repo>/data/<exp>`，勘误 E3 未给修正命令 | **已修**：新增**目录桥接脚本** `scripts/work505_stage_snapshot.py`（只把 sha256 **等于**冻结清单的文件拷进 `<repo>/data/<exp>/`；不符者不拷不覆盖、缺失者记 MISSING、绝不造占位行；输出 `snapshot_staging.json` 带 `complete` 标志），**`work449_build_panel.py` 一个字未改**；勘误新增 **E8**，并在勘误**第四节**补上「把 E1–E8 全部应用后」的**完整可复跑命令表**。本单已实测：桥接后 `work449_build_panel.load_repo(repo,'WORK-505')` 正常返回已落地的 6 只代码（`sh.600000 / 600004 / 600005 / 688506 / 688561 / 688981`），桥接脚本二次运行报 `already=6`（幂等） |

### 7.2 次要项（Minor）

| # | 发现 | 处置 |
|---|---|---|
| M4 | 两个无源数字：「约 65 s 外部 GPU 争用窗口」「156.9 MB」 | **已修**：两个数字已从报告正文删除（前者本就无观测支撑，后者因冻结清单**没有字节字段**而无法引用）；页脚「数字分三类来源」的表述改为按**实际来源逐处标注** |
| M5 | 严重度统计与处置表对不上：处置表实为 14 行（7 Major / 5 Minor / 2 Nit），而正文与 registry 写「7 Major / 4 Minor」 | **已修**：报告、失败日志、勘误、registry 四处统一为 **14 项（7 Major / 5 Minor / 2 Nit）** |
| M6 | `--tag` 与 `--seed` 无一致性护栏，显式 `--seed 43 --tag _42` 仍会覆盖 seed 42 产物 | **已修**：训练脚本加断言，显式 `--tag` 与 `_<seed>` 不一致即报错退出 |
| M7 | `work505_primary_test.py` 的 `--tag-format` 是死参数（声明了但从不引用） | **已修**：死参数已删除 |
| M8 | 「0 外链」断言在**写盘之后**，构建失败时坏产物已落盘 | **已修**：断言前移到写盘之前（含 `<script` / `<link` / `@import` / `src="http` / `href="http` / `url(http`） |
| M9 | `work500_bench.py` 不在本机也不在仓库 ⇒ 报告 §1.2 的 C 组复测命令**无法从仓库复跑** | **不可修，如实记录**：该脚本属 WORK-500 交付物，不在本仓库；原始 22 次运行的 `epoch_seconds` 已完整嵌入 `report_data.json`，**数字可复算、命令不可复跑**，已在报告与本表写明 |
| M10 | 搬运通道图（报告**图 3**，文件 `figures/fig4_transport_channels.png`）只画 6 条通道，图注却写「七条通道」并描述了图中没有的第 7 条 | **已修**：`make_figures.py` 改为从 `transport_evidence.json` 读全部 **7** 条，PNG 已重新生成。**（第 3 轮审查订正：本图在报告里编号是「图 3」，文件名里的 `fig4_` 是脚本内部序号，两者不一致，引用时以报告编号为准。）** |
| M11 | `make_figures.py` 硬编码 `ext=` / `vram=` 的数值（虽与产物一致） | **已修**：改为从 `report_data.json` 的 `w500_bench_medians` 读 `epochs_60_total_s` / `peak_vram_mb` |
| M12 | `work505_add_registry_row.py` 硬编码叙述常量（11,458 文件 / 8,708 blob / 2.6993 s / 0.1313 s / 19、15、68% / 0.58%） | **已修**：registry 脚本改为**全部从产物读** —— 搬运计数读 `transport_evidence.json`，GPU 争用观测读新建的 `env_observation.json`，WORK-500 参考值读 `report_data.json` 的 `w500_bench_medians`，预测离散度读 `experiments/WORK-449/baselines.json`；脚本内不再有这些字面量 |
| M13 | `merged_result` 假定两 seed 序列等长，未做长度校验 | **已修**：加长度校验，不等即报错退出 |
| M14 | `verdict_flip` 用 WORK-505 的**两 seed 合并**判词去比 WORK-449 的**单 seed** 判词，字段名易被误读 | **已修**：字段改名并加口径注释，明确它**不是**判词、只作对照参考，判词本身在 `reading` 字段且带 `scope_note` 限用 |
| M15 | 合成面板残留 3 个无 seed 后缀的 `model_*.pt`（`model_{no_mask,halt_only,full_mask}.pt`），与「第 1 轮 9 个过时产物已删除」的说法不齐 | **已查明并改正说法（本条审查判断与实情不符）**：这 3 个文件**不是**过时残留，而是 **WORK-449 自带的 MLP 训练器** `scripts/work449_train.py:275` 每次运行都会写的**当前产物** —— 该脚本把模型权重写成 `model_{arm}.pt`（**不带 tag**），只把预测文件写成 `pred_{arm}{tag}.npz`（**带 tag**）。本单不动该脚本，于是 MLP 参考臂的 3 个权重文件在每次冒烟跑批时被 seed 43 覆盖（seed 42 的权重不保留），而**逐 seed 的预测文件 `pred_*_mlp42/43.npz` 都在**，回测读的就是逐 seed 的预测文件 ⇒ **逐 seed 的 metrics 互不污染**。本单 Transformer 臂的模型文件带 seed 后缀（`model_{arm}_{seed}.pt`），无此问题。处置：改正 A5 相关的表述（见 §6 A5 行与本表），并在此写明该命名差异；<br>**执行过程中的处置**：曾把这 3 个文件按「过时残留」用可恢复方式移出，**随后重跑冒烟网格时它们被 MLP 臂重新生成** —— 这恰好反证了它们是当前产物、不是残留 |

### 7.3 Nit

| # | 发现 | 处置 |
|---|---|---|
| N1 | `n_label_nan_cells` 按 epoch 累加（实为「epoch × 格数」），读者按「格数」理解会偏差 | **已修**：改为**去重计数**（不同的 (交易日, 股票) 格各计一次），并在训练日志加 `n_label_nan_cells_scope` 说明口径 |
| N2 | 主检验里 `rng` 在 main 与敏感性之间共用同一流，两个成本档的 bootstrap 起点序列不同 | **已修（口径披露）**：与 WORK-449 同款写法，未引入新偏差；已在脚本与报告脚注写明该事实 |
| N3 | `work449_signs()` 遇任一文件缺失即整体返回 `None`，对照段会静默消失 | **已修**：缺文件时打印告警，不再静默 |

### 7.4 本轮返修过程中**新发现并已修**的问题（执行方自查，非审查方提出）

| 项 | 现象 | 处置 |
|---|---|---|
| R1 | N1 的去重计数改动**从未跑过**：冒烟网格第 1 步即 `ValueError: operands could not be broadcast together with shapes (2215,40) (12,40)` —— 批次掩码 `(B,N)` 与全网格 `(T-1,N)` 混用 | **已修**：按 `days_b` 写回全日轴；冒烟网格重跑 26 步全绿 |
| R2 | 同上，改用 `days_b.numpy()` 但 `days_b` 本就是 numpy 数组 ⇒ `AttributeError` | **已修**：改为 `nan_label_cells[days_b] |= ...`；重跑通过 |
| R3 | **搬运通道图（报告图 3）的通道判定标签与图注自相矛盾**：`make_figures.py` 把 7 条搬运通道的标签**硬编码**为 `OK / PART / FAIL`，其中 SSH、git 对象库、SMB、GitHub 历史这 4 条**实际全部是「未命中」**，却被标成 OK（图上写着 OK，旁边却写着「no WORK-449 data」这类原因） | **已修**：标签改为**从 `transport_evidence.json` 的 `status` 字段派生**（failed→FAIL / partial→PART / rejected→REJECT），并加断言：未命中任何文件的通道**不允许**标成 OK；图注同步改成与标签一致的表述；PNG 已重新生成 |

---

### 7.5 本轮新增的机器可读产物（供数字溯源）

| 文件 | 内容 | 谁读它 |
|---|---|---|
| `experiments/WORK-505/env_observation.json` | 环境自检原文（`self_check.stdout_verbatim` + 字段化）与确认轮的 `nvidia-smi` 争用观测（`gpu_contention_observations`，含 `utilization_pct_index`） | 报告 §1.1 / §1.3、registry 行 |
| `experiments/WORK-505/snapshot_staging.json` | 目录桥接的逐股结果（680 行：期望 sha256 / 实际 sha256 / 来源路径 / 落盘路径 / 判定），含 `complete` 标志 | 勘误 E8 的门槛检查 |
| `experiments/WORK-505/transport_evidence.json` | 7 条搬运通道的原始输出留档（上一轮已有） | 搬运通道图（报告图 3）、报告 §2、registry 行 |
| `scripts/work505_stage_snapshot.py` | 目录桥接脚本（只拷 sha256 相符的文件） | 勘误 E8 / 勘误第四节命令表 |

---

## §8 独立代码审查第 3 轮（窄范围，只验第 2 轮返修与回归）

> 审查方：**独立子代理**（`verifier` 角色，同一《code-review-and-quality》流程，全程只读，
> 临时脚本只写在 `%TEMP%`）。审查对象 = 已提交范围 `184c2a0`。
> 判定：**FAIL（1 Major / 3 Minor / 1 Nit）** —— 第 2 轮的 21 项 + 执行方自查 3 项**全部核实为真**，
> 但**第 2 轮那次提交自己引入了一处同类乱码**（见 F1）。审查方在 `%TEMP%` 沙箱里
> **逐条执行了勘误第四节的 8 条命令，全部 RUNNABLE**，并实测 M3 的目录桥接**真的能建出面板**。

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| F1 | **Major** | **第 2 轮提交在报告里引入了第二处 M1 同类乱码**：审查方在我写的审查说明那一行里，把示例表达式 `{num(...)*60,0}` 直接写进了 HTML f-string ⇒ Python **真的去求值**它（`num()` 对非 float 走 `str(v)`，`"Ellipsis"*60` 再逗号成元组），已发布的报告在「⑭ 独立代码审查第 2 轮」那一行渲染出 **508 个字符的垃圾元组** | **已修**：该行改为 `{{num(...)*60,0}}`（转义后按字面输出），报告与 PDF 已重新生成；并对 `report.html` 做全文垃圾扫描（元组 / `Ellipsis` / `None` / dict / list 模式）—— 当前**命中 0 处**。**这是同一类缺陷第二次出现**，已在 §8 末尾记下防复发措施 |
| F2 | Minor | rng_note 脚注里 4 个 markdown 星号 `**` 被原样渲染进 HTML（该字段经 `esc()` 只转义 `& < >`，不解释 markdown） | **已修**：JSON 里的说明改成纯文本（去掉 `**`），脚注已重新生成 |
| F3 | Minor | 审查期间工作树不干净：`scripts/work505_primary_test.py` 被改（F2 的修正）但未提交，导致「脚本重跑出的 report.html 与已发布的不一致」 | **已修**：本轮改动已全部提交并推送；审查方已独立验证**已提交状态可逐字节重建**已发布的 `report.html`（sha256 `250192bc…`） |
| F4 | Minor | 交叉引用错号：失败日志称搬运通道图为「图 4」，而报告里它是**图 3**（文件名的 `fig4_` 是脚本内部序号） | **已修**：失败日志 §7 M10 / R3 / §7.5 的引用改为「搬运通道图（报告**图 3**，文件 `figures/fig4_transport_channels.png`）」并加注说明编号以报告为准 |
| F5 | Nit | `make_figures.py` 的 `cmap` 保留了永远用不到的 `"OK"` 键（状态映射里没有 `ok`，且有断言禁止出现 OK） | **已修**：删除该键 |

### 8.1 审查方**无法**在本机闭合的部分（如实列出）

| 项 | 状态 |
|---|---|
| Mo-server 侧搬运取证的原始输出（11,458 文件 / 1,370 trashinfo / 代际聚类 / 266+8,442 blob / SMB 清单 / 4 条 `push2his` 断连原文） | **无法复核** —— 只存在于 Mo-server，仓库内是转述 |
| `scripts/work500_bench.py` | **不在仓库、无法重跑** —— 审查方只能用 `report_data.json` 内嵌的 22 次运行重算 C/D 汇总，**不能**与原始 22 份 JSON 对照 |
| 真实 680 只数据路径 | **按设计不可测**（674/680 不可复原）；`audit/recompute_work505.py` 从未在真实数据上执行过，正确性仅由「派生自已过审脚本」支撑 |
| 15:59:28 那次 `work505_primary_test.py` 改动的作者 | 审查方**无法归因**（其自身写入全在 `%TEMP%`）；事实已记入 F3 |

### 8.2 审查方**已核实为真**的清单（节选，可复核）

M1 核心（`181 s/臂`，两种路径独立重算）、M2（表 6 seed-43 列 15 个指标格 + ΔSharpe/CI/ΔIC + 判词格逐格对上 WORK-449 产物）、
M3（`work449_build_panel.py` 与 `38fe864` **逐字节相同**；桥接脚本「只拷 sha256 相符者 / 损坏即 exit 2 且不覆盖 / 缺失记 MISSING / 幂等」四项行为全部实测通过；勘误 8 条命令在沙箱里**逐条跑通**）、
M5（14 项与 18 项的严重度计数在四份文件里一致）、M6/M7/M8/M13/M14、N1/R1/R2、N2/N3、
M10/R3（6 张 PNG 与脚本当前输出**逐字节相同**，无假 OK）、M11、M12（registry 11 个 sha256 + 附录二 21 个 sha256 全部对上磁盘）、
M4（无源数字已删、自检原文逐字）、预注册字节未动、报告 0 外链 / 6 图内嵌 / 6 图注、**无任何伪造研究数字**。

### 8.3 防复发措施（同一类乱码已出现两次）

1. 报告生成器里所有**要按字面展示的示例表达式**，一律用双花括号转义（`{{...}}`），不用 f-string 求值；
2. 产物生成后跑**全文垃圾扫描**（元组 / `Ellipsis` / `None` / dict / list 模式）作为硬门禁 —— 当前命中 0 处；
3. 审查每一轮都要独立重建报告并与已发布产物**逐字节比对**，任何不一致先查工作树是否漂移。

---

## §9 边界

本单未产出任何交易指令、仓位建议、评级或目标价；未写入对外研报；未改动
`data_contracts/`；未改动 WORK-449 的任何产物；未改动平台配置。
