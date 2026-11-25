<!-- 自动生成：python scripts/work566_registry_row.py（数字只抄审计产出） -->
## 登记行（粘进 experiments/registry.md 的一、登记表）

| `WORK-566` | 2026-11-25 | 论文 **STOCK-JEPA**（arXiv:2610.07006v1）「S1 岭回归先验锚点 → S2 Transformer 修正分支 → S3 冻结表征读出」三段式结构在 **A 股公开日频数据**上的**增量**可复现性 | 2026-06-26 | `experiments/WORK-566/snapshot_manifest.json`（23 端点 × 23/23 ok；36 个快照文件 sha256 逐条锁定） | 股票池 = quantdb 窗口内有过任意 bar 的全部 ts_code（5783 只，含退市）；切分 2014–15 先验初装 / 2016–21 训练 / 2022–23 验证 / 2024-01-02~2026-06-26 测试（以论文原文为准）；40 维自定特征契约；S1 扩张窗口岭回归 α=20；S2 summary-token Transformer（d_model 64 / 2 层 / 4 头 / FFN 128，**算力降级**）；S3 读出 MLP/线性；成本三档 单边 5/15/30 bps；种子 {42, 2024}；A 股约束（涨停不可买顺延 / 跌停顺延 / 停牌顺延 / 退市 1% 清算）| **H1 ΔRankIC（full − S1-only，跨种子中位数）= 7.9153 pp**（论文基准 +6.07 pp；阈值 进 ≥+3.0 / 退 ≤+1.0 / 部分复现 (1.0,3.0)）→ **判词：三段式增量复现成立**；full RankIC 10.6388 [10.3638, 10.9139]%、净 SR 1.4055 [1.3295, 1.4815]、MDD -0.24 [-0.25, -0.23]%；S1-only RankIC 2.7235 [-0.4085, 5.8555]%；FMB-style RankIC 9.3776 [9.3776, 9.3776]%（论文 8.76%）；H2 full vs FMB 差 1.2612 pp，95% CI [-1.6899685498295276, 4.8874583010493495]；独立复算 **PASS —— 可进入研究备忘录**（PASS 249 / BLOCKING 0） | **三段式增量复现成立** | ① **任务书切分与论文原文冲突**：任务书写「2009–2019 训练 / 2020–2021 验证」，论文原文为「2014–15 先验初装 / 2016–21 训练 / 2022–23 验证 / 2024–26 测试」→ **以论文原文为准，两份在评论与报告里并列，不合成**<br>② **样本量与论文 Table 3 的差异**：train 3,989,422 vs 3,883,006、valid 2,126,317 vs 1,990,842、test 2,949,469 vs 2,965,433（−0.54%）；差异来自股票池 +2.0% 与目标窗跨切分边界的边界清理<br>③ **测试段右边界**：目标窗越过数据截止日的最后 21 个交易日无实现收益 → 本单实际 **578 个预测日**，论文报 599 个（样本总数几乎一致）<br>④ **预注册 v1.1（唯一增补）**：<code>mlp_std</code> 读出分支标准化后加 [−8,8] 裁剪，根因是重尾列产生 max|z|=20.36 的离群点（R0–R3 验证损失 8.5e3~9.2e3 vs 其余四臂 0.33~0.36）；**只作用于 R0–R3（H4 只报数）**，H1/H2/H3 计算路径一行未改；v1.0 保留不删，阈值全未动<br>⑤ **实现期 15+ 处真实缺陷**已逐条留痕于 `failure_log.md`，含 1 处**标签前视**（M^(h) 最大回撤取窗口后极值 → 改 DP 前向）、1 处**训练/验证边界泄漏**（目标窗跨切分未剔除）、1 处**协议 off-by-one**（持有期 t+22 → t+21）、以及**回测/审计两侧同款错**（复算侧也犯了 off-by-one → 独立复算会「假一致」）<br>⑥ **复用评估**：两个同名仓库（Jack1320/StockJEPA 无代码；Gen-Verse/JEPA-Anything 语义冲突）均排除 → 自研（`reuse_assessment.md`）<br>⑦ **宏观 3 维不入必需项**：论文未点名 SHIBOR/USDCNH/信用利差 → 不引入任何外部宏观端点，改用库内 12 个市场维度<br>⑧ **代码审查由独立子代理完成**（主循环不自审），报告见 `code_review_work566.md` 与 `code_review_work566_round3.md`；第 1 轮判定 **FAIL（1 BLOCKING + 11 NON-BLOCKING）**，全部已修并重跑全链：B1 组合估值用未来 bar 价格盯市（前视泄漏，触发 188 次 / 100% 用未来价）→ 改用「估值日之前最后一个可得 bar」；N1 卖端与最终清算补计交易成本；N2 S2 编码器训练期 dropout 位置/次数与官方不等价 → 按 torch 2.15 源码逐位复刻（train 模式 max\|diff\|=0.00e+00）；N5 推理补 clamp(−8,8)；N6 加「种子 < 2 → 未交付」与失败判据 3；N7 加 NAV ≤ 0 守卫；N9 断言改由**引擎自身守卫**触发；N3/N8 以披露处理、N11 legacy 文件改名；第 2 轮复核判定上述均已修、新发现 **B2**（`build_reps` 写回 u 时误用全局样本下标 → 越界 IndexError）→ 改为 split 内位置 `u[pos]`（对齐复核：u 第 r 行 ↔ 样本 idx_all[r]，与 y 同序，无错位），冒烟复现通过；第 3 轮复核（两位独立 verifier）判定 B2 已修、**新发现 3 项 BLOCKING**：**B3** 落盘的表征 checkpoint 实为**最后一轮**权重而非预注册 §10.2 冻结的「验证损失最小者」（`state_dict()` 返回**活引用** + 训练末尾再次 `torch.save(best[1])` + 周期快照写同一路径，三重叠加；实测落盘文件 `upd=1000`（best=epoch0）却带 epoch3 的 `m_k/v_k`，自相矛盾）—— `s1_only` 不用表征（不受影响）而 `full` 用（受影响）→ **系统性压低 ΔRankIC**；**B4** Stage 3 前置检查漏 `fmb` 臂（该臂由 `work566_baselines.py` 产出、训练脚本不产）+ xargs 尾随 echo 掩盖回测失败；**B5** `--resume-rep` 无完成度校验。三项**全部已修**（深拷贝快照 + 周期快照独立路径 + 落盘回读自检 + resume 校验 `selected=="best_vloss"` + 红-绿回归测试 10/10 PASS + Stage 3 前置补齐 fmb + 54 份产物齐备复核 + 交付自检新增 C9)；**第 4 轮复审判「通过」**（B3/B4/B5 经代码 + 磁盘产物 + 合成用例三重验证成立，无新 BLOCKING），其 11 项 NON-BLOCKING 已全部处理（见 `code_review_work566_round4.md` 与 `failure_log.md` §15）；因 epoch0 权重不可恢复（备份经 md5 证明与末轮同源）→ **两个种子已全部完整重训**，本行数字来自重训后产物<br>⑨ **训练期 I/O 事故与终局方案**：面板随机取数在冷缓存下被内核 readahead 放大 **129×**（每批 4.9MB 有用数据实测读盘 636MB），训练速度掉到 0.12 upd/s；终局方案为 **train 日期片预置 tmpfs（1400MB，与源切片 sha256 全量一致）+ stock-major 顺序读 + 去掉 MADV_RANDOM**（顺序读阶段需要内核 readahead）→ 训练 1.72 upd/s，**不改任何取值与口径**；另加 tmpfs 陈旧校验（shape + 起日 + 源文件 size/mtime 指纹）防止面板重建后静默复用旧副本<br>⑩ **整机 OOM 事故（本单真正的拦路虎）**：两种子并行时整机触发 OOM killer，内核同时杀了训练进程与 `multica-daemon.service` —— 平台提示里的 `daemon shut down while task was in flight` 即此 OOM 的次生结果（非平台故障）；处置：两种子改**顺序**跑（单进程 6 线程，硬占用 4.2GB → 2.8GB，总墙钟不变）、`setsid nohup` 脱离会话、挂内存看门狗；另实测线程数 A/B 为**负结果**（8 线程比 6 线程慢 3.6 倍，整机已超订）→ 维持 6 线程<br>⑪ **线程数不影响数值**：seed42 在 4 线程双进程与 6 线程单进程两种配置下 `upd50/100/150/200` 的 L_anc/L_rev **逐位相同**（66.6294/53.4636 → 50.7801/35.0353）<br>⑫ **B3 修复后完整重训**：seed42 重训的 `upd50` 损失为 L_anc=66.6294 / L_rev=53.4636，与修复前逐位相同 → 重跑轨迹可复现，epoch0 权重按预注册选型真正落盘；旧表征与旧产物已隔离到 `data/WORK-566/stale/`（含 buggy ckpt 的 md5）并留档<br>⑬ **读出阶段行序风险已实测排除**：冷页缓存散列读 26 s/全 epoch（热缓存 0.1 s）→ 不做干预（§13.8）|

## 详情卡（粘进 experiments/registry.md 的二、实验详情卡）

### WORK-566 · STOCK-JEPA（arXiv:2610.07006v1）三段式结构在 A 股的增量复现

| 项 | 内容 |
|---|---|
| **实验 ID** | `WORK-566`（平台 issue id `01a2108c-32e6-76b3-a1ea-c4f0ead6aede`） |
| **执行日期** | 2026-11-25 |
| **研究对象** | 论文 STOCK-JEPA（Luo, Yi, Zhang, Sun. *Prior-Anchored Latent Revision Representation Learning in Equity Markets*, arXiv:2610.07006v1）的三段式结构（S1 岭回归先验锚点 → S2 Transformer 修正分支 → S3 冻结表征读出） |
| **唯一主检验量** | H1 = ΔRankIC = RankIC<sub>full</sub> − RankIC<sub>S1-only</sub>，**跨种子中位数** |
| **判词阈值（预注册写死）** | 进 ≥ +3.0 pp / 退 ≤ +1.0 pp 或为负 / 部分复现 (1.0, 3.0)；论文基准 **+6.07 pp**（Table 4：S1-only 5.15% → full 11.22%） |
| **H1 实测** | **7.9153 pp** → **判词：三段式增量复现成立** |
| **数据源** | 本地 quantdb parquet 原始层（只读）+ tushare MCP 端点复测；23 个端点 23/23 ok |
| **数据截止点** | 2026-06-26 |
| **代码提交** | `040b283b0461bcd99edd6cd4eb9b07a957e7ed12`（分支 `agent/agent/e0a29716dad1`） |
| **快照** | `experiments/WORK-566/snapshot_manifest.json`（36 个文件 sha256 逐条锁定） |
| **预注册** | `preregistration.md`（v1.0，commit `16b24fb`）→ `preregistration_v1.1.md`（commit `7580a3d`，唯一增补）→ 首个回测产物 commit `a3d8b88`（**次序由 `git log` 可查**） |
| **样本量核对** | train 3,989,422 / valid 2,126,317 / test 2,949,469；论文 Table 3 为 3,883,006 / 1,990,842 / 2,965,433 |
| **种子** | {42, 2024}（论文 3 个，算力降级为 2 个） |
| **独立复算** | PASS —— 可进入研究备忘录（PASS 249 / BLOCKING 0）；判据：指标类逐项相对差 ≤ 1e-6，不一致以复算值为准；复算脚本 **不 import 建模阶段任何函数**，交易日历从 parquet 独立重取 |
| **代码审查** | 独立子代理（主循环不自审），报告 `code_review_work566.md` |
| **失败记录** | `failure_log.md`（15+ 处实现期与运行期真实缺陷，逐条含现象/根因/处置/证据） |
| **交付物** | 单文件 HTML 报告 `report.html`（图 base64 内嵌、CSS 内联、无外链 JS/CSS、断网可读）+ 1440×900 首屏 PNG + A4 打印首页 PDF + `metrics.json` / `verdict.json` / `metrics_table.csv` / `paper_comparison.csv` / `paper_comparison.md` / `audit_findings.json` / `data_probe.json` / `panel_audit.json` / `sample_stats.json` |

**结论（不构成投资建议）**

三段式增量复现成立。H1 ΔRankIC = 7.9153 pp（论文 +6.07 pp），
阈值 进 ≥+3.0 / 退 ≤+1.0 / 部分复现 (1.0,3.0)。
本单**绝对水平与论文不可比**（40 维字段自定、市场级 12 维自定、算力降级、股票池 +2.0%、
A 股执行约束、读出数值保护），**只有相对增量可作结构性比较**。
本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。
