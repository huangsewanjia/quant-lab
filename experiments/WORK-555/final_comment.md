## 终版交付 · DEE（异质专家集成）在 A 股 CSI300 面板上的复现

**判词：未复现（退）**（严格按预注册 §6.2 机械规则，无自由裁量）

依据（预注册 §6.2 逐条判据，每条给出实测值；共 3 条命中）：

1. Δ_ic 均值 −29.15% **≤ 0** → DEE 不优于最佳单族
2. DEE 净 Sharpe（rf=0，8 bps）均值 −0.5841 **< 0** → **直接退**
3. 95% CI 含 0 → 记「未复现」（仍算交付）

> 预注册：`experiments/WORK-555/preregistration.md` v1.0（commit `9a85409`，**早于**任何回测产物 commit）。本次交付的全部数字来自**最新一轮收口**的产物。**因 FAIL 驱动的重跑**见 `failure_log.md` §13/§15/§16（第一次因独立代码审查判 FAIL：S2 早停监控量与预注册文字不符、S3 组合权重泄漏；第二次因收口审计发现「产物 float32 量化导致审计复算与建模侧不一致」；第三次因第四轮独立复审判 FAIL：诊断臂 xgbfull 秩口径分歧 + 交付件两处同量不一致），最新一轮收口见 `failure_log.md` §20。

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
| XGBoost（无早停·诊断） | 42 | +0.024601 | −0.9453 | −20.44% | −66.48% | 1.286 |
| XGBoost（无早停·诊断） | 43 | +0.022910 | −0.9510 | −20.46% | −65.57% | 1.340 |

- seed 42：Δ_ic = (0.030327 − 0.037082) / 0.037082 = **−18.22%**
- seed 43：Δ_ic = (0.021631 − 0.036100) / 0.036100 = **−40.08%**
- 两种子 **mean −29.15% ± range 21.86 pp**；95% CI（t 近似，df=1）= **[−168.1%, +109.8%]**（含 0 → 记「未复现」，仍算交付）
- RankIC 绝对量极差：DEE **0.0087**、MLP-only 基线 **0.0010**（预注册 §6.2 阈值 0.01）

### 独立复算（不 import 建模阶段的任何函数）

- 判决：**PASS**，blocking **0** 项 / 共 **330** 项检查项
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
| report.html | `experiments/WORK-555/report.html` | 1,346,435 B | `dc8bd0c26979a22e` |
| report_print.pdf | `experiments/WORK-555/report_print.pdf` | 1,527,990 B | `19ea6de11fe6acf7` |
| report_firstscreen_1440x900.png | `experiments/WORK-555/report_firstscreen_1440x900.png` | 212,483 B | `4ca2acfe2c3cb2ce` |
| fig1_rankic_arms.png | `figures/WORK-555/fig1_rankic_arms.png` | 130,224 B | `e9d5a187d4e07e61` |
| fig2_daily_rankic.png | `figures/WORK-555/fig2_daily_rankic.png` | 553,133 B | `b13b2a27d4cd50e6` |
| fig3_net_equity.png | `figures/WORK-555/fig3_net_equity.png` | 196,057 B | `ba1b23005c786947` |
| fig4_paper_compare.png | `figures/WORK-555/fig4_paper_compare.png` | 85,802 B | `7508800566873a91` |

单文件 HTML 自检：内嵌 base64 图 **4** 张、**外链 0 处**（要求 0）、断网可开。

### 独立代码审查（8 轮，其中 4 轮判 FAIL、4 轮判 PASS → 逐项处置 → 增量复审）

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

**第四轮**（增量复审范围 `9bbb1f1..30215ed（含最终产物 commit bb5c746）`）：判决 **FAIL**

> 2 blocking + 4 suggestion + 3 nit。第三轮 B1 已关闭（审计与被审产物同代、独立重跑逐位一致）、B2 部分关闭（B2 点名的四组数字矛盾已全部消除，审查方 181 项同量对齐里 177 项一致；但同一提交里仍有 2 处新暴露的同量不一致/相反结论）、B3 部分关闭（陈旧审计与 n_blocking≠0 都会让门禁变红，但门禁只读 n_blocking 计数器、不看 findings[] 数组）。float64 落盘**不是绕过**：审计阈值 TOL=1e-6 与审计脚本在整个审查区间内逐字节未变，预注册文件也逐字节未变，产物精度变化被如实写进 failure_log §15 与 report §6，且这是预注册 §7.2「以复算值为准、回退到对应阶段重做」的正确处置形态（重跑全 8 臂使产物无损）。判词「未复现（退）」由预注册 §6.2 机械规则得出，审查方复核一致。两项 blocking 均为**证据链/交付件一致性**问题，均不改变判词（Δ_ic 只用 DEE vs MLP-only）。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| B4-1 | blocking | 非预注册诊断臂 xgbfull 的测试段 RankIC 用**序数秩**算（np.argsort(np.argsort(p))），与其余全部臂和审计侧用的**平均并列秩**口径不同；该臂 RankIC 因此与其产物复算值… | ① scripts/work555_xgb_diag.py:69-70 改用与其余臂/审计侧一致的平均并列秩（或直接复用 work555_dee.rank_ic_by_day，但不得 import 建模函数进审计侧）；② audit/recompute_work555.py 的 IC 项改为按「ru… |
| B4-2 | blocking | 同一提交（bb5c746）对**同一个量**（MLP-only 基线两种子 RankIC 极差 0.0010 ≤ 预注册 0.01 阈值）给出**相反的因果结论**：registry.md ⑤ 写「Δ_ic 的符号由基线… | 把 render_registry_row 的 ⑤ 改成与 §12.2 同源：按 primary.rankic_unstable_mlp 实测分支渲染（超阈值才写「由基线种子噪声决定」，否则写「基线本身是稳的，方向由相对表现决定」），或直接复用 §12.2 的同一段文字渲染函数，保证 registr… |
| S4-1 | suggestion | 自证门禁对「审计存在 blocking」的判定只读 audit_findings.json 的 **n_blocking 计数器**，不自行从 findings[] 数组派生。若审计文件里某条 finding 的 sev… | selfcheck.sh 的审计块增加一条：sum(1 for f in a['findings'] if f['severity']=='blocking') 必须为 0，且与 a['n_blocking'] 互相一致（不一致即 FAIL）；或把 audit_findings.json 也纳入一份… |
| S4-2 | suggestion | nomask_assert.json 的 stderr_sha256 取自子进程 traceback 全文，而 traceback 内嵌**脚本的绝对路径**，因此该产物**跨 checkout 路径不可逐字节复现**；… | nomask_assert.json 只记录与路径无关的摘要（例如 stderr 去掉 File 行、或记录 stderr 规范化后的 sha256），或把该产物移出 artifact_manifest 的 sha256 覆盖范围；另可在 selfcheck 里先备份/后还原被写产物，使自证清单幂等… |
| S4-3 | suggestion | registry 与 report.html 的「独立审查」留痕只写**第一轮**（2 blocking + 3 suggestion + 4 nit，留痕 review_round1.json），完全不提第二轮与第三轮… | 把 ⑥ 子句改成按 experiments/WORK-555/review_round*.json 实际份数与各轮 verdict 动态生成（与 work555_final_comment.py 的做法一致），并在本轮 review_round4.json 落地后自动入列。… |
| S4-4 | suggestion | 精度门禁的 --check 只**读**已落盘的 float32_quant_diag.json（信任其中的 n_over_tol / model_audit_identical 字段），不重算；其 PASS 文案写「全… | §15 与 --check 的 PASS 文案改成「6/8 臂/种子的相对差为 0.0；xgbfull 两臂未参与该比较（口径差异）」，并让 --check 复算而不是只读字段；对 xgbfull 采用与其真实建模脚本一致的秩函数后再纳入比较。… |
| N4-1 | nit | 正文称「8 个臂/种子的「产物复算值 vs 建模侧报告值」相对差**全部为 0.0**」，而同一段落下方表格里 xgbfull 两行为 n/a、「（诊断臂无该字段）」。措辞与自己的表打架。… | 改为「6 个臂/种子的相对差为 0.0（4 臂中 dee/mlp_only/xgb 共 6 个臂/种子参与比较），xgbfull 2 行未参与」。… |
| N4-2 | nit | 交办书期望「新口径数值应与上一轮逐位相同，只是产物精度变了」，实测**并非全部相同**：dee_s43 的 test_rankic_mean / test_ic_mean 与其回测指标在 v3→v4 之间变了（0.023… | 无需处置；建议交办书/结论文档把「建模侧数值一个字节不变」的表述限定为「float64 落盘这一步本身不改变指标计算；v3→v4 的 dee_s43 变化来自 S2 早停修复经全量重跑落到产物」。… |
| N4-3 | nit | 同一个量「独立复算检查项数」在同一文件里出现两个值：§12 的历史叙事行写「独立复算 **PASS，blocking 0，261 项**」，§16 写「blocking 0 项 / 共 318 项检查项」。§12 那行属… | 把 §12 的历史行加上显式时点标注（如「（2026-11-24 14:15 当时）」），或把小节标题里的「最终」改为「阶段」，避免与 §12.2-§12.4 的现值生成块混读。… |

留痕：`experiments/WORK-555/review_round4.json`

**第 5 轮**（增量复审范围 `30215ed..83dc1ba（含处置 commit 6a3eab9 与产物重出 commit 83dc1ba）`）：判决 **PASS**

> 0 blocking + 2 suggestion + 2 nit。第四轮两项 blocking **均已关闭**：B4-1（xgbfull 秩口径分歧 + 审计零覆盖）—— 三个秩实现（work555_dee._rank / work555_xgb_diag 用的 _rank / audit._rankdata）在重并列样本上**逐位相同**；float32_quant_diag.json 8 个臂/种子 rel 全部 0.0、model_audit_identical=True；audit_findings.json 新增 ic::xgbfull_s{42,43}::test_ic_mean_best_iter，项数 318→320 的增量**只有**这 2 项、无删项、TOL 仍 1e-6、预注册逐字节未改；全仓库已**不存在**xgbfull RankIC 的第二个值（0.024601 / 0.022910 在 report.html / failure_log.md / registry.md / final_numbers.json / final_comment.md / run_*.json / audit_findings.json 全部一致）。B4-2（registry ⑤ 与 §12.2 相反结论）—— registry ⑤ 现按 primary.rankic_unstable_mlp 实测分支渲染，与 §12.2 **同源同结论**。四项 suggestion：S4-1 已闭合（selfcheck 自派生 findings[] 并与 n_blocking 互校，负控变红），残留 final_numbers --check 仍只读计数器（S5-2）；S4-2 已闭合（连续两次 selfcheck 均 exit 0、清单检查两次 exit 0、nomask_assert.json 哈希不变，且新增 stderr_sha256_normalized 跨路径逐位相同）；S4-3 已闭合（registry/report/comment 均按 review_round*.json 动态列出 4 轮，轮次分级计数与各轮文件实测一致）；S4-4 已闭合（--check 改为重算并与落盘逐行比对，篡改 rel_diff_func_vs_reported 即红）。三项 nit 全部处置（§15 表 8 行全 0.0、§15.4 加口径限定、§12 的 261 项加时点标注、新增 §16）。**本轮无 blocking**：交付件之间 195 项同量对齐零不一致，提交态自证清单 11/11 全过，审查方自带复算 352 项 0 blocking，判词「未复现（退）」仍由预注册 §6.2 机械得出。遗留的 2 suggestion + 2 nit（其中 S5-2 即第四轮 S4-1 的残留）均不触及判词。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| S5-1 | suggestion | 提交态（83dc1ba）的**交付评论**顶部 provenance 段写「本次交付的全部数字来自 v4 重跑后的产物；**中途重跑过两次**……两次的原因与影响见下文「独立审查」段与 `failure_log.md` … | ① 提交态生成器 scripts/work555_final_comment.py:102 的 provenance 段改为与 §16 一致（「最新一轮收口 / 中途重跑过三次 / 见 §13/§15/§16」）—— 工作区已有此改法，提交即可；② 给 work555_final_comment.p… |
| S5-2 | suggestion | 第四轮 S4-1 的处置只落在 `scripts/work555_selfcheck.sh`（已闭合，见 disposition S4-1）；`scripts/work555_final_numbers.py --che… | work555_final_numbers.py 的 collect() 里增加：n['audit']['n_blocking_derived'] = sum(1 for f in au.get('findings', []) if f.get('severity')=='blocking')；--… |
| N5-1 | nit | 为 import work555_dee 而插入的 sys.path 逻辑把 `HERE = os.path.dirname(os.path.abspath(__file__))` 写了两遍（43 行、52 行），后一次… | 删掉 43 行那次重复赋值（保留 52 行），或把 import 块移到常量区之后。… |
| N5-2 | nit | B4-1 统一了**秩**口径（Spearman 侧已逐位一致），但 **Pearson IC** 侧该臂仍走独立实现：`pearson_ic_by_day` 对 float32 标签数组直接做 `t.std()`（fl… | work555_xgb_diag.pearson_ic_by_day 里把标签升到 float64（`ys = np.asarray(y, dtype='float64')`）或改用 np.corrcoef，与其余臂口径统一；预计 gap 收敛到 ~1e-16。… |

留痕：`experiments/WORK-555/review_round5.json`

**第 6 轮**（增量复审范围 `83dc1ba..7e026fb（处置 + 产物重出单 commit；含 scripts/work555_final_comment.py / work555_report.py / work555_final_numbers.py / work555_selfcheck.sh / work555_finalize_v4.sh / work555_xgb_diag.py / audit/recompute_work555.py 与产物重出）`）：判决 **PASS**

> 0 blocking + 1 suggestion + 1 nit。第五轮遗留 4 项（S5-1 / S5-2 / N5-1 / N5-2）经审查方独立验证全部闭合：① 交付评论 provenance 段已改为「最新一轮收口 / 中途重跑过三次 / 见 §13/§15/§16」，与 §13/§15/§16 三节一一对应、与正文 xgbfull 数值（+0.024601 / +0.022910）一致；新增的 work555_final_comment.py --check 与 work555_report.py --check 两道漂移闸门经审查方负控实测真的会红（改落盘评论一行 → exit 1；改 report.html 一个字符 → exit 1），自证清单在 pristine2 沙箱实测 13 项全过、exit 0，且连续两次跑均 exit 0、产物清单 44/44 逐份 sha256 一致、nomask_assert.json 哈希未变（幂等）；② work555_final_numbers.py 的 --check 已从 findings[] 派生 n_blocking_derived 并与计数器互校，审查方负控（把某条 finding 的 severity 改成 blocking 而 n_blocking 留 0）exit 1（第五轮此处仍 exit 0），反向负控（n_blocking 改 5、数组 0 条 blocking）同样 exit 1；③ work555_xgb_diag.py 的重复 HERE 赋值已删（提交态只剩 scripts/work555_xgb_diag.py:43 一处）；④ 该臂 Pearson IC 标签升 float64 后，audit_findings.json 的 ic::xgbfull_s42/s43::test_ic_mean_best_iter 两项 gap 均为 0.0（逐位相同），run_xgbfull_*.json 与审计复算值逐位一致，preds_xgbfull_*.npz 哈希前后不变（e1f8b191… / e3e982ac…）。§17.3 三问的独立回答：审计脚本 diff 只有一个 hunk（audit/recompute_work555.py:437 elif d > 0: → else:），TOL=1e-6（第 48 行）逐字未动、add( 调用数 14=14、findings.append 8=8、blocking 分支逐字未动，不属放宽判据、不属删检查；项数在新版下只取决于覆盖面（构造 gap 恰好 0 的场景实测项数仍 330，旧版同数据掉到 317）；无新的 blocking 判据被削弱（构造 gap≈1e-3 的场景，新旧两版都报 n_blocking=1 / verdict=BLOCKING，blocking item 同一条）。本轮无 blocking：审查方自写复算 352 项 0 blocking、跨交付件同量对齐 195 项零不一致、提交态审计重跑与提交逐条一致（330 项、0 blocking）、判词「未复现（退）」仍按预注册 §6.2 机械得出且判词相关量在本增量内逐字节未改。唯一遗留 S6-1：三份交付件硬编码「全部判 FAIL」，与同句里「第 5 轮 PASS」自相矛盾（叙述性，不触及判词）；N6-1：provenance「中途重跑过三次」未计入 §17.4 记录的第四次收口重跑。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| S6-1 | suggestion | 三处生成器把「全部判 FAIL」写死，而轮次是动态的（len(_rounds)）。第 5 轮判决是 PASS，因此这句话在本增量里变成了假的，且与同一句里紧接着列出的「第5轮 PASS」直接冲突（同一交付件内部自相矛盾）… | 把三处硬编码改为按实际轮次动态生成（例如统计 verdict 为 FAIL 的轮数，写成「5 轮，其中 4 轮判 FAIL、第 5 轮判 PASS」），或直接删去「全部 FAIL」这类总结性措辞，只保留逐轮判决列表；改完后重跑 work555_final_numbers.py --check / w… |
| N6-1 | nit | provenance 段枚举的三次重跑对应 §13（S2/S3 修复重跑）、§15（float32 无损落盘重跑）、§16（第四轮处置重跑），三节一一对应，引用无误；但 §17.4 记录了本轮（第五轮处置）又跑了一次完整… | 把「中途重跑过三次」改成不含次数的表述（如「中途因 FAIL 驱动的重跑见 §13/§15/§16，最新一轮收口见 §17」），或补上第四次（§17）的口径；改完重跑三道 --check 闸门与自证清单。… |

留痕：`experiments/WORK-555/review_round6.json`

**第 7 轮**（增量复审范围 `7e026fb..8ec570b（第六轮处置 + 产物重出单 commit；含 scripts/work555_final_comment.py / work555_report.py / work555_final_numbers.py 与产物重出；未改动 audit/recompute_work555.py、scripts/work555_selfcheck.sh、任何 run_*.json / backtest_*.json）`）：判决 **PASS**

> 0 blocking + 1 suggestion + 1 nit。第六轮遗留 2 项处置经审查方独立验证：S6-1 **已闭合** —— 三处生成器（work555_final_comment.py:259-266、work555_report.py:803-810、work555_final_numbers.py:542-544）改为按盘上 review_round*.json 的 verdict 动态统计；审查方在沙箱把 review_round3.json 的 verdict 由 FAIL 改成 PASS 后重生成三份交付件，comment 标题随即变为「6 轮，其中 3 轮判 FAIL、3 轮判 PASS」、report 变为「共 6 轮，其中 3 轮判 FAIL、3 轮判 PASS」、registry 变为「6 轮，其中 3 轮判 FAIL」；第二次翻转后计数再次同步，全仓库扫描已无「全部 FAIL」式硬编码渲染残留（仅剩问题描述引文与代码注释）。N6-1 **已闭合** —— provenance 段改为不含次数的表述（「因 FAIL 驱动的重跑见 §13/§15/§16，最新一轮收口见 §17」），§13/§15/§16 三节实测存在且与所枚举的三个 FAIL 驱动重跑一一对应。附带诚实性修正 **已核实为真**：两条 --check 提示语单位由「B」改为「字符」（负控实测输出「落盘 20,104 字符 vs 重生成 20,100 字符」「落盘 1,330,662 字符 vs 重生成 1,330,661 字符」，均 exit=1）；failure_log §17.2 表格把 15,263/17,743 两个数字改为「字符」并加注「这两个数字是字符数不是字节数，且当时的落盘状态不在任何 git 版本里，无法被第三方复现 —— 故此处按『生产者侧日志记录』标注」，未把不可复现的记录说成可复现。本轮无 blocking：提交态 pristine 沙箱自证清单 **13/13 全过且连续两次 exit 0**（幂等）、产物清单 44/44 逐份 sha256 一致、审计重跑与提交 **逐条一致**（330 项、0 blocking、severity 全 pass、ic::* 16 项其中 12 项 gap 恰为 0.0）、审查方自写复算 352 项 0 blocking、跨交付件同量对齐 195 项零不一致；判词「未复现（退）」仍按预注册 §6.2 机械得出，且判词相关量在本增量内 **逐字节未动**（8 份 run_*.json 与抽查的 backtest_*.json 完全相同；final_numbers.json 剔除时间戳类字段后逐字段相同）。本轮新增 1 suggestion + 1 nit，均属叙述指认层：S7-1 provenance 的「最新一轮收口…见 §17」在本增量又跑一次收口后指向陈旧（最新收口对应 §18，且 §18 未记录本轮复跑）；N7-1 registry ⑥ 只给 FAIL 计数、未给 PASS 计数，与生产者自陈「三份均渲染『…2 轮判 PASS』」不符（registry 文本本身为真）。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| S7-1 | suggestion | N6-1 的处置把 provenance 的次数口径去掉了，但替代表述里「最新一轮收口（含第五轮处置）见 §17」是**写死的章节号**，而本增量本身又跑了一次收口（交付件全部重出），因此「最新一轮收口」现在对应的是第六… | 把 provenance 末句改为不写死章节号（如「最新一轮收口见 `failure_log.md` 末节」），或让生成器按 `failure_log.md` 实际存在的最大 § 编号动态填；同时在 §18 补一节「§18.3 处置后的复跑结果（实测）」，把本轮收口的时间戳（清单 22:00:58 … |
| N7-1 | nit | registry 的 ⑥ 子句按实际判决动态生成了 FAIL 计数（不再硬编码「全部判 FAIL」，这一半已修好），但只给 FAIL 计数、**不给 PASS 计数**，且逐轮只列分级计数、**不列该轮判决**。于是单看… | registry 的 ⑥ 子句按 comment/report 同一口径补上 PASS 计数（如「6 轮，其中 4 轮判 FAIL、2 轮判 PASS」），三处用同一段渲染逻辑，避免三份交付件口径不一；改完重跑 final_numbers --check 与自证清单。… |

留痕：`experiments/WORK-555/review_round7.json`

**第 8 轮**（增量复审范围 `8ec570b..6f2ad07（第七轮处置 + 产物重出单 commit；仅改 scripts/work555_final_comment.py / scripts/work555_final_numbers.py 与文字层产物；未改动 audit/recompute_work555.py、scripts/work555_report.py、scripts/work555_selfcheck.sh、任何 run_*.json / backtest_*.json）`）：判决 **PASS**

> 0 blocking + 1 suggestion + 1 nit。第七轮遗留 2 项处置经审查方独立验证全部闭合：S7-1 **已闭合（实验确认）** —— scripts/work555_final_comment.py 新增 `_latest_section_ref()`，扫盘上 failure_log.md 的 `^## §(\d+)` 取实际最大章节号；审查方在沙箱做了三步实验：基线渲染「§19」→ 追加 `## §20 …` 后重生成变为「§20」→ 删掉 §20 回落「§19」→ 再删掉 §19 回落「§18」，指针完全跟随盘上文件，无写死。N7-1 **已闭合** —— 三份交付件的审查轮次段现为同源同值：轮数 7、FAIL 4 / PASS 3，逐轮判决与分级计数与盘上 7 份 review_round*.json 逐项一致；report 与 registry 的逐轮列表逐字相同，comment 标题口径一致；§19.4 的留痕索引 7 行与盘上真值逐行一致。**§19.3「处置后的复跑结果（实测）」逐项核实为真**：精度诊断 8/8 行 rel=0.0、审计 PASS/blocking 0/330 项、Δ_ic 复算值 −0.2914861368509015 与建模侧逐位一致、8 臂回测 n_guard_violations 全 0、不可成交断言 n_combinations=8/n_triggered=8/all_triggered=true、权重不变量 16 项 max Σw=1.0、n_days_over_1=0、ledger_max_dev=5.551115123125783e-16、自证清单 13/13 exit 0、清单 44 份逐份 sha256 一致、快照 6/6 MATCH —— 唯一不符项是本轮收口时间戳：§19.3 写「（2026-11-24 22:2x）」，而本轮产物的 generated_at 实为清单 22:09:31 / 审计 22:09:51 / 数字 22:09:51（见 S8-1）。本轮无 blocking：提交态 pristine 沙箱自证清单 13/13 全过且连续两次 exit 0（幂等）、审计重跑与提交逐条一致（330 项、0 blocking、项集合相同、共有项差异 0）、四道 --check（final_numbers / artifact_manifest / comment / report）在干净沙箱全部 exit 0、审查方自写复算 352 项 0 blocking、跨交付件同量对齐 195 项零不一致；判词「未复现（退）」仍按预注册 §6.2 机械得出，判词相关量在本增量内逐字节未动（`git diff --name-only 8ec570b 6f2ad07` 不含任何 run_*.json / backtest_*.json；final_numbers.json 剔除时间戳类字段后逐字段相同）。本轮新增 1 suggestion + 1 nit，均属叙述/记录层：S8-1 §19.3 的收口时间戳与产物时间戳不符；N8-1 §19.2 的 N7-1 处置 cell 引用的样式举例仍用上一轮的「6 轮…第6轮 PASS」，与本轮实际渲染「7 轮…第7轮 PASS」不同。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| S8-1 | suggestion | §19.3 是本轮唯一标「实测」的复跑记录，其内容（精度诊断、审计项数、Δ_ic、guard、不可成交断言、权重不变量、自证 13/13、四道 --check、清单 44 份、快照 6/6）经审查方逐项复核**全部为真*… | 把 §19.3 的时间改为与产物一致的精确值（如「2026-11-24 22:09:31~22:09:51」，或直接写「本轮收口时间见 `artifact_manifest.json` 的 `generated_at`」）；若刻意不写分钟数，则改成「（2026-11-24 22 时，见产物 `gen… |
| N8-1 | nit | 该处置 cell 用**上一轮的数字**作为「改成什么样式」的引文，而本轮实际渲染已是「7 轮，其中 4 轮判 FAIL、3 轮判 PASS（第1轮 FAIL（…）；…；第7轮 PASS（…））」。作为处置说明的**格式… | 把 §19.2 的 N7-1 处置 cell 的引文改成不带具体轮数的格式模板（如「N 轮，其中 M 轮判 FAIL、K 轮判 PASS（第1轮 <判决>（…）；…）」）或补上时点标注；改完重跑 final_numbers --check 与自证清单。… |

留痕：`experiments/WORK-555/review_round8.json`

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
bash scripts/work555_selfcheck.sh        # 自证清单：13 项一次跑完
bash scripts/work555_run_arms_v4.sh      # 4 臂 × 2 种子重跑（产物来源，float64 落盘）
bash scripts/work555_finalize_v4.sh      # 回测→产物清单→复算→图→报告→PNG/PDF→自检
/tmp/w555venv/bin/python scripts/work555_float32_quant_diag.py   # 产物精度诊断
/tmp/w555venv/bin/python audit/recompute_work555.py            # 独立复算闸门
/tmp/w555venv/bin/python scripts/work555_final_numbers.py --check  # 对外数字陈旧自检
/tmp/w555venv/bin/python scripts/work555_artifact_manifest.py --check  # 产物清单复核
```

**线程固定是复现前提**：实测表明线程数会改变训练数值结果（`scripts/work555_thread_sensitivity.py`：同代码 / 同 seed / 同 60 步，T=1 与 T=4 的最优快照集成级验证损失相对差 3.569e-05），不固定线程时同命令同 seed 结果不可复现。该机制的根因未被单独隔离验证，表述只到「线程数改变数值结果」为止，详见 `failure_log.md` §14。

