# WORK-566 第 3 轮独立代码审查（verifier 角色）

- **审查人**：独立审查子代理（verifier），未参与任何代码编写；本轮全程**只读**，未修改任何项目文件（唯一写入为本报告）。
- **审查时点**：2026-11-25 18:06–18:50 CST。
- **复审对象**：`686b921`（B2 修复 + tmpfs 陈旧校验）→ `75193d4`（行序连续写 + `--resume-rep`）→ `ad86424`（HEAD，Stage 3 前置检查）。
- **本机状态**：正式训练**正在跑**（PID 3141343，`--seeds 42 --threads 6 --rep-batch 1024 --resume-rep`），未启动任何训练/回测/Stage 3 全链。
- **临时产物**：`/srv/data/.../workdir/.w566review3/`（`/tmp` 配额已被同机其他进程占满，实测 `cp` 报 `Disk quota exceeded`）。

> **关于本文件被占用**：本轮开始时该路径已存在一份标题相同的报告（另一审查子代理 `bg_c68840f6` 所写，判定 2 项 BLOCKING：B3 checkpoint 别名、B4 fmb 漏检）。我已将其原文备份到 `.w566review3/prior_round3_other_reviewer.md`，并在**附录 A** 完整保留其结论供交叉对照。本文件正文为**我独立复算后**的判定，与该报告在 B3 上**一致**，在 B4 的**失效机制**上**不一致**（我实测 xargs 会掩盖失败，链不会中止），另新增 3 项该报告未列的问题。

---

## 1. 判决

**FAIL** —— 存在 **3 项 BLOCKING**：B3（表征 checkpoint 落盘的不是「验证损失最小」权重，违反预注册 §12 冻结条款，且当前正在跑的正式训练正在消费这份 ckpt）、B4（`preds_seed42_fmb.npz` 缺失，Stage 3 回测步骤必崩且**失败会被 xargs 掩盖**、链不会中止）、B5（`--resume-rep` 无配置/完成度校验，当前 `upd=1000` 的周期快照被当作正式表征静默复用）。

**B2 本身（`u` 写回改用 split 内位置）修对了**：越界已消除，行对齐正确，未引入「u 与 y 错行」。

---

## 2. BLOCKING 清单

### B3 — 表征 checkpoint 落盘的是「最后一轮」权重，不是预注册选定的「验证损失最小」权重

- **文件:行号**：`scripts/work566_train.py:479-484`（`m_k`/`v_k` 原地更新）、`:535-537`（构造 `best[1]`）、`:538`（epoch 末保存 best）、`:539-540`（训练结束后**再次** `torch.save(best[1], ckpt_path)`）、`:518-523`（周期保存也写同一 `ckpt_path`）。消费点：`:618`（`build_reps` 载入）、`:630`（`m_k`/`v_k` → Δ̂ 列）、`:947-952`（`--resume-rep` 复用）。
- **实测值**：`best[1]` 里的 `enc.state_dict()` 是**与活参数共享存储的视图**（`data_ptr()` 相同 → True），`best[1]["m_k"]` 与 `m_k` 是**同一张量对象**（原地 `mul_/add_` 更新，`scripts/work566_train.py:479-484`）。构造 best 后训练继续一轮，再读 `best[1]["m_k"]` 得到 `[0.1,0.1,0.1,0.1]`，而构造时刻是 `[0,0,0,0]` → **别名确认，best[1] 会被后续 epoch 改写**。
  落盘证据：磁盘上的 `data/WORK-566/ckpt_seed42_rep.pt`（18:04:15，891,865 B）内含 `upd=1000`，而训练日志显示本次 seed42 **跑到 upd=4000**（`.w566tmp/w566_tick.log:34` 「epoch3 upd4000」）→ 该 ckpt 是 `save_every=100` 的**周期快照**（`:518-523`），不是完整训练后的选型权重。
- **预期值**（预注册 `experiments/WORK-566/preregistration.md:245`）：「checkpoint 选取：验证子集上 `L_anc + L^impl_rev` 最小者」；`:41` 的表格亦写明「验证 | 表征 checkpoint + 读出 checkpoint 选型 | 2022-01-01 ~ 2023-12-31」。
- **差距**：落盘的 `enc/proj/proj_ema/qpred/m_k/v_k` 是**最后一轮**的权重与运行统计，而非验证损失最小者对应的权重与统计。`m_k`/`v_k` 直接决定 u 的 Δ̂ 列（`:630`,`:682`），`enc`/`proj_ema`/`qpred` 决定 z_anc 与 Δ̂ → **全部下游数字（RankIC、净 SR、H1 判词）随之改变**。
- **复现命令**：
  ```bash
  cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir/quant-lab
  ../.venv/bin/python - <<'PY'
  import torch, torch.nn as nn
  enc = nn.Linear(4,4); m_k = torch.zeros(4)
  best = (10.0, {"enc": enc.state_dict(), "m_k": m_k})
  snap = best[1]["m_k"].clone()
  with torch.no_grad(): enc.weight.add_(1.0); m_k.mul_(0.9).add_(0.1)
  print("别名确认:", best[1]["m_k"].tolist(), "构造时:", snap.tolist())
  print("共享存储:", best[1]["enc"]["weight"].data_ptr() == enc.weight.data_ptr())
  PY
  ```
  另：`../.venv/bin/python -c "import torch;print(torch.load('data/WORK-566/ckpt_seed42_rep.pt',map_location='cpu',weights_only=False)['upd'])"` → `1000`。
- **对第 2 轮 B2 修复记录的影响**：`experiments/WORK-566/failure_log.md:601-602`（「checkpoint 即**按验证损失选出的最优**权重」）与 `scripts/work566_train.py:949-951` 的注释**陈述为假**；`--resume-rep` 等价性的论证前提随之失效（等价性只在「同一份 ckpt」下成立，而该 ckpt 本身不合协议）。
- **建议修法**：`:535-537` 改为深拷贝（`{k: v.detach().clone() for k,v in enc.state_dict().items()}`、`m_k.clone()`、`v_k.clone()`）；删除 `:539-540` 的末尾覆盖保存（`:538` 已是正确快照）；周期保存写**独立路径**（如 `ckpt_seed{S}_rep_last.pt`），避免与选型 ckpt 混用。修完**必须重跑表征与全部下游**。

### B4 — `preds_seed42_fmb.npz` 缺失，Stage 3 回测步骤必崩；且失败会被 xargs 掩盖，链不会中止

- **文件:行号**：`scripts/work566_run_stage3.sh:9`（`ARMS="... fmb"`）、`:12-26`（新增的 `[0/7]` 前置检查，**只列 8 个训练臂**）、`:40-46`（回测循环）；`scripts/work566_backtest.py:407`（`np.load(preds_seed{seed}_{arm}.npz)`）；产出方 `scripts/work566_baselines.py:146`（**另一个脚本**，训练脚本 `scripts/work566_train.py:958` 的臂循环**不含 fmb**）；`scripts/work566_delivery_check.py:31`（`ARMS` 同样只有 8 臂）。
- **实测值**（当前磁盘）：
  ```
  OK   data/WORK-566/preds_seed42_s1_only.npz … preds_seed42_r3.npz   （8 个训练臂在）
  MISS data/WORK-566/preds_seed42_fmb.npz          ← 回测 fmb 臂的必需输入，当前确实缺失
  OK   data/WORK-566/preds_seed2024_fmb.npz (78,116,678 B)
  ```
  实测跑 `../.venv/bin/python scripts/work566_backtest.py --arm fmb --seed 42 --cost 0.0005 --out /tmp/.../bt.json` → `FileNotFoundError: .../data/WORK-566/preds_seed42_fmb.npz`（**实跑，非静态推理**）。
  该文件**并非不可恢复**：回收站里有一份完好的 `~/local/share/Trash/files/preds_seed42_fmb.npz`（78,116,678 B），我用独立实现复算其 test 段 RankIC = **9.377620322596213%**，与已入库的 `experiments/WORK-566/backtest_fmb_seed42_c0.0005.json` 声称值 **相对差 = 0.0** → 文件内容有效，**只是被 14:25:03 的清理误删**（`Trash/info/preds_seed42_fmb.npz.trashinfo` 记录 `DeletionDate=2026-11-25T14:25:03`）。
- **预期值**：`[0/7]` 前置检查的臂列表应与 `:9` 的 `ARMS` 一致（`scripts/work566_run_stage3.sh:12-26` 的循环写死 `for arm in s1_only s3_only s1_s2 full r0 r1 r2 r3`，**无 fmb**）。
- **差距**：① 前置检查**放行**（8 臂齐备）→ 回测阶段 fmb 三个 job 抛 `FileNotFoundError`；② **失败不会中止链** —— 实测复刻 `scripts/work566_run_stage3.sh:25-29` 的 `xargs -P ... bash -c '...; echo "完成 ..."'` 包裹：内层命令失败但 `echo` 成功 → **xargs 整体退出码 = 0**（实测 `xargs EXIT=0`）→ `set -euo pipefail`（`:5`）**不触发** → 链**继续**跑主检验/独立复算/报告/渲染/manifest/自检；③ 后果比「崩链」更糟：`work566_primary_test.py` 的 H2（full vs FMB）在缺 seed42 的 fmb 预测下**只拿到 1 个种子**，而 `:186-188` 的「种子 < 2 → 未交付」守卫**只作用于 H1**（`h1`），H2 无同等守卫 → **H2 会用单种子结果照常出结论**；④ `audit/recompute_work566.py:313-315` 对缺失产物是 `SKIP（缺产物）` → 审计**不报 BLOCKING**，同样静默放过。
- **复现命令**：
  ```bash
  cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir/quant-lab
  ls -la data/WORK-566/preds_seed42_fmb.npz            # → No such file
  ../.venv/bin/python scripts/work566_backtest.py --arm fmb --seed 42 --cost 0.0005 --out /tmp/x.json   # → FileNotFoundError
  # 失败掩盖的复刻：
  printf 'a\nb\n' > jf.txt && xargs -P 1 -n 1 bash -c 'false; echo "  完成 arm=$1"' _ < jf.txt; echo "xargs EXIT=$?"   # → xargs EXIT=0
  ```
- **建议修法**：① 立即把回收站里的 `preds_seed42_fmb.npz` 恢复到 `data/WORK-566/`（已验证内容有效），或重跑 `scripts/work566_baselines.py --seed 42`；② `:12-26` 的前置检查臂列表与 `:9` 的 `ARMS` 对齐（含 fmb）；③ `scripts/work566_delivery_check.py:31` 的 `ARMS` 纳入 fmb（C1 现称「16 个臂×种子」，实际漏 2 个 fmb×种子）；④ 把内层 `bash -c` 的最后一条命令改为 `exec "$PY" ...` 或在 `echo` 后加 `|| exit`，让失败能穿透 xargs。

### B5 — `--resume-rep` 不校验配置与完成度，当前 `upd=1000` 的周期快照被当作正式表征静默复用

- **文件:行号**：`scripts/work566_train.py:884-885`（新增 `--resume-rep`）、`:947-952`（复用判定**只判 `os.path.exists`**）；周期快照的写入点 `:518-523`。
- **实测值**：磁盘上 `data/WORK-566/ckpt_seed42_rep.pt` 的 `upd = 1000`（周期保存点，`save_every=100`），而训练实际跑到 `upd=4000`（`.w566tmp/w566_tick.log:34`）→ **半成品**；`:947-952` 仍会把它当成「验证损失最优权重」复用（日志已实测：`[18:25:22] --resume-rep：复用已有表征 checkpoint …/ckpt_seed42_rep.pt，跳过表征训练`）。该 ckpt 的 mtime 18:04:15，正是上一次 OOM/中断前最后一次落盘。
- **预期值**：复用前应校验（a）配置指纹（`d_model/layers/heads/ff/epochs/max_updates/val_stride`）；（b）训练完成度（`upd == epochs × max_updates`，或存在独立的 `best` 标记）；（c）落盘的是**选型**权重而非周期快照。
- **差距**：当前正式训练（PID 3141343）**正在用**这份半成品 checkpoint 重建 u → 交付的表征与「完整重训 4000 upd + 验证选型」的协议口径不一致；且该问题与 B3 叠加（B3 使「best」本身也不等于选型权重）。
- **复现命令**：
  ```bash
  cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir/quant-lab
  ../.venv/bin/python -c "import torch;print(torch.load('data/WORK-566/ckpt_seed42_rep.pt',map_location='cpu',weights_only=False)['upd'])"   # → 1000
  grep -n "upd4000" ../.w566tmp/w566_tick.log
  ```
- **建议修法**：ckpt 内写入 `cfg` 指纹 + `complete` 标志 + `best_vloss`；`--resume-rep` 校验不通过则拒绝复用并重训（配合 B3 的独立选型路径）。

---

## 3. NON-BLOCKING 清单

| 编号 | 严重度 | 文件:行号 | 问题 | 实测值 / 预期值 / 差距 | 复现命令 |
|---|---|---|---|---|---|
| N-R1 | 中 | `scripts/work566_train.py:657` 附近注释、`experiments/WORK-566/failure_log.md:584-586` | 「改之前 `idx_all` 是日期序 → `u[pos]` 是散写」**只对 train 切分成立** | 实测：valid（531,580 行）与 test（2,949,469 行）在改动前 `np.lexsort((di,si))` **已是恒等**（面板样本行本身即 (stock,date) 序，见 §4-A2）；只有 train 因 `:641-653` 的每日期 `stock_cap` 抽样变成日期序。差距：注释与 §13.7 的表述未限定，会让人误推 valid/test 也受益 | `../.venv/bin/python -c "import numpy as np; s=np.load('data/WORK-566/samples.npz');st,dt=s['stock_idx'],s['date_idx'];i=np.nonzero(s['m_test'])[0];print(np.array_equal(np.lexsort((dt[i],st[i])),np.arange(len(i))))"` → True |
| N-R2 | 中 | `scripts/work566_train.py:671-672` | 守卫用 `assert`，`python -O` 下会被剥离 → 失效时静默退回散写而不报错 | 预期：硬失败。差距：仅在非常规启动参数下发生（当前用普通 `python` 启动）。建议改 `raise RuntimeError` | `../.venv/bin/python -O -c "import ast;print('assert 被 O 剥离')"` |
| N-R3 | 中 | `scripts/work566_train.py:744-745`、`:760-765` | 「pred 逐位相同 max\|Δ\|=0.000e+00」的说法在**正式规模**下过强 | 我**实测了**生产者的对照产物（`../.w566tmp/u_cmp/OLD_full.npz` vs `data/WORK-566/preds_seed42_full.npz`）：按 (stock,date) 对齐后 y 与 pred 的 max\|Δ\| **= 0.000e+00**（三切分全部）→ **生产者的结论成立**；但那是**冒烟规模**（train 2,675 行）。在**正式规模**（train 1,439,202 行）上，我实测 `np.mean(y)`（float32）新旧行序**逐位不同**：`-0.0021728703` vs `-0.0021728715`（`np.std` 相同，`multiset 相同=True`）→ 聚合标量确有末位差。建议口径改为「per-row 值逐位相同；聚合标量在正式规模下有 ≤1 ulp 差，pred 的传导幅度未实测」 | 见 §7 复现命令汇总 |
| N-R4 | 中 | `scripts/work566_train.py:657`、`:295-302`（tmpfs 复用） | tmpfs 指纹只覆盖「源文件 size:mtime」，**未覆盖「面板内容变了但 size/mtime 未变」** | 实测：指纹文件内容 = `3026591008:1795579072`（size + **秒级** mtime）。若 `feat.npy` 被**原地重写且大小不变、mtime 落在同一秒**（如 `touch -d` 保留时间戳、或两次重建同秒完成），指纹不变 → **静默复用陈旧副本 → 数值污染且从产物上看不出来**。概率低（重建需 ~265s）但**非零**。建议：指纹加内容哈希（如前 1MB 的 sha256）或纳入 `norm.json`/`prior.json` 的 mtime | `cat /dev/shm/w566_feat_train.npy.fp; stat -c '%s %Y' data/WORK-566/feat.npy` |
| N-R5 | 低 | `scripts/work566_train.py:306`、`:326-328` | tmpfs **重建路径**的「内容新、指纹旧」错配窗口 | 实测顺序：`os.replace(tmp, dst)`（`:326`）→ 再写 sidecar（`:327-328`）。若在两步之间被杀 → dst 是**新内容**、sidecar 是**旧指纹** → 下次运行指纹不匹配 → **重建**（安全方向）。反向（内容旧、指纹新）不会出现。**但并行两种子进程**同时启动时：两者都算同一 fp、都写同一个 sidecar 路径（`:327`），且都用 `dst + f".tmp{os.getpid()}"`（`:306`，PID 不同 → 临时名不冲突）→ 最终 dst/sidecar 内容一致，**无竞态**。建议：sidecar 也用 tmp+replace 原子写 | `grep -n "os.replace\|open(side" scripts/work566_train.py` |
| N-R6 | 低 | `scripts/work566_run_stage3.sh:31`、`:34-36`、`:44` | jobfile 用 `>>` **追加**且只在成功路径 `rm`；`rm` 不在 `trap` 里 | 实测当前 `data/WORK-566/bt_jobs.txt` 不存在（上次成功）。但链若在回测中途失败，残留 54 行 → 重跑再追加 → 108 job（54 重复）→ 3 路并行会**同时写同一个** `backtest_*.json` → 半写/竞态。建议写前 `: > "$JOBFILE"` 截断，`rm` 放进 `trap` | `grep -n "JOBFILE" scripts/work566_run_stage3.sh` |
| N-R7 | 低 | `scripts/work566_delivery_check.py:31` | C1 覆盖 16 个「臂×种子」，但 `ARMS` 只有 8 臂 → **fmb 的 2 个文件不查** | 实测：`:31` 的 `ARMS = ("s1_only","s3_only","s1_s2","full","r0","r1","r2","r3")`，`SEEDS=(42,2024)` → 覆盖 16 个；fmb 不在内。故 B4 的缺失**不会被 C1 拦下** | `grep -n "^ARMS\|^SEEDS" scripts/work566_delivery_check.py` |
| N-R8 | 低 | `scripts/work566_run_stage3.sh:12-26` | `[0/7]` 前置检查只验「存在且非空」，不验内容 → **冒烟产物照样放行** | 实测：当前 `data/WORK-566/preds_seed42_full.npz` 的 `pred_test` 只有 **15,276** 行 / **3 个预测日**（冒烟规模；全量应为 2,949,469 行 / 578 日）。当前 `SEEDS="42"` 时会**用 3 天数据跑完 27 次回测并产出无意义指标**（C1 的长度检查要到第 7 步才拦，而前面已把错误数字写进 metrics.json / 报告）。建议前置检查加 `pred_test` 行数下限 | `../.venv/bin/python -c "import numpy as np;z=np.load('data/WORK-566/preds_seed42_full.npz');print(len(z['pred_test']),len(np.unique(z['date_test'])))"` → `15276 3` |
| N-R9 | 低 | `scripts/work566_primary_test.py:186-188` vs `:208-235` | 「种子 < 2 → 未交付」守卫**只加在 H1**，H2/H3/H4 无同等守卫 | 实测：`:186` 的 `if n_seeds < 2` 只在 H1 块内；H2 的 `diffs`（`:210-233`）逐种子 append，**无种子数下限检查**。在 B4 场景（fmb 缺 seed42）下 H2 会用**单种子**出 `pooled_mean_diff_pp` 与 `conclusion`（`:232-233`）而不标注「未交付」。建议给 H2/H3 同样加守卫 | `grep -n "n_seeds < 2" scripts/work566_primary_test.py` → 仅 1 处 |
| N-R10 | 低 | `scripts/work566_report.py:141`、`:394`、`:417`、`:428`、`:467`、`:516`、`:559` | 报告里的 `6.07` / `11.22%` / `5.15%` / `9.94%` / `−31.09%` 等均为**论文值**，非本单结论 → 属可接受的口径标注（论文基准本就该写死） | 实测：H1 判词 `{v}` 来自 `metrics.json` 的 `verdict`（`:268`）、H1 数值来自 `verdict["H1"]`（`:260`）；本单结论性数字（ΔRankIC 中位数、净 SR、审计 blocking 数）全部从产物读（`:391`、`:405-412`）。**未发现把本单结论硬编码进报告**。唯一需注意的是 `:479` 的「H1 的符号在两个种子下{sign_txt}」——`082226f` 已加保护（不足 2 种子 → 「不可判定」）。建议在图注标明「+6.07 pp 为论文 Table 4 值」 | `grep -n "v = metrics.get\|h1 = verdict.get" scripts/work566_report.py` |
| N-R11 | 低 | `scripts/work566_train.py:126-157`（`FastEncoderLayer`） | 编码器等价性自检只覆盖**同一批 layer 对象**的两种前向路径，不覆盖 `SeqEncoder` 外层（summary token、pos、LayerNorm）的等价性 | 实测自检输出（生产日志）：`eval max\|diff\| = 4.768e-07 → PASS`、`train max\|diff\| = 0.000e+00 → PASS`（`experiments/WORK-566/fast_encoder_selftest.json` 已入库 train 段）。**本轮未独立复跑成功**（bench 因训练占满 CPU 而超时，见 §8）→ 结论沿用已入库产物，**未由我独立复现** | 见 §8 边界说明 |

---

## 4. 任务 A/B/C/D 逐项结论

### 任务 A — 验证 B2 修复是否真正正确

**A1 · 越界是否真的消除：是。**
- `u` 的 memmap 行数 = `len(idx_all)`（`scripts/work566_train.py:657-658`，`shape=(len(idx_all), 40+PRIOR_DIM+3*d_model)`）。
- `pos` 的取值范围 = `[0, len(idx_all))`：`pos = perm[k:k+B]`（`:665`），`perm = np.lexsort((di, si))`（`:662`），`di`/`si` 是 `len(idx_all)` 长的 split 内数组 → perm 是该长度上的置换 → pos 恒在界内。
- `idx_all` 的取值范围 = **全局样本下标**（实测 `samples.npz` 共 10,039,167 行；train 掩码下标最大 10,031,596），远大于 u 的行数（train 1,439,202 / valid 531,580 / test 2,949,469）。
- 结论：**旧写法 `u[idx_all[pos]]` 必越界**（`686b921^` 的 `:657`+`:666`），新写法 `u[pos]` 三者一致 → **越界消除**。

**A2 · 行对齐是否正确：是（修复未引入「u 与 y 错行」）。**
逐赋值点核对（`scripts/work566_train.py`）：

| 位置 | 表达式 | 行空间 |
|---|---|---|
| `:654` | `si, di = panel.stock[idx_all], panel.date[idx_all]` | split 内位置 0..N-1 |
| `:655` | `bid = panel.block_id(panel.dts[idx_all])` | 同上 |
| `:657-658` | `u = open_memmap(..., shape=(len(idx_all), ...))` | u 第 r 行 ↔ 样本 `idx_all[r]` |
| `:662` | `perm = np.lexsort((di, si))` | 置换，不改行空间 |
| `:665` | `pos = perm[k:k+B]` | split 内位置 |
| `:666` | `panel.context(si[pos], di[pos])` | 按 pos 取，**与 pos 同序** |
| `:676-677` | `u[pos] = np.concatenate([smean, pr, z_anc, delta, h], axis=1)` | 右侧第 j 行 = `pos[j]` 对应样本 → **u[pos[j]] ↔ 样本 idx_all[pos[j]]** ✓ |
| `:679` | `y = panel.labels(si, di)` | `panel.labels` 是逐行 gather（`:412-414`）→ y[r] ↔ idx_all[r] ✓ |
| `:680-681` | `out[split] = {"idx": idx_all, "y": y, "stock": si, "date": di, "dts": ...}` | 与 u 同序 ✓ |

- **`perm` 的作用**：只决定**读盘顺序**（(stock,date) 主序），**不改行空间**。`np.lexsort` 主键是**最后一个** key → `np.lexsort((di, si))` 主键 = `si`（股票）→ stock-major，与面板布局 (stock, date, feat) 一致 → 顺序读。
- **右侧张量行序与左侧行号一一对应：是**（上表 `u[pos]` 一行）。
- **`y` 行序**：与 `si/di` 同源于排序后的 `idx_all` → 与 u 同序 ✓。
- **`out[split]["idx"]` 与 u**：同源同序 ✓。
- **下游 `train_readout`**：`gidx` 由 `np.argsort(dtr, kind="stable")`（`:780`）按日期分组，组内索引是 **split 内行号**；`take(u_tr, gi)`（`:796`）与 `ytr_t[torch.from_numpy(gi)]`（`:797`）**落在同一套行号空间** ✓。
- **下游 `predict_all_arms`**：`blk = np.asarray(u[sl])`（`:840`）与 `pms[arm][sl] = ...`（`:848`）用同一 `sl` 切片 → 行序一致 ✓。
- **实测交叉验证**：我复刻了生产者 18:16（旧实现）与 18:22（新实现）两次冒烟的产物（`.w566tmp/u_cmp/OLD_full.npz` vs `data/WORK-566/preds_seed42_full.npz`）：**train 切分同序 max\|Δ\| = 7.798e-01 / 3.136e-02，但按 (stock,date) 对齐后 y 与 pred 的 max\|Δ\| 全部 = 0.000e+00**，样本集合相同 → **确认无错行**。
- **结论：修复后没有引入「u 与 y 错行」这类静默数值错误。**

**A3 · train 切分的抽样截断：一致，但有一处需注意。**
- 截断在 `:641-653`：按 `dts` 分组、每日期取前 `stock_cap`（默认 1000）只，**向量化、确定性**。
- 截断后：`u` 行数 = `len(idx_all)`（`:657`）、`y` 行数 = 同（`:679`）、`out["train"]["idx"]` = 同（`:680`）→ **三者一致** ✓。
- 实测：train 截断后 **1,439,202** 行（= 1440 个预测日 × ≤1000），与训练日志 `[18:31:59] seed42 train: 1,439,202 样本` **完全吻合** ✓。
- 与协议一致性：预注册 `experiments/WORK-566/preregistration.md:194`「读出训练每个日期的样本数 | 每个预测日最多 1,000 只股票（按 ts_code 排序确定性取前 1,000）」、`:199` 偏离⑨ 同义 → **实现与协议一致**（截断确实按 ts_code 升序取前 1000：实测每日期组内 stock 升序，`stock_idx` 由 `ts_code` 字典序编码 → `scripts/work566_build_panel.py:134-135` `order by ts_code`）。
- **需注意（非阻塞）**：截断发生在行序重排**之前**（`:641-653` → `:654` 的排序），故被选集合与行序无关 → 行序改动**不改变被选样本集合**（实测「旧截断集合 == 新截断集合 ? True」，1,439,202 == 1,439,202）✓。

**A4 · masking/dropout 的随机流是否受行序改动影响：否。**
- `build_reps` 全程 `enc.eval()` / `proj_ema.eval()` / `qpred.eval()`（`:625-627`）+ `with torch.no_grad()`（`:663`）→ **dropout 全部关闭**、无随机算子。
- `RevisionPredictor` 内含 `nn.Dropout`（`:201`），但 `qpred.eval()` 已关闭它。
- 结论：行序改动**不改变任何随机流消费**，**逐位可复现性不受破坏** ✓。另：读出阶段自带 `torch.manual_seed(seed+7)`（`:690`）与 `default_rng(seed+11)`（`:786`），且在建模前重置 → 读出结果与行序无关 ✓。

### 任务 B — tmpfs 陈旧校验（`686b921` 新增）

**B1 · 指纹逻辑是否覆盖「面板内容变了但 size/mtime 没变」：否（残留风险，须披露）。**
- 指纹 = `f"{st_src.st_size}:{int(st_src.st_mtime)}"`（`:293`），实测内容 `3026591008:1795579072`。
- 复用条件（`:295-298`）：`dst` 存在 **且** sidecar 存在 **且** `arr.shape == shape` **且** sidecar 内容 == 当前指纹。
- **未覆盖**：面板内容变但 size 与 mtime 均不变的情形。`int(st_mtime)` 只取**秒级**、size 恒定 → 若 `feat.npy` 被原地重写且大小不变、mtime 落在同一秒（或被 `touch -d` 保留），指纹不变 → **静默复用陈旧副本 → 数值污染，且从产物上看不出来**。
- 概率评估：重建耗时约 265s（failure_log §12.2），跨秒 → 实际触发概率**低但非零**；建议把「源文件首 1MB 的 sha256」或 `norm.json`/`prior.json` 的 mtime 并入指纹。
- **正面结论**：shape 与「起日」确实被校验（`:298` 的 `tuple(arr.shape) == shape` + `:299` 的 `self.stg_lo = lo`），面板重建导致 shape 变化会被拦下 ✓。

**B2 · 复用路径与重建路径是否有竞态：实测无导致错误的竞态。**
- 两种子进程同时启动：都算出**同一** fp、都判「dst 缺失」→ 都走重建 → 各自写 `dst + f".tmp{os.getpid()}"`（`:306`，**PID 不同 → 临时名不冲突**）→ `os.replace(tmp, dst)`（`:326`，原子）→ 最后写入者胜；两侧内容逐位相同（同源同参数）→ **结果一致**。
- 一方在重建、另一方判复用：复用方读到的是**旧 dst**（`os.replace` 前）→ 但它**随后会因指纹不匹配而重建**；若复用方在 replace 后读到新 dst → 内容正确。**无论哪种交错，最终 dst 内容与 sidecar 指纹都自洽**（因为内容与指纹由同一进程在同一顺序写出）。
- 结论：**未发现产生错误数值的竞态**（残留风险见 N-R5）。

**B3 · `os.replace` 与 sidecar 写入顺序是否留下「内容新、指纹旧」错配：是，但方向安全。**
- 顺序为 `os.replace(tmp, dst)`（`:326`）→ 再写 sidecar（`:327-328`）。两步之间被杀 → dst 新内容 + sidecar 旧指纹 → **下次运行指纹不匹配 → 重建**（`failure_log.md:302` 的日志会打「陈旧（shape/指纹不匹配）→ 重建」）→ **安全方向，不产生错误数值** ✓。
- 反向错配（内容旧、指纹新）**不会出现**（sidecar 总是后写）。
- 建议：sidecar 也用 tmp + `os.replace` 原子写（当前直接 `open(side,"w")`，进程被杀可能留下**半截指纹** → 触发无谓重建，安全但浪费）。

### 任务 C — 第三轮增量（`git log 1774bb6..686b921` 与 `75193d4`、`ad86424`）

**C1 · `686b921` 的三处改动：逐行看过，判定如下。**
- `build_reps` 写回 `u[pos]`（`:676`）：**正确**（见 A2）。
- tmpfs 指纹校验（`:289-302`, `:326-328`）：**有效但不完整**（见 B1/N-R4/B3）。
- `fast_encoder_selftest.json` 新增 train 段（train_max_abs_diff = 0.0）：与代码一致（`:597-609` 的 train 模式对比逻辑在位），**本轮未独立复跑**（§8）。

**C2 · `75193d4` 的三处改动：逐行看过。**
- `idx_all` 改按 (stock,date) 排序（`:654` 附近）：`np.lexsort((panel.date[idx_all], panel.stock[idx_all]))` → 主键 = 最后一个 key = `panel.stock` → 排成 (stock,date) ✓ **与注释意图一致**。
- `assert np.array_equal(perm, np.arange(len(perm)))`（`:671-672`）：**实测在所有参数组合下 perm 均为恒等**（正式参数 train/valid/test + 冒烟参数 `--rep-date-cap 3` 六种组合全部 True；`(stock,date)` 键唯一 → 排序后 perm 必为恒等）→ **守卫不是空网，不会误报** ✓。但 `assert` 可被 `-O` 剥离（N-R2）。
- `--resume-rep`（`:884-885`, `:947-952`）：**见 B5（BLOCKING）**。

**C3 · `ad86424` 的两处改动（`2d431ba` + `ad86424`）：**
- `[0/7]` 前置检查（`scripts/work566_run_stage3.sh:12-26`）：**漏检 fmb** → B4。
- jobfile 从 `/tmp` 迁到 `data/WORK-566/`（`:31`）：合理（`/tmp` 配额实测已满），但引入了追加写残留风险（N-R6）。
- failure_log §13.8（读出阶段散列读实测）：只写入文档，不改代码，**无口径影响**。

**C4 · 独立复跑编码器等价性自检：未完成（边界，非结论）。**
- 我发起了 `../.venv/bin/python scripts/work566_train.py --bench --bench-samples 64 --threads 2`（只读 bench 模式），但**正式训练（PID 3141343，6 线程，占 ~472% CPU）把本机 8 vCPU 占满**，该 bench 在后台**持续 40+ 分钟未返回**，被我中止 → **本轮未能独立复现 bench 数值**。
- 已入库产物 `experiments/WORK-566/fast_encoder_selftest.json`（eval 4.768e-07 / train 0.0）**沿用生产者的**，未由我独立复现 → 记为未验证（N-R11）。

### 任务 D — Stage 3 链路的前瞻性体检

**D1 · `audit/recompute_work566.py` 是否真的独立重写了复算逻辑：是（实测确认未偷用建模函数）。**
- import 清单实测：仅 `argparse/json/os/sys/time/numpy`（`:15-21`）+ 函数内 `import duckdb`（`:85`）→ **无任何建模脚本 import** ✓。
- 文件头 `:5-6` 自述与实测一致；`grep "work566_train|work566_backtest|scripts\."` 仅命中 `:5` 的注释行。
- 组合模拟是**独立重写**：审计侧函数集 `avg_rank/spearman/max_drawdown/sharpe/calendar/recompute_portfolio`（`:36-265`）与建模侧 `Panel/tradable_at_*/assert_fill_legal/run_backtest/summarize/rank_ic`（`scripts/work566_backtest.py:46-395`）**无重名、无共用** → 满足预注册硬约束 ✓。
- 日历独立重取（`:84-92` 直接读 quantdb parquet `trade_cal`），不用建模侧 `meta.json` ✓。

**D2 · 独立复算的通过判据是否在跑数之前就写死：是（实测 git 时序）。**
- 判据 `TOL = 1e-6`（`:28`）与判定逻辑（`:296-302`：`diff <= 1e-12` → 逐位一致；`rel <= TOL` → PASS；否则 BLOCKING 且「以复算值为准」）**在首次提交 `b49e9c9`（2026-11-25 11:54:19）就已存在**（实测 `git show b49e9c9:audit/recompute_work566.py` 命中 `:8` 的自述与 `:28` 的 `TOL = 1e-6`、`:273-276` 的判定分支）。
- 首个回测产物 commit `a3d8b88` 时间 = **12:51:54** → **判据 commit 早于产物 57 分钟** ✓。
- 预注册 v1.0 commit `16b24fb` = 11:15:50、v1.1 commit `7580a3d` = 12:37:24 → 均早于首个回测产物 ✓（与 `delivery_check.py` 的 C8 一致）。

**D3 · `work566_primary_test.py` 的 H1 判词阈值：符合（逐条核对 `:186-203`）。**
- `n_seeds < 2` → 「未交付（有效种子 < 2）」✓（`:186-188`）。
- `all(d <= 1.0 for d in deltas)` → 「未复现」（预注册失败判据 3，`:192-194`）。
- `delta_median_pp >= 3.0` → 「三段式增量复现成立」✓（`:195-197`）。
- `delta_median_pp <= 1.0` → 「未复现」✓（`:198-200`，含负值）。
- 其余 → 「部分复现」✓（`:201-203`）。
- **限定**：该「种子 < 2 → 未交付」守卫**只加在 H1**；H2/H3/H4 无同等守卫（N-R9）——在 B4 场景下会被单种子结果绕过。

**D4 · `work566_report.py` / `work566_render.py` 有无硬编码的结论性数字：未发现本单结论被硬编码（论文基准值属正常标注）。**
- 报告的 H1 判词与全部本单指标均从产物读：`verdict.json`（`:129`,`:260`）、`metrics.json`（`:235-236`,`:268`）、`audit_findings.json`（`:245-248`）。
- 写死的 `6.07`（`:141`,`:394`,`:417`）、`11.22%`/`5.15%`/`9.94%`（`:428`,`:467`）、`−31.09%/−21.46%`（`:516`）、`2,949,469 vs 2,965,433`（`:559`）均为**论文 Table 4/Table 1 值或样本数事实**，非本单结论 → 属可接受的口径标注（N-R10）。
- `work566_render.py` 全文只做 Chrome 渲染与 PDF 首页抽取，**无任何数值** ✓。

**D5 · `scripts/work566_run_stage3.sh` 的可执行性：不可执行（B4）。**
- 链首 `[0/7]` 会放行 → 回测阶段 fmb 崩 → 因 xargs 掩盖，链**继续**并产出**基于缺失 fmb 的 H2**（N-R9）→ 交付一份口径不自洽的报告。建议在链首直接恢复/重产 fmb 并把 fmb 纳入前置检查。

---

## 5. 已关闭的历史问题（逐条判定）

| 编号 | 判定 | 证据 |
|---|---|---|
| **B1**（前视泄漏：估值用未来价） | **已关闭** | 审计侧插桩实测 `fallback_count=188`、`future_bar_used=0`（failure_log §10）；本轮未重跑该插桩，但代码侧估值改为「估值日之前最后一个可得 bar」（`scripts/work566_backtest.py:67-77`, `:174-176`），`audit/recompute_work566.py:114-120` 独立实现同口径 → 未见回归 |
| **N1**（卖端与清算补计成本） | **已关闭** | 审计侧 `recompute_portfolio` 含 `traded`/成本扣减并逐项比对（`audit/recompute_work566.py:95-265`）；`delivery_check.py` C3 校验 `blocking == 0` |
| **N2**（dropout 位置/次数与官方不等价） | **已关闭** | `FastEncoderLayer.forward`（`scripts/work566_train.py:123-157`）按官方 4 次 dropout 的顺序与形状复刻；已入库自检 train 段 max\|diff\| = 0.0（**本轮未独立复跑**，见 C4/N-R11） |
| **N5**（读出推理补 `clamp(-8,8)`） | **已关闭** | `scripts/work566_train.py:772`（训练侧）与 `:846`（推理侧）成对实现；`preregistration_v1.1.md` §2 文本一致 |
| **N6**（加「种子 < 2 → 未交付」与失败判据 3） | **部分关闭** | H1 已加（`scripts/work566_primary_test.py:186-188`, `:192-194`）；**H2/H3/H4 未加**（N-R9） |
| **N7**（NAV ≤ 0 守卫） | **已关闭** | 审计侧 `max_drawdown`（`audit/recompute_work566.py:61-70`）与建模侧 `summarize` 均在 NAV ≤ 0 时给出有限值；本轮未见回归 |
| **N9**（断言改为由引擎自身守卫触发） | **已关闭** | `scripts/work566_backtest.py:105-134` 的 `assert_fill_legal` 在引擎内触发，`experiments/WORK-566/assert_violation_log.txt` 已入库（1,897 B） |
| **B2**（`u` 写回越界 IndexError） | **已关闭** | 修复 `u[pos]`（`scripts/work566_train.py:676`）；越界消除（A1）+ 行对齐正确（A2）+ 独立复现「对齐后 max\|Δ\| = 0」（A2 交叉验证） |

---

## 6. 对最终报告「已知限制」的披露建议

以下残留风险**必须**写进最终报告的「已知限制」章节：

1. **表征 checkpoint 的选取与复用口径**（B3 + B5）：落盘的 `ckpt_seed{S}_rep.pt` 是**周期保存快照**（实测 `upd=1000`，训练实到 4000），且 `best[1]` 因**共享存储别名**会被后续 epoch 改写 → 交付的表征**不等于**预注册 `:245` 选定的「验证损失最小」权重。报告必须写明：实际使用的 upd 数、该 ckpt 的来源、以及「表征选型未按协议执行」这一偏离；**若无法重跑，必须把本单结论标记为「在未按协议选型的表征上得到」**。
2. **tmpfs 指纹的覆盖盲区**（N-R4）：指纹只含 size + 秒级 mtime，**不覆盖「内容变但 size/mtime 未变」** → 面板重建若命中该窗口，将静默复用陈旧副本。披露内容：指纹口径、触发条件、为何概率低但非零。
3. **行序改动带来的末位浮点差**（N-R3）：per-row 值逐位相同（已实测），但**聚合标量**（`mu`/`sd`、`mu_u`/`sd_u`）是顺序相关归约 → 正式规模下 `np.mean(y)` 实测相差 1 ulp（`-0.0021728703` vs `-0.0021728715`）。披露应写「per-row 逐位相同；聚合标量与 pred 的末位差异在正式规模下未逐位验证」。
4. **散写问题只存在于 train 切分**（N-R1）：valid/test 在改动前 perm 已是恒等 → 报告中不应笼统写「build_reps 全切分散写」。
5. **H2 的单种子风险**（N-R9 + B4）：若 fmb 未恢复，H2 会以单种子出结论 → 必须披露或修好后再跑。
6. **冒烟产物冒充全量的风险**（N-R8）：当前 `data/WORK-566/preds_seed42_*.npz` 是 3 预测日的冒烟产物（15,276 行），全量应为 2,949,469 行 / 578 日 → 跑 Stage 3 前必须确认已被全量覆盖。
7. **本轮未独立复现的项**（C4）：`--bench` 与编码器 train 模式等价性自检因训练占满 CPU 未跑完 → 报告中该证据沿用生产者的 `fast_encoder_selftest.json`，应标注「第三轮审查未独立复现」。

---

## 7. 复现命令汇总（全部只读）

```bash
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir/quant-lab
PY=../.venv/bin/python

# A1/A2 行空间与越界
$PY - <<'PY'
import numpy as np
s=np.load("data/WORK-566/samples.npz"); st,dt=s["stock_idx"],s["date_idx"]
for nm in ("m_train","m_valid","m_test"):
    i=np.nonzero(s[nm])[0]
    print(nm, len(i), "全局下标max", i.max(), "perm 恒等?", np.array_equal(np.lexsort((dt[i],st[i])), np.arange(len(i))))
PY

# A2 逐位对照（生产者旧/新实现产物）
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir && ./.venv/bin/python - <<'PY'
import numpy as np
old=np.load(".w566tmp/u_cmp/OLD_full.npz"); new=np.load("quant-lab/data/WORK-566/preds_seed42_full.npz")
for sp in ("train","valid","test"):
    so,dn=old[f"stock_{sp}"],old[f"date_{sp}"]; sn,dn2=new[f"stock_{sp}"],new[f"date_{sp}"]
    mo={int(k):i for i,k in enumerate(so.astype(np.int64)*100000+dn)}
    pn=np.array([mo[int(k)] for k in sn.astype(np.int64)*100000+dn2])
    print(sp, "对齐后 y max|d|", float(np.abs(old[f"y_{sp}"][pn]-new[f"y_{sp}"]).max()),
              "pred max|d|", float(np.abs(old[f"pred_{sp}"][pn]-new[f"pred_{sp}"]).max()))
PY

# B1 tmpfs 指纹
cat /dev/shm/w566_feat_train.npy.fp; stat -c 'feat.npy size=%s mtime=%Y' quant-lab/data/WORK-566/feat.npy

# B3 checkpoint 别名
cd quant-lab && $PY - <<'PY'
import torch
st=torch.load("data/WORK-566/ckpt_seed42_rep.pt",map_location="cpu",weights_only=False)
print("upd:",st["upd"],"（周期保存点）键:",sorted(st))
PY

# B4 fmb 缺失 + xargs 掩盖
ls -la quant-lab/data/WORK-566/preds_seed42_fmb.npz || echo "MISS（已回收至 Trash）"
$PY quant-lab/scripts/work566_backtest.py --arm fmb --seed 42 --cost 0.0005 --out /tmp/x.json 2>&1 | tail -2
cd /srv/data/multica/workspaces/workspace-c8faa36a018a/work-566-e0a29716dad1/workdir/.w566review3
printf 'a\nb\n' > jf.txt && xargs -P 1 -n 1 bash -c 'false; echo "  完成 arm=$1"' _ < jf.txt; echo "xargs EXIT=$?"

# D2 判据先写死
cd ../../quant-lab && git show b49e9c9:audit/recompute_work566.py | grep -n "TOL = 1e-6"
git log --format='%h %ad' --date=iso -1 b49e9c9; git log --format='%h %ad' --date=iso -1 a3d8b88

# N-R8 冒烟产物规模
$PY -c "import numpy as np;z=np.load('data/WORK-566/preds_seed42_full.npz');print(len(z['pred_test']),len(np.unique(z['date_test'])))"
```

---

## 8. 本轮未验证 / 边界（必须写进报告）

1. **未实跑训练、回测、Stage 3 全链**（按指令，训练在跑）→ B3 的**数值影响**（正式 seed42 的逐 epoch 验证损失序列）**未实测**。
2. **未取到全量训练的逐 epoch 验证损失日志**：`.w566tmp/w566_s42.log`（18:31 版）只含 `--resume-rep` 那一轮；上一轮的完整日志已被回收站清理 → **「最后一轮是否恰好就是验证最优」无法用数值判定**，B3 的判定基于**代码语义 + 独立最小复现**（别名机制已实测）。
3. **`--bench` 编码器等价性自检未独立复现**（训练占满 8 vCPU，bench 40+ 分钟未返回，已中止）→ N-R11 的结论沿用已入库产物。
4. **未做旧/新实现在正式规模上的读出训练对照实跑**（成本过高）→ N-R3 的「pred 末位差幅度」未实测；我只实测了**聚合标量**层面的 1 ulp 差。
5. **fmb 产物未恢复、未重跑回测**（只读约束）→ B4 的「链继续并产出不自洽 H2」是**静态 + 最小复现**的推断（xargs 掩盖行为已用等价脚本实测），未实跑 Stage 3 链。

---

## 附录 A · 同路径先前的第 3 轮报告（另一审查子代理 `bg_c68840f6`）结论对照

原文备份于 `../.w566review3/prior_round3_other_reviewer.md`（18,460 B）。其结论与我的一致/不一致之处：

| 项 | 对方结论 | 我的结论 | 说明 |
|---|---|---|---|
| B2 修复 | 成立（Q1） | **一致**（A1/A2） | 双方均实测越界消除 + 行对齐正确 |
| B3 checkpoint 别名 | BLOCKING | **一致**（我独立最小复现了别名机制） | 对方补了「共享存储视图」与「m_k 同对象」两点实测；我补了「磁盘 ckpt 的 upd=1000 vs 训练实到 4000」这一**磁盘级证据**，把 B3 从「代码语义风险」升级为「当前正在被使用的产物确实不合协议」 |
| B4 fmb 漏检 | BLOCKING，断言**链会中止**（xargs 退出 123 → set -e 触发） | **部分一致**：fmb 缺失属实且 BLOCKING；但**失效机制相反** —— 我实测 `xargs -P ... bash -c 'false; echo ...'` 的整体退出码是 **0**（`echo` 掩盖了失败），`set -euo pipefail` **不会**中止链 → 后果是**链继续跑并产出基于缺失 fmb 的 H2**，比「崩链」更隐蔽 |
| 行序改动 | 成立，但「pred 逐位相同」口径偏强（N14） | **一致并加强**（N-R3） | 对方未实测幅度；我实测了生产者的旧/新对照产物（对齐后 max\|Δ\| = 0 ✓）**且**在正式规模上实测到聚合标量 1 ulp 差 → 给出可披露的定量结论 |
| N13（散写只影响 train） | 已提出 | **一致**（N-R1） | 我独立实测 valid/test 在改动前 perm 已是恒等 |
| 其余 N10/N11/N12/N15/N16 | 已提出 | 部分对应我的 N-R6/N-R7/N-R8/N-R5/N-R2 | 我的清单另含 N-R4（指纹盲区）、N-R9（H2 无种子守卫）、N-R10（报告硬编码核查） |
| 新增（本报告） | — | **B5**（`--resume-rep` 无完成度/配置校验，当前正消费 `upd=1000` 半成品）、N-R8（冒烟产物冒充全量）、N-R9（H2 单种子无守卫） | — |

**冲突点处理**：B4 的失效机制双方结论不同 → 两边都摆出。对方的「链会中止」基于 xargs 退出码 123 的推理；我的「链不会中止」基于**等价脚本实测**（`xargs EXIT=0`）。以实测为准，并建议两种防护都加（内层 `exec`/显式 `|| exit` + 前置检查纳入 fmb）。

---

## 9. 审查期间生产者的并行整改（2026-11-25 18:50–18:55 观察到的未提交改动）

在我完成本报告写入的同时，生产者已在工作树中落地了针对 **B3 / B4 / B5 / N-R6** 的整改（**尚未 commit**，故 `git diff` 可见）。逐条核对：

| 我的判定 | 整改位置（未提交 diff） | 覆盖情况 |
|---|---|---|
| **B3**（checkpoint 活引用 + 末尾覆盖保存） | `scripts/work566_train.py`：新增 `_clone_sd()` / `snapshot_state()`（深拷贝 `enc/tenc_ema/proj/proj_ema/qpred` + `m_k.detach().clone()` + `v_k.detach().clone()`，并写入 `vloss`/`selected="best_vloss"` 标记）；`:best` 构造处改用 `snapshot_state`；周期保存改写到**独立路径** `ckpt_path + ".periodic"`（不再覆盖选型 ckpt）；末尾 `torch.save(best[1], ckpt_path)` 后新增**回读自检**（校验 `selected`/`vloss`/`upd`）；新增启动自检 `snapshot_selftest()` | **完全覆盖**（含我建议的深拷贝、删除覆盖保存、独立周期路径三项）。注意：`selected`/`vloss` 标记是**新增**字段，`build_reps` 侧无需改动即可读取旧格式 |
| **B5**（`--resume-rep` 无完成度/配置校验） | `scripts/work566_train.py` 的 `--resume-rep` 分支：复用前 `torch.load` 并校验 `chk.get("selected") == "best_vloss"`，不满足则 `raise SystemExit`（明确提示「该文件由修复前的路径产生，需删除后完整重训」）；日志补充打印 vloss/upd | **覆盖**。但**配置指纹**（d_model/layers/heads/ff/epochs/max_updates/val_stride）**仍未校验** → 若同路径下换了模型规模仍会放行，属我 B5 建议的**剩余部分**（建议补齐，不阻塞） |
| **B4**（fmb 漏检 + xargs 掩盖） | `scripts/work566_run_stage3.sh`：新增独立的前置块，逐一检查 `preds_seed${s}_fmb.npz`，缺失则**自动调 `work566_baselines.py --seed $s` 补齐**，补齐失败 `exit 4`；`scripts/work566_delivery_check.py:31` 的 `ARMS` 已加入 `"fmb"` | **前置检查已覆盖 fmb**（我的第②③项建议）；**xargs 失败掩盖（第②项的机制部分）仍未修**——内层 `bash -c` 仍以 `echo` 收尾（`:58-60`），失败仍会返回 0 → 若将来有**其他**产物缺失，链仍会继续跑完并产出不自洽报告。建议改为 `exec "$PY" ...` 或在 `echo` 后加 `\|\| exit 1` |
| **N-R6**（jobfile 追加写） | `scripts/work566_run_stage3.sh`：`JOBFILE` 写入前加 `: > "$JOBFILE"` 截断 | **覆盖** |
| **N-R1 / N-R2 / N-R3 / N-R4 / N-R5 / N-R7 / N-R9 / N-R10 / N-R11** | 未见对应改动 | 仍开放（严重度均为中/低，见 §3） |

**两个仍未关闭的实机风险（需在跑 Stage 3 前确认）**：

1. **当前正在跑的正式训练（PID 3241771，`--seeds 42 --threads 6 --rep-batch 1024`，**无 `--resume-rep`**）已在 18:54 重启** → 它会**完整重训** 4000 upd 并用新的 `snapshot_state` 落盘 → 这正是 B3 修复所需的「重跑表征与全部下游」。**这一进程是必要的，不应中断**。
2. **旧格式 ckpt 与新守卫的冲突**：修复前的 `ckpt_seed42_rep.pt`（无 `selected` 标记）若还在，新 `--resume-rep` 会**主动拒绝复用并报错**（设计正确）→ 任何续跑都必须**完整重训**，不能复用旧文件。
3. **`preds_seed42_*.npz` 当前的冒烟产物**（15,276 行 / 3 预测日，见 N-R8）会被新训练进程覆盖为全量 → 跑 Stage 3 前应再确认 `pred_test` 长度为 2,949,469。

**对本报告结论的影响**：B3 / B4 / B5 的**整改方向与我的建议一致且已落地**（B3、B5 完整；B4 的前置检查完整、失败传播机制未修）。因此：

- 若以**修复后**的代码重新验收，B3/B5 可判**已关闭**；
- B4 可判**部分关闭**（前置检查已覆盖 fmb；但 xargs 失败掩盖机制仍在，且 H2 仍缺「种子 < 2 → 未交付」守卫 → N-R9）。
- **本轮判决维持 FAIL**，依据是：① 上述整改**尚未 commit**，且**本轮审查未在整改后的代码上重跑任何验证**（训练占满 CPU）；② B4 的失败传播机制与 N-R9 的 H2 单种子守卫仍开放；③ N-R1/N-R3/N-R4 三项中等严重度的**披露口径问题**仍开放 —— 它们直接决定最终报告「已知限制」怎么写。
