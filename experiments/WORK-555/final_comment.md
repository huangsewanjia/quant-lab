## 终版交付 · DEE（异质专家集成）在 A 股 CSI300 面板上的复现

**判词：未复现（退）**（严格按预注册 §6.2 机械规则，无自由裁量）

依据（预注册 §6.2 逐条判据，每条给出实测值；共 3 条命中）：

1. Δ_ic 均值 −29.15% **≤ 0** → DEE 不优于最佳单族
2. DEE 净 Sharpe（rf=0，8 bps）均值 −0.5841 **< 0** → **直接退**
3. 95% CI 含 0 → 记「未复现」（仍算交付）

> 预注册：`experiments/WORK-555/preregistration.md` v1.0（commit `9a85409`，**早于**任何回测产物 commit）。本次交付的全部数字来自 v4 重跑后的产物；**中途重跑过两次**——第一次因独立代码审查判 FAIL（S2 早停监控量与预注册文字不符、S3 组合权重泄漏），第二次因收口审计发现「产物 float32 量化导致审计复算与建模侧不一致」；两次的原因与影响见下文「独立审查」段与 `failure_log.md` §13/§15。

> **关于本单此前那条「首屏样张（版式先交，数字后补）」评论**：那条评论里的数字栏是**当时的占位值**（训练未完成时填的），**已被本条终版评论全面取代**，请勿引用。那条评论的用途只是先确认版式；本条才是数字与判词的正式交付。

### 主检验量三档对照（Δ_ic = (RankIC_DEE − RankIC_MLP-only) / RankIC_MLP-only，同种子）

| 臂 | 种子 | 测试 RankIC | 净 Sharpe(rf=0, 8bps) | 年化(8bps) | MDD(8bps) | 日均双边换手 |
|---|---|---|---|---|---|---|
| **DEE (N=20)** | 42 | +0.030327 | −0.3027 | −8.14% | −43.90% | 1.001 |
| **DEE (N=20)** | 43 | +0.021631 | −0.8655 | −19.09% | −64.13% | 1.063 |
| **MLP-only (N=20)** | 42 | +0.037082 | −0.6661 | −14.37% | −55.23% | 1.002 |
| **MLP-only (N=20)** | 43 | +0.036100 | −0.3954 | −10.35% | −51.30% | 1.028 |
| XGBoost | 42 | +0.014752 | −1.1463 | −23.78% | −69.15% | 1.211 |
| XGBoost | 43 | +0.016158 | −0.9573 | −20.37% | −64.26% | 1.194 |
| XGBoost（无早停·诊断） | 42 | +0.024574 | −0.9453 | −20.44% | −66.48% | 1.286 |
| XGBoost（无早停·诊断） | 43 | +0.022894 | −0.9510 | −20.46% | −65.57% | 1.340 |

- seed 42：Δ_ic = (0.030327 − 0.037082) / 0.037082 = **−18.22%**
- seed 43：Δ_ic = (0.021631 − 0.036100) / 0.036100 = **−40.08%**
- 两种子 **mean −29.15% ± range 21.86 pp**；95% CI（t 近似，df=1）= **[−168.1%, +109.8%]**（含 0 → 记「未复现」，仍算交付）
- RankIC 绝对量极差：DEE **0.0087**、MLP-only 基线 **0.0010**（预注册 §6.2 阈值 0.01）

### 独立复算（不 import 建模阶段的任何函数）

- 判决：**PASS**，blocking **0** 项 / 共 **318** 项检查项
- Δ_ic 复算值 `-0.2914861368509015`（建模侧 `-0.2914861368509015`）→ 逐位一致
- 组合权重不变量（现金账本口径）：全部 16 份逐日账本 max Σw = **1.000000000**、Σw>1 天数 = **0**、max\|Σw+cash−1\| = **5.551e-16** → 总敞口恒为 1，**无隐含杠杆**

### 与论文逐项对照（arXiv:2611.12389v1，ICAIF '26，论文无官方代码，DEE 全自实现）

| 论文主张 | 论文值 | 本单实测 | 判定 |
|---|---|---|---|
| Table 2：Full DEE vs MLP only 的 Δ | +9.1% ((0.060−0.055)/0.055) | Δ_ic 两种子 −18.22% / −40.08%（mean −29.15%） | **未复现**（**与论文 +9.1% 符号相反**（本单两种子均为负）；两种子同号；均值 −29.15% 不代表方向） |
| Table 1：DEE RankIC | 0.060 | 0.0260（两种子均值） | **绝对水平远低**（因子 48 vs 158、自建面板、PIT 口径、含 A 股成本） |
| Table 3：DEE 年化/Sharpe/MDD | 38.56% / 1.846 / 17.15% | −8.14% / −0.303 / −43.90% | 口径不可得（专有面板、无成本），预注册 §6.3 不追绝对水平 |

### 不可成交断言实测（预注册 §7 失败判据第 4 条）

命令：`/tmp/w555venv/bin/python scripts/work555_nomask_all.py`（4 臂 × 2 种子逐组真跑 `--no-mask`，非抽样）

| 臂 | 种子 | 退出码 | 是否触发 | 命中的不可成交事件 |
|---|---|---|---|---|
| dee | 42 | 1 | **是** | `300017.SZ` 2017-01-16 limit_down/sell_fill |
| dee | 43 | 1 | **是** | `300017.SZ` 2017-01-16 limit_down/sell_fill |
| mlp_only | 42 | 1 | **是** | `000839.SZ` 2017-02-17 limit_up/buy_fill |
| mlp_only | 43 | 1 | **是** | `002797.SZ` 2017-02-08 limit_up/buy_fill |
| xgb | 42 | 1 | **是** | `000839.SZ` 2017-02-17 limit_up/buy_fill |
| xgb | 43 | 1 | **是** | `000839.SZ` 2017-02-17 limit_up/buy_fill |
| xgbfull | 42 | 1 | **是** | `002797.SZ` 2017-02-08 limit_up/buy_fill |
| xgbfull | 43 | 1 | **是** | `002797.SZ` 2017-02-08 limit_up/buy_fill |

**8/8 组合全部触发、退出码全部非 0**（记录文件 `experiments/WORK-555/nomask_assert.json`，含每组 stderr 的 sha256 留痕）。

掩码开启时同一命令 `n_guard_violations = 0`、exit 0（`bt_daily_*.csv` 逐日账本可核）。

### 数据快照（sha256 逐份锁定）

| # | 快照 | 路径 | sha256（前 16 位） | 状态 |
|---|---|---|---|---|
| 1 | `panel_daily` | `data/WORK-555/panel_daily.csv` | `619765cad57063a4` | MATCH |
| 2 | `panel_stk_limit` | `data/WORK-555/panel_stk_limit.csv` | `5a1761c29afb0fc9` | MATCH |
| 3 | `panel_suspend` | `data/WORK-555/panel_suspend.csv` | `41969c4b23bd1651` | MATCH |
| 4 | `panel_index_weight` | `data/WORK-555/panel_index_weight.csv` | `d6eea91ae2623e45` | MATCH |
| 5 | `panel_stock_basic` | `data/WORK-555/panel_stock_basic.csv` | `b3e11fc7e2b8b32e` | MATCH |
| 6 | `panel_backtest` | `data/WORK-555/panel_backtest.csv` | `67ae31fe6fe23a7d` | MATCH |

**6/6 份逐份 MATCH**（含事实层 `panel_backtest.csv` —— 第一轮审查指出它原先没被锁住，已补）。

### 交付件

| 交付件 | 路径 | 大小 | sha256（前 16 位） |
|---|---|---|---|
| report.html | `experiments/WORK-555/report.html` | 1,345,715 B | `791abf7f3dccfa98` |
| report_print.pdf | `experiments/WORK-555/report_print.pdf` | 1,524,438 B | `0b53ce336230e4fd` |
| report_firstscreen_1440x900.png | `experiments/WORK-555/report_firstscreen_1440x900.png` | 212,483 B | `4ca2acfe2c3cb2ce` |
| fig1_rankic_arms.png | `figures/WORK-555/fig1_rankic_arms.png` | 130,224 B | `e9d5a187d4e07e61` |
| fig2_daily_rankic.png | `figures/WORK-555/fig2_daily_rankic.png` | 553,133 B | `b13b2a27d4cd50e6` |
| fig3_net_equity.png | `figures/WORK-555/fig3_net_equity.png` | 196,057 B | `ba1b23005c786947` |
| fig4_paper_compare.png | `figures/WORK-555/fig4_paper_compare.png` | 85,802 B | `7508800566873a91` |

单文件 HTML 自检：内嵌 base64 图 **4** 张、**外链 0 处**（要求 0）、断网可开。

### 独立代码审查（3 轮，全部 FAIL → 逐项处置 → 增量复审）

实现完成后交给**独立子代理**审查（隔离沙箱、零 import 建模函数、自带一套从头写的复算实现）。主循环不参与审查判决；每轮修复后交回同一审查方**复审增量**。

**第一轮**（增量复审范围 `—`）：判决 **FAIL**

> 2 blocking + 3 suggestion + 4 nit；核心链路数值经审查方独立复算全部一致（RankIC/Sharpe/MDD/年化/换手/成本 rd <= 1e-9）

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| B1 | blocking | 快照 sha256 十六进制串被送进 rel_diff() -> float() 抛 ValueError，脚本崩溃、不写 audit_findings.json，旧 PASS 留在盘上被报告层读取… | 改为字符串比较 + 显式 blocking 分支（不送 rel_diff），并补 else 分支写 pass finding… |
| B2 | blocking | manifest 只锁 5 份原始层 CSV，未锁事实层 panel_backtest.csv（95 MB，全部结论的直接输入）… | 补入第 6 份 panel_backtest.csv 并逐份 sha256 锁定… |
| S1 | suggestion | `if rel > worst:` 缩进在 `for i in idxs:` 循环体之外 -> 每个参数族只比对最后一个抽样下标，其余抽样点的梯度错误完全漏检… | 把 worst 更新移入 for i 循环体；新增每参数族覆盖度门（min(3, 族元素数) 个可分辨下标）；grad_check 返回结构化 dict（worst/worst_param/worst_index/per_family）… |
| S2 | suggestion | 预注册 §4.1/§4.3 写死「监控集成级验证损失（不是单专家损失）」，实现监控的是 mean_k MSE(f_k, y)（逐专家 MSE 的平均）… | 按预注册改为集成级验证 MSE = MSE(mean_k f_k(x), y)；并行记录旧口径作对照（loss_monitor / alt_monitor_mean_expert_mse）… |
| S3 | suggestion | 强制持有的旧权重与新买入的 1/K 直接叠加，总权重不做归一 -> 组合带隐含杠杆（实测 Σw 最高 1.44、974 天里 151 天 > 1）；独立复算脚本用了同样写法故查不出… | 回测引擎改为现金账本口径（卖出释放现金、买入受可用现金约束，总敞口恒为 1）；审计侧独立重写同一口径；新增 Σw <= 1 与 Σw + cash ≡ 1 两道闸门… |
| N1 | nit | 字段 rankic_unstable 实际装的是 Δ_ic 极差，而预注册 §6.2 阈值指的是 RankIC 绝对量极差… | 拆成 rankic_range_dee / rankic_range_mlp_baseline 两项，rankic_unstable 改由 RankIC 极差 > 0.01 判定，并附 rankic_unstable_detail 说明指标与阈值… |
| N2 | nit | 95% CI 写成 [-13.08%, +14.87%]，实为比值未乘 100；按百分比应为 [-1307.9%, +1487.4%]… | failure_log 两处 CI 改正为百分数，并写明「Δ_ic 是比值，百分号 = ×100」… |
| N3 | nit | 「5 bps 敏感性下 MLP-only seed 43 的 Sharpe 转正 +0.117，其余仍为负」不准确——dee s42 在 5 bps 也是正的（+0.0803）… | 改为逐臂逐种子列举 5 bps 净 Sharpe（2 正 5 负）… |
| N4 | nit | swrr_types 注释里的并列打破序（linear→ridge→lasso→elasticnet→MLP→soft tree）在代码中未显式实现（np.argmax 取首个最大值）… | 注释补上等价性论证（ratios 入参顺序 == 预注册固定序号，argmax 取首个最大值 ≡ 取小者）；并新增 FROZEN_TYPE_SEQ_N20 机器断言… |

留痕：`experiments/WORK-555/review_round1.json`

**第二轮**（增量复审范围 `8fd1bb3..1be06e3`）：判决 **FAIL**

> 1 新 blocking（生成器在受审 commit 里不可用）+ 4 suggestion + 2 nit；9 项发现里 7 项已修且经独立实测验证（含负控），N2/N3 因生成块标记未随 commit 走而未在受审状态生效

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| RB1 | blocking | 生成块标记未随 1be06e3 提交 -> apply_block 直接 SystemExit，生成器写模式与 --check 双双 exit 1，work555_finalize_v3.sh 的 [4b/9] 与 [9… | apply_block 改为标记缺失时**按小节锚点自动插入**而不是中止；标记随 94afd78 提交… |
| RS1 | suggestion | --check 模式仍写盘（json.dump 在 --check 分支之前），与「只检查不写」自述冲突，且检查失败时也会覆盖合法产物… | json.dump 移到 --check 分支之后；--check 现在连 final_numbers.json 都不落地… |
| RS2 | suggestion | 生成器对半成品产物不健壮：0 字节 bt_daily_*.csv 触发 EmptyDataError；d['rankic'] 为 None 时 TypeError。v3 重跑在途时半成品必然出现，两处都在关键路径上… | collect() 对 CSV 读取加 try/except 并跳过空文件/缺列文件；Δ_ic 计算加 None 与零分母守卫… |
| RS3 | suggestion | 生成器内嵌了不随产物重算的叙述性数字（「年化双边换手 ~100%」「年化摩擦 ~39%」「XGBoost 实际只用 1 棵树」），产物一变就与 §12.3 的动态值互相矛盾——正是生成器想消灭的那类漂移… | 全部改由产物算：日均双边换手 / 年化双边换手（倍）/ 日均成本（bps）/ 年化摩擦 从 backtest 汇总派生；XGBoost 实际用树数从 run_xgb_s42.json 取… |
| RS4 | suggestion | B2 建立的「事实层必须 sha256 锁定」原则只覆盖 data/WORK-555/*.csv，未覆盖审计的直接输入；第三方无法确认复算用的是同一份输入… | 新增 experiments/WORK-555/artifact_manifest.json（scripts/work555_artifact_manifest.py 生成/校验），把审计的直接输入与建模汇总逐份 sha256 锁住；审计脚本新增 artifact_sha256::* 检查… |
| RN1 | nit | 报告层汇总 weight_invariant 时只展示 max Σw 与 >1 计数，未展示账本偏差 max|Σw+cash−1|，却在文案里宣称判据含该式… | 审计 pass finding 里补记 ledger_max_dev / n_days_over_1；报告层三项并排展示… |
| RN2 | nit | 审计侧 pos[c] 在「持仓股当日无面板行」时 KeyError（事实层非稠密：每股最少只出现 25/974 天）… | 改为 pos.get(c)，语义与建模侧 idx_of.get(c) 对齐（当日无行 -> 不可成交 -> 强制持有；结算跳过）… |

留痕：`experiments/WORK-555/review_round2.json`

**第三轮**（增量复审范围 `1be06e3..7de12b6`）：判决 **FAIL**

> 3 blocking + 6 suggestion + 4 nit。第二轮 7 项处置中 5 项完全有效、2 项部分有效；X1 线程敏感性收敛表述获认可（独立复跑与 §14 逐位吻合）；但提交的 audit_findings.json 比它审计的产物旧一代，导致交付件内部数字互相矛盾，且生产者自证门禁对 238 项未关闭 blocking 完全盲视

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| B1 | blocking | 提交的审计产物比它审计的产物旧一代（审计 16:45，mlp_only_s42 重训 16:48），主检验量复算值 +0.8194 与建模侧 −0.2705 符号相反（绝对差 1.08992 / 相对差 4.030），却… | ① 审计输出新增 generated_at 与 artifact_manifest_sha256（本次审计所依据的产物清单指纹）；② final_numbers.py 实算复算值与建模侧的差，按实测措辞（逐位一致 / 相对差 x / **不一致**），不再无条件写「逐位一致」；③ --check 增… |
| B2 | blocking | 交付件内部对同一量给出互相矛盾的值：report.html 判词 −27.05% vs 翻案条件段 +89.76%；registry 33.4% vs failure_log ~39%（年化摩擦）；registry「— … | ① 报告 §10 翻案条件段的硬编码「+89.76%」「~100%」改为按产物计算；② failure_log §12.3 的「日成本约 15~16 bps / 年化摩擦 ~39%」改为从 backtest 汇总派生（日均成本 bps / 年化摩擦 % / 日均与年化换手分开写）；③ 生成器读 XG… |
| B3 | blocking | 238 项审计 blocking 未关闭（14/16 组账本仍是旧引擎产物），而生产者自证门禁对这个状态报 8/8 PASS —— 门禁对「审计陈旧」完全盲视… | ① 收口流水线重跑全部 8 臂回测（现金账本新引擎）→ 账本全部带 sum_w/cash，238 blocking 归零；② selfcheck.sh 新增「审计新鲜度」一项：审计记录的产物清单 sha256 必须与当前清单一致，且 n_blocking 必须为 0，任一不满足即 FAIL… |
| S1 | suggestion | --check 从不读 final_numbers.json 自身，却打印「final_numbers.json 与两处文档一致」；把它改成 0.9999 仍 exit 0… | --check 现在把磁盘上的 final_numbers.json 与按当前产物重算的结果逐字段比对，不一致即 FAIL… |
| S2 | suggestion | RS2 残留：d['cash'] 在 try 之外，守卫只检查 sum_w -> 含 sum_w 但缺 cash 的账本触发 KeyError… | 守卫改为同时要求 sum_w 与 cash 两列；缺任一列的账本不参与不变量统计… |
| S3 | suggestion | 审计输出无时间戳、也无输入绑定 -> 审计陈旧不可见（直接导致 B1）… | 审计输出新增 generated_at 与 artifact_manifest_sha256；消费端（final_numbers --check、selfcheck.sh）据此判定新鲜度… |
| S4 | suggestion | 硬编码 cd /srv/share/quantdb/code/quant-lab -> 不可沙箱化，独立审查方无法在不写被审仓库的前提下跑生产者的自证门禁（其余脚本都用 abspath(__file__)，此处是唯一例外… | 改为按脚本自身位置定位仓库（BASH_SOURCE -> dirname -> 上级目录）；拷进沙箱后直接运行，输出落在沙箱… |
| S5 | suggestion | 潜伏硬编码段：「MLP-only 基线自己的两种子极差就超过了预注册的 0.01 阈值」——当前未渲染（dee 的 loss_monitor 为空走 else 分支），但一旦该字段有值就会渲染出与产物相反的结论（实测极差… | 按实测分支：极差超阈值才写「由基线种子噪声决定」；否则改写为「基线本身是稳的，方向由相对表现决定」… |
| S6 | suggestion | registry 写「全部 bt_daily 的 max Σw」，而统计实际只在带 sum_w/cash 列的 n_files 份上算（当时只有 2 份）… | 表述改为「N 份带账本列的 bt_daily 里」；收口流水线重跑后 N = 16… |
| N1 | nit | RB1 恢复路径非逐字节：§13 块落到文末（锚点 §13.1 不存在）+ 多余空行… | 锚点改为 §14（存在时逐字节一致）… |
| N2 | nit | 把日均换手标成「年化双边换手」（真实年化 252.13/261.77 倍）… | 日均与年化分开写，年化值由 turnover_total/n_days*252 派生… |
| N3 | nit | 臂 C 的 note 写「截断到 1 棵树」，但该 seed 实为 best_iteration=1 -> 用了 2 棵树（复制粘贴错误）… | note 按 best_iteration 实测生成；重跑该臂（npz 逐字节不变：s42 f2ea526eedc494fc / s43 6b2b3ee35482773b）… |
| N4 | nit | commit message 称「报告 §6 权重闸门改为三项并排」，但三项代码在 ff55dee 已有，该 commit 实际只改了 HTML 转义… | 不改历史（不重写公共历史）；以本文件与提交说明为准… |

留痕：`experiments/WORK-555/review_round3.json`

### 字节级可复现性（补上第一轮审查未能核实的项）

在独立沙箱（`/tmp/w555_repro`）用**同一份代码、同一 seed、同一份面板**重跑，与仓库产物逐份比 sha256：

| 产物 | 沙箱 sha256（前 16 位） | 与仓库一致 |
|---|---|---|
| `preds_dee_s42.npz` | `52048ef19cfc5e21` | **是** |
| `preds_mlp_only_s42.npz` | `cfda7e05165c63d9` | **是** |

**2/2 份逐字节一致**（复跑命令：`bash scripts/work555_run_arms_v4.sh`，线程固定为 1 —— 线程数会改变训练数值结果，见 `failure_log.md` §14）。

### 复跑命令（全部可复跑）

```bash
cd /srv/share/quantdb/code/quant-lab
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
bash scripts/work555_selfcheck.sh        # 自证清单：11 项一次跑完
bash scripts/work555_run_arms_v4.sh      # 4 臂 × 2 种子重跑（产物来源，float64 落盘）
bash scripts/work555_finalize_v4.sh      # 回测→产物清单→复算→图→报告→PNG/PDF→自检
/tmp/w555venv/bin/python scripts/work555_float32_quant_diag.py   # 产物精度诊断
/tmp/w555venv/bin/python audit/recompute_work555.py            # 独立复算闸门
/tmp/w555venv/bin/python scripts/work555_final_numbers.py --check  # 对外数字陈旧自检
/tmp/w555venv/bin/python scripts/work555_artifact_manifest.py --check  # 产物清单复核
```

**线程固定是复现前提**：实测表明线程数会改变训练数值结果（`scripts/work555_thread_sensitivity.py`：同代码 / 同 seed / 同 60 步，T=1 与 T=4 的最优快照集成级验证损失相对差 3.569e-05），不固定线程时同命令同 seed 结果不可复现。该机制的根因未被单独隔离验证，表述只到「线程数改变数值结果」为止，详见 `failure_log.md` §14。

