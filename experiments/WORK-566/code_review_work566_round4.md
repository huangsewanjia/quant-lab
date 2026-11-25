# WORK-566 第 4 轮独立代码审查（verifier 角色 · 只读复审第 3 轮整改）

> 复审对象：`code_review_work566_round3.md` 判 FAIL 的 3 项 BLOCKING + 11 项 NON-BLOCKING 整改
> 审查者：独立 verifier（未编写任何被审代码）　审查时点：2026-11-25 19:28–19:50
> 硬约束遵守：未运行 `work566_train.py / backtest / primary_test / baselines / report / render / recompute`；
> 未 import torch（避免与在训的 seed42 进程争内存）；仅用只读 grep/sed/read + 纯 Python 小片段（<5 s、<100 MB）；
> 磁盘 checkpoint 用 `zipfile+pickle` 只读解析（不写盘）。

---

## 1. 判决

**通过** —— 第 3 轮的 3 项 BLOCKING（B3 / B4 / B5）**均已真正修好**，不是装饰性整改；
11 项 NON-BLOCKING 中 10 项确实落地，1 项（N-R11）**代码侧未改、披露侧只在 failure_log 兑现、报告侧未兑现**（见 NB-B）。
本轮**未发现新的 BLOCKING**；新发现 11 项 NON-BLOCKING（均为披露口径 / 边界加固 / 一致性问题，不影响数值正确性）。

核心证据：磁盘上正在被训练进程写入的两个 checkpoint 已经是新格式且语义正确 ——
`ckpt_seed42_rep.pt` = `upd=1000, vloss=115.03603349268953, selected='best_vloss', complete=True`（= 日志里 epoch0 的验证损失 115.0360），
`ckpt_seed42_rep.pt.periodic` = `upd=1600, vloss=inf, selected='periodic', complete=False`（独立路径，未覆盖选型文件）。

---

## 2. 整改核对表

| 编号 | 原严重度 | 修法位置 file:line | 真的修好？ | 证据 |
|---|---|---|---|---|
| B3 | BLOCKING | `scripts/work566_train.py:238-240`（`_clone_sd`）、`:243-269`（`snapshot_state`）、`:611-614`（周期快照独立路径）、`:625-629`（选型时深拷贝快照）、`:630-640`（末尾保存 + 回读自检）、`:272-297`（`snapshot_selftest` 红绿自检） | **成立** | 每个张量都 `detach().clone()`（:240），`m_k/v_k` 单独 clone（:262-263）；`build_reps` 从**磁盘**读该文件（:718-728 用 `st["enc"]/st["proj_ema"]/st["qpred"]/st["m_k"]/st["v_k"]`）→ 修复不是装饰。磁盘实测：选型文件 `upd=1000/vloss=115.0360/selected=best_vloss`，周期文件独立为 `.periodic`（`upd=1600/selected=periodic`） |
| B4 | BLOCKING | `scripts/work566_run_stage3.sh:50-64`（fmb 缺失自动补齐 + 失败 exit 4）、`:84-91`（内层 `exit 1`）、`:95-108`（54 份齐备性复核）；`scripts/work566_delivery_check.py:31`（ARMS 纳入 fmb） | **成立** | 等价 shell 片段实测：旧写法 `bash -c 'false; echo …'` → xargs 退出码 **0**、链继续；新写法 `if ! cmd; then exit 1; fi` → xargs 退出码 **123**、`set -euo pipefail` 断链（`AFTER-XARGS` 未打印）。fmb 产物当前确实缺失（`data/WORK-566/` 只有 `preds_seed2024_fmb.npz`），链会自动重生成 |
| B5 | BLOCKING | `scripts/work566_train.py:1056-1075`（`--resume-rep` 三重校验）、`:546-548` 与 `:1067-1069`（cfg 指纹） | **成立** | 用源码 AST 抽出 `:1056-1077` 实块 + 合成 checkpoint 逐例执行：周期快照→拒绝；修复前旧 ckpt（无 selected/complete/cfg）→拒绝；cfg 不一致→拒绝（`{'epochs': (8, 4)}`）；`complete=False`/缺失/`complete=1`（非 bool）→拒绝；新格式且 cfg 一致→放行。**例外**：cfg 缺失时放行（fail-open，见 NB-D） |
| N-R1 | 中 | `scripts/work566_train.py:760-762`（注释限定「只有 train 因…才出现散写」）+ `failure_log.md` §14.6 | **成立** | 注释已改为「valid/test 的 `perm` 改前就已经是恒等置换；只有 train…」 |
| N-R2 | 中 | `scripts/work566_train.py:777-779` | **成立** | `raise RuntimeError("perm 应为恒等置换…")` 已替换 `assert`。但**同类问题在新增代码里又出现一次**（见 NB-C） |
| N-R3 | 中 | `scripts/work566_train.py:763-766`（注释）+ `failure_log.md:745` | **成立** | 注释已限定为「per-row 层面成立（max\|Δ\|=0.000e+00）；聚合标量…≤1 ulp 末位差」 |
| N-R4 | 中 | `scripts/work566_train.py:300-314`（`_content_hash`）、`:374`（指纹 = size:mtime:hash）、`:368-371`（注释） | **部分成立** | 哈希偏移正确（首 4MB + 末 4MB，`:309-313` 用 `fstat` 取真实 size、`seek(size-chunk)`），代价 ~8MB 读；不会造成无尽重建（sidecar 写的是**源文件**指纹 `:374`，下次同源同指纹即命中复用）。**但只哈希首末 8MB**：3GB 面板中段被改写而首末不变时仍检测不到（见 NB-E 同类，见 §4-Q8） |
| N-R5 | 低 | `scripts/work566_train.py:409-414`（sidecar 走 tmp + fsync + `os.replace`） | **成立** | 临时名带 PID（`:409`）→ 两进程各自 tmp 不冲突；`os.replace` 原子替换（`:414`）。dst 本身也是 tmp+replace（`:400-407`），顺序为「先 dst 后 sidecar」→ 中途被杀落在**安全方向**（下次指纹不匹配→重建） |
| N-R6 | 低 | `scripts/work566_run_stage3.sh:73`（`: > "$JOBFILE"`） | **成立** | 写前截断已加；`:92` 的 `rm` 仍未进 `trap`，但因截断存在，重跑不会翻倍 |
| N-R7 | 低 | `scripts/work566_delivery_check.py:31` | **成立** | `ARMS = ("s1_only","s3_only","s1_s2","full","r0","r1","r2","r3","fmb")`，`SEEDS=(42,2024)` → 覆盖 18 个臂×种子；C1 名称已改「18 个臂×种子…（含 FMB 基线臂）」（:83） |
| N-R8 | 低 | `scripts/work566_run_stage3.sh:25-48`（pred_test 行数校验） | **成立（fmb 臂除外）** | 基准取自 `experiments/WORK-566/sample_stats.json` 的 `n_test=2,949,469`（该文件由 `scripts/work566_prior.py:315-327` 产出，prior.py 的日期区间是硬编码全量 `:44-49`，**不会**被冒烟开关污染 → 校验非自指）；冒烟产物（15,276 行）会被 `exit 6` 拦下。fmb 未纳入内容校验（见 NB-H） |
| N-R9 | 低 | `scripts/work566_primary_test.py:236-241`（H2）、`:259-262`（H3 逐臂）、`:294-295`（H4）；披露 `scripts/work566_report.py:428-432` | **部分成立** | 守卫已加且不会崩：H2 种子<2 → `conclusion` 改写为「未交付」+ `delivered=False`；H3 逐臂 `delivered`；H4 `delivered=all(v>=2)`；报告 :428-432 披露三者状态。**残留**：H3 的「净 SR<0 → 直接判退」判据未受 `delivered` 约束（`:276-278`），单种子仍可改写顶层判词（见 NB-G） |
| N-R10 | 低 | `scripts/work566_report.py:76,141,394,417,472,493,521` | **成立** | 这些硬编码值全是**论文值**（5.15/9.94/11.22/6.07/−31.09/−21.46）；本单结论全部来自 `verdict.json`/`metrics.json`（`:129,:260-263` 读取，`:417` 只渲染 `{v}`）。未发现把本单结论写死进报告 |
| N-R11 | 低 | `experiments/WORK-566/failure_log.md:753` | **部分成立** | 代码未改（自检范围本就是 layer 级）；`failure_log.md:753` 声明「自检范围如实写入报告与本日志」，但**报告里没有这句限定**（`report.py` 全文 grep「外层 / SeqEncoder / summary token / LayerNorm / layer 级」0 命中）→ 见 NB-B |

---

## 3. 新发现

### BLOCKING
无。

### NON-BLOCKING（按严重度）

| 编号 | file:line | 为什么是问题 | 建议修法 |
|---|---|---|---|
| NB-B（中） | `scripts/work566_report.py:570-573` | N-R11 的披露承诺未在报告兑现：报告只写「自检逐元素最大差 eval 4.77e-07 / train 0.00e+00（逐位一致）」，读者会当成 S2 编码器**端到端**等价；实际自检只覆盖 encoder layer 的两条前向路径，不覆盖 `SeqEncoder` 外层（summary token / pos / LayerNorm） | 在 :572 后补一句范围限定：「该自检只比较 encoder layer 的两条前向路径，不覆盖 `SeqEncoder` 外层（summary token / pos / LayerNorm）」 |
| NB-A（中） | `scripts/work566_report.py:579-580` | 报告自述与实现不符：仍写 tmpfs 陈旧校验是「源文件 size/mtime 指纹」，而 `train.py:374` 已改成 size:mtime:**内容哈希**（N-R4）→ 报告的「已知限制/偏离」表落后于代码 | 改为「size:mtime:sha256(首末各 4MB)」 |
| NB-C（中） | `scripts/work566_train.py:634-638` | **新加的**落盘回读自检用 `assert`，`python -O` 下被剥离 → B3 的自检形同虚设。同文件 `:287-296` 的 `snapshot_selftest` 已改用 `raise SystemExit`，说明该规则作者已知，只是新代码没照做 | 4 条 `assert` 改 `raise RuntimeError` |
| NB-D（中） | `scripts/work566_train.py:1072` | `if old_cfg and diff:` 是 fail-open：cfg 缺失/为空 → 直接放行（合成用例实测「允许复用」）。与「配置指纹必须一致」的声明不符（当前 trainer 必写 cfg，线上不可达） | 改为 `if not old_cfg or diff:` 即拒绝 |
| NB-E（低） | `scripts/work566_train.py:546-548`、`:1067-1069` | 指纹未覆盖**数据快照**（feat.npy / prior.json / norm.json / samples.npz 重建后仍复用旧表征），也未覆盖 `--threads` / `--val-batch`（改变验证损失的浮点归约顺序，可能翻转「最优 epoch」选择）；另 `_content_hash` 只覆盖首末 8MB（`:300-314`），中段改写检测不到 | 指纹加面板/样本快照 sha256；补 `--threads/--val-batch`；或改全文件哈希（3GB，一次 ~3–10 s） |
| NB-F（低） | `scripts/work566_train.py:629`、`:631` | 选型 checkpoint 直接 `torch.save` 到 `ckpt_path`，**非原子**（周期快照反而用了 tmp+replace，`:613-614`）。训练在最终写的中途被杀 → 文件截断 | 选型保存也走 tmp + `os.replace` |
| NB-G（低） | `scripts/work566_primary_test.py:276-278`、`:236-241`；`scripts/work566_report.py:397-398,420-421,499-500` | N-R9「同口径」未完全落实：H3 只要 full 臂平均净 SR<0 就写顶层 `verdict`，即使 `h3["delivered"]["full"]` 为 False；H2 单种子时 `pooled_mean_diff_pp` 仍留在 metrics 并被报告渲染（只靠 `conclusion` 文案标注「未交付」） | H3 判退要求 `delivered` 为真，或给结论加「（单种子）」后缀 |
| NB-H（低） | `scripts/work566_run_stage3.sh:36` vs `:53-63` | 前置**内容**校验只覆盖 8 个训练臂，fmb 只查存在 → 若 fmb 是冒烟规模，直到第 8 步 C1（`delivery_check.py:66-88`）才拦 | 把 `fmb` 纳入 `:36` 的臂列表 |
| NB-I（低） | `scripts/work566_run_stage3.sh:105` | 54 份回测产物只验「存在且非空」，不验 JSON 内的 `arm/seed/cost` 与 mtime → 陈旧文件理论上可蒙混过关（当前由「xargs 失败即停」+ `backtest.py:434-441` 成功必写两道防线兜住） | 补字段与 mtime 校验 |
| NB-K（低） | `scripts/work566_delivery_check.py:104-107` | C9 只查 `selected == "best_vloss"`，不查 `complete` 与 `cfg` 非空 → `complete=False` 但 selected 被改成 best_vloss 的文件能过 | 加 `st.get("complete") is True` 与 `bool(st.get("cfg"))` |
| NB-J（极低） | `scripts/work566_report.py:606` vs `scripts/work566_ckpt_snapshot_test.py:66,68,87,89,91,92,94,103,105,106` | 报告称「9/9 PASS」，但测试里 `check()` 实为 **10** 次 | 计数口径对齐（以实际运行为准，本轮未运行，见 §5） |

---

## 4. Q1–Q10 逐条回答

**Q1 `snapshot_state` 是否真独立？**
成立。`_clone_sd`（`train.py:238-240`）对 `state_dict()` 每个条目做 `v.detach().clone()`（非张量原样透传）；`snapshot_state`（`:243-269`）中 `enc/tenc_ema/proj_ema/proj/qpred` 五个模块的 state_dict 全部经 `_clone_sd`，`m_k`/`v_k` 也各自 `detach().clone()`（`:262-263`）。`cfg`/`upd`/`vloss`/`selected`/`complete` 是 Python 标量或 dict。**没有任何活张量能进入落盘 dict**：optimizer/EMA 的 in-place 更新（`:479-484` 旧址 / 现 `:570-574`、`:600-601`）作用在 `m_k/v_k/参数本体` 上，快照是它们的独立副本；`build_reps` 随后从磁盘重新载入（`:718-728`），连「同一进程内活引用」这条路径也断了。唯一保留活引用的旧写法只剩 `train_readout` 的 `best`（`:933-934`，那里写的是 `{k: v.clone()}`，已克隆）。

**Q2 周期保存还能覆盖选型 checkpoint 吗？**
不能。全仓 `torch.save`/`os.replace` 涉及 rep ckpt 的只有三处：`:611-613`（写 `ckpt_path + ".periodic.tmp"`）、`:614`（`os.replace` → `ckpt_path + ".periodic"`）、`:629` 与 `:631`（写 `ckpt_path` 本体）。路径字符串不同 → 不可能互相覆盖。磁盘实证：两个文件同时存在且内容语义正确。

**Q3 回读自检会不会「文件错了却通过」？**
不会（在正常路径上）。自检比较的是「磁盘文件」与「内存里的 `best`」：`selected=="best_vloss"`、`vloss≈best[0]`、`upd==best[1]["upd"]`、`complete is True`（`:633-638`）。`best[1]` 是深拷贝、构造后不再变；`best[0]`/`best[1]["upd"]` 属同一 epoch 的配对值 → 后期 epoch 找到更优 best 只会更新 `best` 整体并重新落盘，不会让旧快照「改口」。**残留两点**：① 自检**不比对张量内容**（只比元数据），若文件被换成品相同元数据、不同权重的副本仍会通过；② 自检用 `assert`，`python -O` 下被剥离（NB-C）；③ `best[1] is None`（验证损失全为 inf）时不落盘也不报错，直接返回 ckpt_path，由下游 `torch.load` 抛错——是响的，不静默。

**Q4 `--resume-rep` 现在安全吗？**
基本安全，但有 fail-open 边界。代码：`train.py:1056-1075`。合成用例实测（源码块 + 假 torch/os，**未触碰真实数据文件**）：
- (a) 周期快照（`selected='periodic', complete=False`）→ **拒绝**（且周期文件本就在另一路径，`:614`）；
- (b) 修复前代码路径产出的 ckpt（无 `selected/complete/cfg`）→ **拒绝**，并提示「该文件由修复前的路径产生，需删除后完整重训」；
- (c) cfg 与当前 CLI 不一致 → **拒绝**（`{'epochs': (8, 4)}`）；一致 → 放行。
边界：cfg 缺失 → **放行**（NB-D）；cfg 多出未知键 → 放行（向前兼容，可接受）；`complete=1`（int）→ 拒绝（`is not True` 严格比较，好）。

**Q5 xargs 掩盖真修好了吗？会不会被陈旧文件骗过？**
掩盖已修，且是**实测**的：等价 shell 片段中，旧写法 `bash -c 'false; echo "完成…"'` → xargs 退出码 **0**、链继续打印下一行；新写法 `bash -c 'if ! cmd; then echo "失败" >&2; exit 1; fi; echo "完成…"'` → xargs 退出码 **123**、`set -euo pipefail`（`stage3.sh:5`）直接断链。`:84-91` 正是新写法。
陈旧文件问题**在链级已被堵住**：回测失败 → 非零退出 → 断链，`:95-108` 的 54 份齐备性检查根本走不到；即便走到，`backtest.py:434-441` 每次成功必写 `backtest_{arm}_seed{seed}_c{cost}.json`（名字与 `:103` 的期望名一致：成本按 `float()` 格式化，`0.0030→c0.003`）。**残留**：该检查本身只验「存在且非空」，不验 JSON 内 `arm/seed/cost` 与 mtime（NB-I）。

**Q6 Stage 3 前置内容校验真能挡冒烟产物吗？**
能。`stage3.sh:27-48` 逐臂比较 `len(z["pred_test"])` 与 `sample_stats.json` 的 `n_test=2,949,469`（`experiments/WORK-566/sample_stats.json`），不符即 `exit 6`。基准来源正确：该文件由 `scripts/work566_prior.py:315-327` 写出，prior.py 的日期区间是硬编码全量（`:44-49`，btest = 20240102~20260626），**不受任何冒烟开关影响** → 校验不是自指的。冒烟产物（15,276 行）会被拦。**残留**：fmb 臂不在内容校验列表内（NB-H）。

**Q7 H2/H3/H4 的新种子守卫行为正确吗？**
- 不崩：H2 `len(diffs)<2` → 覆盖 `conclusion` + `delivered=False`（`primary_test.py:236-241`）；H3 逐臂 `delivered`（`:259-262`）；H4 `delivered=all(v>=2)`（`:294-295`）。
- 不静默出单种子结论：H2 的 `conclusion` 被改写为「未交付（有效种子 < 2）」；H3/H4 的交付状态写进 metrics 且在 `report.py:428-432` 披露。
- 2 个种子在时不会误报未交付：`>=2` 即 `delivered=True`（无额外条件）。
- 残留：H3 的判退判据与 H2 的 pooled 数值未受该守卫完全约束（NB-G）。

**Q8 这次编辑引入的新缺陷？**
(a) `_content_hash`（`train.py:300-314`）：偏移正确（`fstat` 取真实 size，末段 `seek(max(0, size-chunk))`），代价 ~8MB 顺序读；**不会**造成无尽重建——sidecar 存的是**源文件**指纹（`:374`），只要源不变，下次必然命中复用；代价是「只哈希首末 8MB」→ 中段改写漏检（NB-E）。
(b) 原子 sidecar 写（`:409-414`）：临时名带 PID → 双进程不冲突；`os.replace` 原子；dst 与 sidecar 都是「先内容后指纹」顺序 → 中断落在安全方向。**无竞态**。
(c) `cfg` 指纹（`:546-548`）：8 个 CLI 键覆盖了影响表征训练与选型的主要旋钮（lr/wd 是硬编码 `:525-527`，种子由路径区分）；**不覆盖数据快照与 `--threads/--val-batch`**（NB-E）；`cfg` 也写进了周期快照（`:612` 传同一个 `cfg`）→ 周期文件同样可被指纹校验。另有 fail-open 分支（NB-D）。

**Q9 新 checkpoint 格式会弄坏消费方吗？**
不会。全仓读 `ckpt_seed{seed}_rep.pt` 的地方只有两处：`scripts/work566_train.py:718`（按 key 取 `st["enc"]/st["proj_ema"]/st["qpred"]/st["m_k"]/st["v_k"]`，新增键不影响）与 `scripts/work566_delivery_check.py:93`（C9，只读 `st.get("selected")`）。`audit/recompute_work566.py` 不读该文件（grep 无命中）。没有消费方再假设旧的别名语义——**别名语义本身已被消灭**（落盘的是拷贝，`:256-269`）。另：`work566_ckpt_snapshot_test.py` 是新增的独立回归测试（红-绿两段，10 个断言），未被 Stage 3 链调用。

**Q10 「两个种子跑的是同一份代码」成立吗？**
**成立（截至本轮审查时点），但附带一个前置条件。**
- `git log -- scripts/work566_train.py` 的最后一条是 `37355ee`（2026-11-25 **18:59:21**），此后到 HEAD `6645db2` 无任何 train.py 改动；
- 在训的 seed42 进程 PID 3267519 起于 **18:59:26**（`ps` lstart），命令 `../.venv/bin/python -u scripts/work566_train.py --seeds 42 --threads 6 --rep-batch 1024` → 晚于该 commit 5 秒，跑的就是 `37355ee` 的 train.py；
- `git status --porcelain scripts/` **干净** → 磁盘上的 train.py 字节 == HEAD == seed42 正在跑的字节；
- `.w566tmp/w566_fullchain.sh` 的 `run_seed 42` → `run_seed 2024` 用同一 `REPO` 目录、同样参数（仅 `--seeds` 不同），seed2024 尚未开始。
**条件**：seed2024 启动前不得再改动 `scripts/work566_train.py`（哪怕未提交）。建议在 seed2024 启动那一刻记录 `sha256sum scripts/work566_train.py` 与 `git rev-parse HEAD` 写进 `failure_log`，把「同一份代码」从推断变成可核事实。

---

## 5. 明确列出「未验证 / 未取到」

1. **训练完成与最终数字**：seed42 仍在 epoch1（审查时 upd≈1450/4000），seed2024 未开始 → H1/H2/H3/H4 的最终数值、判词方向、报告数字**全部未验证**（需跑完训练与 Stage 3，本轮按硬约束不跑）。
2. **`scripts/work566_ckpt_snapshot_test.py` 的实际 PASS 结果**：本轮**未运行**——该脚本 `import torch`（RSS 常 >250 MB），而本机 swap 已满、MemAvailable 在 0.9–2.9 GB 波动（`RUN_STATE.md:64-69`），启动它有把在训的 seed42 进程挤爆触发 OOM 的风险。替代证据：静态逐行审读（红-绿两段逻辑正确）+ 磁盘上真实 checkpoint 的新格式与语义自洽。报告称「9/9 PASS」与代码里的 10 个断言不符（NB-J），实跑后才能定论。
3. **Stage 3 全链端到端**（含真实回测失败是否断链、54 份产物是否真齐、C1–C9 是否全绿）：**未跑**。已用等价 shell 片段实测 xargs 退出码语义（0 → 123），其余为静态推理。
4. **`preds_seed42_fmb.npz` 的再生成本身**：磁盘上当前确实缺失（只有 seed2024 版），Stage 3 会自动跑 `work566_baselines.py --seed 42`；该脚本内**无任何随机数调用**（grep `random/default_rng` 无命中）→ 预期确定性，但**未实跑复算**其 RankIC 是否仍为 9.377620%。
5. **交付自检 C1–C9 在新产物上的结果**：未跑（依赖上一项）。
6. **`_content_hash` 在 3GB 面板上的实测耗时与面板中段改写的漏检概率**：未实测（重算哈希会与在训进程抢 IO/内存）。
7. **审计链路 `audit/recompute_work566.py` 对新 ckpt 的行为**：静态确认它不读 rep ckpt（grep 无命中），但**未实跑**。

---

**VERDICT：PASS（通过）** —— 无 BLOCKING 残留；B3/B4/B5 三项整改均经代码、磁盘产物与合成用例三重验证成立，新发现 11 项均为 NON-BLOCKING（披露口径 / 边界加固 / 一致性），建议在下次提交中一并处理 NB-B、NB-A、NB-C、NB-D。
