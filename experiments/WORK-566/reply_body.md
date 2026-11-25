## WORK-566 交付：STOCK-JEPA 三段式结构在 A 股的增量复现 · 判词「未交付（有效种子 < 2）」

### 1 · 唯一主检验量与判词

- **H1 = ΔRankIC = RankIC<sub>full</sub> − RankIC<sub>S1-only</sub>，跨种子中位数 = — pp**
- 预注册阈值：进 ≥ +3.0 pp / 退 ≤ +1.0 pp 或负 / 部分复现 (1.0, 3.0)；论文基准 **+6.07 pp**（Table 4：5.15% → 11.22%）
- 有效种子 0 个（42 / 2024），每种子 ΔRankIC — ~ — pp
- 判定规则：预注册 §9「只报单种子结果 = 未交付」
- **判词：未交付（有效种子 < 2）**

### 2 · 三档消融（测试期 2024-01-02 ~ 2026-06-26，主成本档 单边 5bps；mean ± range）

| 成本 | 各臂 RankIC(%) |
|---|---|
| 5 bps | fmb 9.3776% [9.3776, 9.3776] |
| 15 bps | fmb 9.3776% [9.3776, 9.3776] |
| 30 bps | fmb 9.3776% [9.3776, 9.3776] |

- full：RankIC —%｜净 SR —｜净 AR —｜MDD —%
- S1-only：RankIC —%｜净 SR —
- FMB-style 基线：RankIC 9.3776 [9.3776, 9.3776]%（论文 8.76%）｜净 SR 1.2786 [1.2786, 1.2786]

### 3 · 并列检验

- **H2**（full vs FMB-style 逐日 RankIC 差，block bootstrap 20 日 / 10000 次 / seed 42）：— pp，95% CI None → —
- **H3**（可交易性）：full 净 SR —，95% CI 下界 —，相对论文 1.50 的比率 —（降级配置下只披露不判定；净 SR < 0 直接判退仍生效）
- **H4**（R2 vs R3，只报数）：R2 —% vs R3 —%，差 — pp

### 3b · 基准调整 alpha（等权 + 市值加权）与回撤对照

**基准调整 alpha**（口径：日超额收益 = 组合日收益 − 基准日收益；等权基准 = 面板内当日有 bar 的全部股票等权平均，
市值加权基准 = 同日 `circ_mv` 加权平均；年化 = 252 × 日均超额；括号内为超额日均的 t 统计；种子 42 / 2024 的均值）

| 臂 | 基准调整 alpha（年化超额） |
|---|---|
| full | — |
| S1-only | — |
| FMB-style 基线 | 等权全市场 +0.26%/年（t=1.94）；市值加权 +0.10%/年（t=0.76） |

**回撤对照**（任务书「MDD 是否差于最强基线」并列验收项，不许只报 Sharpe）

| 口径 | MDD |
|---|---|
| 论文 STOCK-JEPA（Table 1） | -31.09% |
| 论文 MASTER（Table 1，最强基线） | -21.46% |
| 论文 FMB-style（Table 1） | -16.36% |
| 本单 full（主成本档 5bps） | —% |
| 本单 S1-only | —% |
| 本单 FMB-style 基线 | -0.20 [-0.20, -0.20]% |

- 本单**绝对水平与论文不可比**（40 维字段自定、市场级 12 维自定、算力降级、股票池 +2.0%、A 股执行约束），
  上表并列只为给出**量级参照**；可判的只有同一降级配置内三档之间的相对增量。
- 逐项论文对照表（论文值 / 本单值 / range / 差异 / 差异原因）见交付物 `paper_comparison.csv` 与 `paper_comparison.md`。

### 4 · 独立复算与代码审查

- **独立复算**：PASS —— 可进入研究备忘录（PASS 24 / BLOCKING 0）；判据逐项相对差 ≤ 1e-6；复算脚本**不 import 建模阶段任何函数**，交易日历从 parquet 独立重取
- **独立子代理代码审查**（verifier，主循环不自审，共 **3 轮**）：第 1 轮判定 **FAIL（1 BLOCKING + 11 NON-BLOCKING）**，全部已修并重跑全链 —— B1 组合估值用未来 bar 价格盯市（前视泄漏）、N1 卖端与清算成本、N2 S2 编码器训练期 dropout 与官方不等价、N5 推理裁剪、N6 种子守卫、N7 NAV 守卫、N9 引擎级断言；N3/N8 以披露处理。第 2 轮复核判定上述均已修、新发现 **B2**（`build_reps` 写回 u 时误用全局样本下标 → 越界）→ 改为 split 内位置并冒烟复现通过。第 3 轮复核判定 B2 已修、**新发现 2 项 BLOCKING**：
  - **B3（会改变判词方向）**：落盘的表征 checkpoint 实为**最后一轮**权重，而非预注册 §10.2 冻结的「验证损失最小者」——`state_dict()` 返回**活引用** + 训练末尾再次 `torch.save(best[1])` + 周期快照写同一路径，三重叠加；实测落盘文件 `upd=1000`（best=epoch0）却带着 epoch3 的 `m_k/v_k`，自相矛盾。`s1_only` 不用表征（不受影响）而 `full` 用（受影响）→ 系统性**压低 ΔRankIC**。已改为深拷贝快照 + 周期快照独立路径 + 落盘回读自检 + `--resume-rep` 校验 `selected=="best_vloss"`，并补红-绿回归测试（10/10 PASS）与交付自检 C9。**因 epoch0 权重不可恢复（备份经 md5 证明与末轮同源），两个种子已全部完整重训**，本评论全部数字来自重训后产物。
  - **B4**：Stage 3 前置检查漏 `fmb` 臂（该臂由 `work566_baselines.py` 产出、训练脚本不产）→ 回测跑到一半才崩；已改为前置缺失即补齐 + jobfile 写入前截断。
  - **第 4 轮复审（只审第 3 轮的整改）判「通过」**：B3/B4/B5 三项经代码 + 磁盘产物 + 合成用例三重验证成立，无新的 BLOCKING；磁盘实证选型 ckpt 为 `upd=1000 / vloss=115.0360 / selected=best_vloss / complete=True`，周期快照另存 `.periodic` 不再覆盖。
  - 第 4 轮另提 11 项 NON-BLOCKING，**全部已处理**：报告补「编码器自检只覆盖 layer 级、不覆盖 `SeqEncoder` 外层」的范围限定；报告与实现的 tmpfs 指纹口径对齐（`size:mtime:sha256(首末各 4MB)`）；回读自检的 `assert` 改 `raise RuntimeError`（`python -O` 下不被剥离）；`--resume-rep` 的配置指纹校验由 fail-open 改 fail-closed；配置指纹补 `--threads` / `--val-batch` 与**数据快照指纹**；选型 checkpoint 改原子写；H3「净 SR<0 判退」加「有效种子 ≥ 2」约束；Stage 3 前置内容校验纳入 `fmb` 臂；54 份回测产物复核加「JSON 内 arm/seed/cost 与文件名一致」；交付自检 C9 补查 `complete` 与 `cfg`。
  - 复审全文：<code>code_review_work566.md</code>（第 1–2 轮）、<code>code_review_work566_round3.md</code>（第 3 轮）、<code>code_review_work566_round4.md</code>（第 4 轮）；整改记录见 <code>failure_log.md</code> §10–§15。
- 复审自检：S2 编码器 eval max|diff| 0.000、**train max|diff| 0.000（逐位一致）**

### 5 · 数据与复现

- 样本量：train 3,989,422 / valid 2,126,317 / test 2,949,469（论文 Table 3：3,883,006 / 1,990,842 / 2,965,433）
- 端点复测 23/23 ok；快照 manifest 锁定 36 个文件 sha256；代码提交 `2d431ba76e22ba56ff7aa64ef1f1e5437e42582e`
- 预注册 commit `16b24fb`（v1.0）→ `7580a3d`（v1.1，唯一增补）→ 首个回测产物 commit `a3d8b88`，**次序由 `git log` 可查**
- 交付物：`report.html`（单文件，图 base64 内嵌、CSS 内联、无外链、断网可读）+ 1440×900 首屏 PNG + A4 打印 PDF + `metrics.json` / `verdict.json` / `metrics_table.csv` / `paper_comparison.csv|md` / `audit_findings.json` / `data_probe.json` / `panel_audit.json` / `sample_stats.json` / `code_review_work566.md` / `code_review_work566_round3.md` / `code_review_work566_round4.md` / `failure_log.md` / `train_log_seed42.log` / `train_log_seed2024.log` / `ckpt_snapshot_test_output.txt` / `assert_violation_log.txt`

### 6 · 必须并列的口径冲突与已知限制

1. **任务书切分与论文原文冲突**：任务书写「2009–2019 训练 / 2020–2021 验证」，论文原文为「2014–15 先验初装 / 2016–21 训练 / 2022–23 验证 / 2024–26 测试」→ **以论文原文为准，两份并列不合成**
2. **绝对水平与论文不可比**：40 维特征字段自定、市场级 12 维自定、算力降级（d_model 64 / 2 层 / 4 头 / FFN 128 / d_z 64 / fp32）、股票池 +2.0%、A 股执行约束自加 → **只有相对增量可作结构性比较**
3. **测试段右边界**：目标窗越过数据截止日（2026-06-26）的最后 21 个交易日无实现收益 → 本单 578 个预测日，论文报 599 个（样本总数几乎一致）
4. **停牌日价格未前向填充**（文档三处声明与实现不符，影响 0）与**退市清算晚 1 个交易日**（因果口径）→ 均已在报告偏离表单列，未改数字口径
5. **训练期 I/O**：面板随机取数在冷缓存下被内核 readahead 放大 129×，经 **train 日期片预置 tmpfs（1400MB，与源切片 sha256 全量一致）+ stock-major 顺序读 + 去掉 MADV_RANDOM** 修复，**不改任何取值与口径**；另加 tmpfs 陈旧校验（shape + 起日 + 源文件 size/mtime 指纹）
6. **整机 OOM（本单真正的拦路虎）**：两种子并行时整机触发 OOM killer，内核同时杀了训练进程与 `multica-daemon.service` —— 平台提示里的 `daemon shut down while task was in flight` 即此 OOM 的次生结果（非平台故障）；处置为两种子改**顺序**跑（硬占用 4.2GB→2.8GB，总墙钟不变）、`setsid nohup` 脱离会话、挂内存看门狗。附实测**线程数 A/B 负结果**（8 线程比 6 线程慢 3.6 倍，整机已超订 → 维持 6 线程），以及 seed42 在 4/6 线程两种配置下 `L_anc/L_rev` 逐位相同的可复现性证据

本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。本结论不构成任何投资建议或推荐。
