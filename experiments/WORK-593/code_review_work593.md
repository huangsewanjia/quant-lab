# WORK-593 代码与口径审查（独立审查方）

> 审查依据：仓库 skill `skill`（代码审查规范）。该 skill 指定的是前端/后端项目路径（`backend/tests`、`frontend`），
> 本仓库无这些目录，故按其中的**分级口径**（blocking / suggestion / nit + 每条「位置 / 问题 / 为什么 / 改法」）
> 执行，并按派单要求补「实测值 / 验证命令」。
> **纪律**：本审查不改被审代码、不动 git。仅新增本文件；临时脚本与备份已删除。

---

## 1. 审查对象

| 项 | 值 |
|---|---|
| 仓库 | `/srv/data/multica/workspaces/workspace-c8faa36a018a/work-593-25ff1592b35d/workdir/quant-lab` |
| 被审提交 | `968c8d1e7143d454469b196c064c679be3858dcf`（父 `a7483bb`，预注册 v1.0 为 `1c8eaac`） |
| 审查期间新增提交 | `bf07084`（2026-11-26 15:52，仅改 `report.html` 的 C4 实测列与 `registry.md` 的 12.9%→12.1%）。**本文所有问题均在 `bf07084` 的 HEAD 树上复核过，仍存在**（除已注明「已修」的一条） |
| 代码基线 | 审查开始/结束时 `git status` 均无未提交改动（只有我造的 `audit_findings_firstrun.json`，已删） |
| 解释器 | `/home/moyongkai/.venvs/quantdb/bin/python` → Python **3.14.4**；pandas 3.1.1 / numpy 2.5.5 / duckdb 2.0.1 / pyarrow 26.0.1 |
| 数据快照 | `data/WORK-593/`（基础面板 6,156,584 行；三套表示各 6,156,584 行；掩码 1,862 只 / 2,505 段） |

审查文件（全部通读全文，非只读 diff）：`experiments/WORK-593/{preregistration,preregistration_v1.1,failure_log}.md`、
`scripts/work593_{fetch,represent,audit,report}.py`、`audit/recompute_work593.py`、
`data_contracts/field_dictionary.md` §8、以及 `experiments/WORK-593/` 下全部 JSON/CSV 与 `report.html`。

---

## 2. 独立复跑结果

### 2.1 指定复跑（`audit/recompute_work593.py`）

```
cp experiments/WORK-593/audit_findings.json experiments/WORK-593/audit_findings_firstrun.json   # 备份（正式交付物）
/home/moyongkai/.venvs/quantdb/bin/python audit/recompute_work593.py
```

| 项 | 结果 |
|---|---|
| 退出码 | **0**（无 blocking） |
| 用时 / 峰值内存 | 4:25.4 / 3.79 GB RSS |
| 复算 ΔIC_pool | **−0.0017495753265477426**（交付值 −0.0017496337910846967，绝对差 **5.85e-8**，相对差 **3.3e-5**） |
| 复算 IC_pool_C | −0.02873501269682741（交付 −0.02873499398686589，绝对差 1.9e-8） |
| 复算 ratio | 0.06088653396491837（交付 0.060888608220499865，相对差 3.4e-5） |
| 复算判词 | 未复现（与交付一致） |
| 逐因子 144 项 | 最大绝对差 **1.78e-6**（INV25 ΔIC，符号翻转：−1.20e-6 → +5.78e-7）、最大相对差 **7.25** |

### 2.2 两次 `audit_findings.json` 是否一致

**逐字节一致。** 复跑前后 sha256 均为 `20f910668c60b6b5c954988a542185ea446235e008d54ca0a83306f740856229`；
`audit_factor_compare.csv`（`6a9036…`）与 `audit_ci_compare.csv`（`1cae2e…`）同样逐字节一致。已按要求
`cp` 回 firstrun 备份并删除 `audit_findings_firstrun.json`（因两文件本已相同，工作树最终仍干净）。
⇒ **交付物可重放、非人工拼接**。

### 2.3 附加独立抽查（一次性脚本，已删）

| 抽查 | 结论 | 实测 |
|---|---|---|
| `close_E·f_E == close_C·f_C`（随机 3 日 × 3 股 + 全量） | **成立** | 全量 6,156,584 行最大相对差 **4.79e-16**；样本逐值打印一致 |
| `vwap_E·vol_E == vwap_raw·vol` | **成立** | 样本相对差 0 ~ 2.2e-16；全量见 C2：违规 0，max 4.1e-16 |
| bootstrap 换种子 | **敏感但符号稳定** | 独立重写同算法：种子 42/43/44 复现交付 CI（[−0.002491,−0.001062] 等，6 位小数一致）；另加种子 7/123/999/2026 → CI 移动 ~1e-4，**全部不含 0 且整段为负**；块长 10/20/40 亦然 |
| 标签跨表示一致 | **成立** | 交付 `report_data.json` 1.78e-15；复算侧独立算得同一量级 |
| 前视 | **无** | 36 条表达式只出现 `shift(+k)`/`rolling`；`shift(-20)` 仅用于标签；IC 与标签同用 `M[t]` 时点掩码 |
| 幸存者偏差 | **无** | 1,862 只全并集；126 只末次交易 <2026-11-01（最早 2010-02-03）仍在面板与掩码内；掩码按快照 s→下一快照 −1 生效 |
| 主检验量是否真是因子级均值 | **是** | 36 条因子 `n_days` 全为 3832（同一批有效日），故 `mean_t d_t` 与 `mean_f ΔIC_f` 等价：`metrics_factors.csv` 的 `mean(ΔIC) = −0.0017496337911247306` vs 交付 −0.0017496337910846967（差 4e-14） |

---

## 3. 分级问题表

### 3.1 blocking（4 条，全部不改变「未复现」这一数字结论，但必须先修）

#### B1 · 独立复算的 materiality 判据被改成「绝对差 1e-5」，未登记偏离，且取值超实际需要 5.6 倍

- **位置**：`audit/recompute_work593.py:42`（`ABS_FLOOR = 1e-5`）、`:45-53`（`diff_ok`）、`:344`（自相矛盾的 expected 文本）、`:399-400`（写进 JSON 的 `tol_rule`）
- **触发条件与后果**：`diff_ok` 对**每一个**对外数字（144 项逐因子 + 主检验量 3 项 + CI 6 项）生效。预注册 §8 写死「独立复算与建模侧**相对差** > 1e-6 → 该项判未通过，以复算值为准」；`failure_log.md` D4 落盘的是「相对 1e-6 **或绝对 1e-9**」。代码实际用的是**绝对 1e-5**。后果：`audit_findings.json` 的 `can_enter_memo: true` 与报告 §8 的「blocking = 0」建立在一个**既非预注册、也非失败日志记载**的放宽判据上。
- **实测值**：
  - 按预注册规则（rel ≤ 1e-6）：**25/144 项不通过**（INV08/09/10/11/12/13/25、PL06/07/08/09…）；
  - 按 failure_log D4 记载的规则（rel 或 **abs ≤ 1e-9**）：**同样 25 项不通过** —— 落盘的 1e-9 分支**救不了任何一项**；
  - 主检验量本身按预注册规则也不通过：ΔIC_pool 相对差 **3.3e-5**、ratio 相对差 **3.4e-5**；
  - 全部 144 项的**最大绝对差只有 1.78e-6**（= INV25 ΔIC），故最小可用 floor 是 **2e-6**；代码的 1e-5 是它的 **5.6 倍**，等于主检验量 1.75e-3 的 **0.57%**；
  - 代码注释的理由「IC 报 5 位小数，1e-5 即最小可判差」**与事实不符**：比对读的是 `metrics_factors.csv`，`float_format="%.10g"`（10 位有效数字，小量级 IC 分辨率约 1e-12）；5 位小数只出现在 HTML 表格里。
- **判定（派单重点项）**：**属「程序性放水」，不属「数量性放水」**。差异来源可完整归因、无一来自实现分歧——INV25 的 1.3e-6 来自 TS_RANK 只有 21 个离散取值时的并列平均秩放大，PL06/07/08 的 1e-7 来自 cumsum 与 pandas rolling 的累加顺序；绝对值层面 1.78e-6 「确实等价」。但**改判据这件事没有落盘**（v1.1 偏离表只有 D1–D5，缺这一条），且取值没有按实测需要推导。另外绝对差判据天然会把「CI 边界在 0 附近换号」判成一致，对 C2（CI 不含 0）是结构性风险（本单 CI ≈ −1e-3，未触发）。
- **对 INV25 / INV16/17 / 主检验量的实际影响**：

  | 项 | 最大相对差 | 能否过预注册 rel 1e-6 | 是否依赖 1e-5 floor |
  |---|---|---|---|
  | INV16 / INV17（D5 修复后） | **3.3e-10 / 3.3e-10** | 能 | **否**（D5 有效，已回到浮点噪声） |
  | INV25（ΔIC） | **1.48**（符号翻转） | 不能 | **是** |
  | 主检验量 ΔIC_pool / ratio | 3.3e-5 / 3.4e-5 | 不能 | **是** |

- **可执行改法**（二选一，推荐 A）：
  - A：`ABS_FLOOR = 2e-6`（= 实测最大绝对差 1.78e-6 × 1.12 余量，注释写清推导），并在 `preregistration_v1.1.md` 偏离表补 `D6`：「materiality 加绝对差分支，理由 = 数值零量级/并列平均秩放大，实测最大绝对差 1.78e-6，取 2e-6」；
  - B：保留 1e-5，但（i）补 `D6` 登记；（ii）`audit_findings.json` 同时输出「按预注册 rel 1e-6 的通过/不通过」两份结果；（iii）把 `:344` 的 expected 文本改成与 `tol_rule` 一致。
- **验证命令**：
  ```
  /home/moyongkai/.venvs/quantdb/bin/python - <<'EOF'
  import pandas as pd
  c=pd.read_csv('experiments/WORK-593/audit_factor_compare.csv'); c['ad']=(c.claimed-c.recomputed).abs()
  t=1e-6*c[['claimed','recomputed']].abs().max(axis=1)
  print('rel1e-6 fail',int((c.ad>t).sum()),'| rel1e-6 or abs1e-9 fail',int(((c.ad>t)&(c.ad>1e-9)).sum()),'| max abs',c.ad.max())
  EOF
  ```

#### B2 · 报告与失败日志声称「停牌日保留行 + 价格前向填充」，流水线实际没有做（预注册 §4 未实现）

- **位置**：`scripts/work593_report.py:521`（§10「停牌日按前向填充保留行（字段字典 §5）」）、`scripts/work593_fetch.py:151-156`（`base = dl.merge(af…`，无 ffill、无补行）、`experiments/WORK-593/failure_log.md` §5
- **触发条件与后果**：预注册 §4「停牌/缺行」明确要求「停牌日**保留行**、价格**前向填充**、成交量置 0」。`panel_base.parquet` 里一条这样的行都没有：`vol == 0` 行数 = **0**，全量 7,623,028 个「日历 × 股票」单元格只有 6,156,584 行（**19.24% 缺失**）。后果有二：① 研究备忘录若照抄报告 §10，会陈述一个**没有发生的**数据处理；② 因子实际看到的是「NaN 缺口 + `rolling(min_periods)` 自动缩短窗口」，与「stale 价 + 量 0」在横截面成员、窗口长度、INV25 秩上都不等价，属未登记协议偏离。
- **实测值**：成员股票-日里就有 **79,315 / 3,260,000（2.43%）** 没有行（= 审计 `membership_cells` 3,260,000 − manifest `mask_stock_days` 3,180,685，两者定义不同，见 U2）；**涉及 1,316 只股票，其中位缺口 26 个成员日** —— 这不是零星退市尾段，而是普遍存在的中途停牌。样本例：`600978.SH` 在 2020-07-15 有行、2021-07-14 与 2025-12-04 无行。
- **可执行改法**：二选一——（i）按 §4 实现：`work593_fetch.py` 用交易日历 × 个股重建行、价格 ffill、`vol=0`、`vwap_raw=NaN`，重跑全链（会改动全部数字，需登记并复跑复算）；（ii）不改代码，则**必须**把 §10 与 failure_log §5 改成「停牌/退市缺行按 NaN 剔除，未前向填充（偏离预注册 §4；方向：窗口有效值不足时置 NaN，影响 4.43% 的 INV25 单元格）」，并在 v1.1 偏离表登记。
- **验证命令**：
  ```
  /home/moyongkai/.venvs/quantdb/bin/python -c "
  import pandas as pd; b=pd.read_parquet('data/WORK-593/panel_base.parquet',columns=['ts_code','trade_date','vol'])
  print('rows',len(b),'vol==0',int((b.vol==0).sum()),'cells',b.trade_date.nunique()*b.ts_code.nunique())"

  # 成员股票-日里缺行的精确计数（向量化，~2s）：期望 3,260,000 / 3,180,685 / 79,315
  /home/moyongkai/.venvs/quantdb/bin/python - <<'EOF'
  import pandas as pd, numpy as np
  b=pd.read_parquet('data/WORK-593/panel_base.parquet',columns=['ts_code','trade_date'])
  mem=pd.read_parquet('data/WORK-593/membership.parquet')
  dates=np.sort(b.trade_date.unique()); bycode={c:np.sort(g.trade_date.to_numpy()) for c,g in b.groupby('ts_code')}
  cal=pres=0
  for code,s,e in zip(mem.ts_code,mem.start_date,mem.end_date):
      cal+=int(np.searchsorted(dates,e,'right')-np.searchsorted(dates,s,'left'))
      d=bycode.get(code); pres+=0 if d is None else int(np.searchsorted(d,e,'right')-np.searchsorted(d,s,'left'))
  print(cal,pres,cal-pres)
  EOF
  ```

#### B3 · 报告图 3 图注把「价格水平型整体为正」写成事实，实测 11 条全为负

- **位置**：`scripts/work593_report.py:468`（渲染进 `experiments/WORK-593/report.html`）
- **触发条件与后果**：图注称「ΔIC 按组排序：价格水平型（蓝）**整体为正**、比值型（青）集中在 0 附近，与论文……一致」。同一页 §5 表格写 A 组 ΔIC = −0.005726，图形本身画的是 `dIC_E_minus_C` 的负值柱。读者（尤其只看图的人）会把主结果方向读反。
- **实测值**：`metrics_factors.csv` 的 11 条 PL ΔIC = −0.005863 / −0.005858 / −0.005842 / −0.005876 / −0.005857 / −0.005799 / −0.005869 / −0.005997 / −0.004400 / −0.005763 / −0.005863，**正数 0 条**，均值 −0.0057261。
- **可执行改法**：改为「价格水平型（蓝）**整体为负**（A 组 −0.0057，11 条全负）、比值型（青）集中在 0 附近（B 组 +3.1e-8）：同一份漂移在 A、B 两组符号不同，与论文『十个裸字段之外均值接近零』只在 B 组上一致」。
- **验证命令**：`grep -o "整体为正" experiments/WORK-593/report.html`，与 `metrics_factors.csv` 的 `dIC_E_minus_C` 列对照。

#### B4 · 预注册 §12 点名的两项表格交付物不可核验：`paper_comparison.csv` 不存在、`metrics_factors.csv` 未入库

- **位置**：`experiments/WORK-593/preregistration.md:310`（交付映射第 7 行）、`.gitignore`（`*.csv` 全局忽略；本单只加了 `report.html/png/pdf/figures` 白名单，未加表格）
- **触发条件与后果**：① `paper_comparison.csv` **在仓库里根本不存在**（`ls experiments/WORK-593/` 无此文件；`scripts/work593_report.py` 只把它内联进 HTML 的 §7 表）。交付映射第 7 行无法验收。② `metrics_factors.csv`（36 条因子全部对外数字的来源）被 `*.csv` 忽略，`git ls-files experiments/WORK-593` 不含它——仓库接收方拿不到因子级证据，`audit_factor_compare.csv` / `audit_ci_compare.csv` 同理。WORK-449 用显式白名单解决过同一问题（`.gitignore` 有 `!experiments/WORK-449/paper_comparison.csv`），本单没照做。
- **实测值**：`git ls-files experiments/WORK-593` = 15 个文件，无任何 `.csv`；`ls` 显示 20 个文件（含 4 个 CSV）。
- **可执行改法**：`scripts/work593_report.py` 增加 `paper_comparison.csv` 落盘（把 `paper_rows` 直接 `to_csv`）；`.gitignore` 的 WORK-593 例外块补 `!experiments/WORK-593/metrics_factors.csv`、`!experiments/WORK-593/audit_factor_compare.csv`、`!experiments/WORK-593/audit_ci_compare.csv`、`!experiments/WORK-593/paper_comparison.csv`；重跑报告脚本后把四个 CSV 一起提交。
- **验证命令**：`git ls-files experiments/WORK-593 | grep -c csv`（应为 4）。

### 3.2 suggestion（7 条）

#### S1 · 成分段结束日改用「两个指数的并集快照日历」，把一个指数的区间耦合到另一个指数的快照上

- **位置**：`scripts/work593_fetch.py:200-209`（`cal_all` / `next_of`），对照 `:198-199` 的 per-index `snaps`
- **触发条件与后果**：`next_of` 由 `iw` 里**全部** `index_code` 的 `trade_date` 并集构造，于是 A 指数每段成分区间的结束日 = **任意一个指数**的下一个快照日 − 1。若 CSI300 与 CSI500 的快照日错开（一份月初、一份月中），A 的成分会被 B 的快照日**提前截断**，掩码静默缩小。首版实现用的是 per-index `next_snap`（正确），本提交改成了并集日历。
- **实测值**：**当前数据未触发**——两个指数各 202 个快照日、`300-only = 0`、`500-only = 0`，完全相同。潜在缺陷，非现存偏差。
- **可执行改法**：在 `for idx, g in iw.groupby("index_code")` 里用该指数自己的 `snaps` 造 `next_of`；顺带把 `mask_cells`（`:226-231`，逐段扫全表 2,505 次）改成一次 `groupby`。
- **验证命令**：`/home/moyongkai/.venvs/quantdb/bin/python -c "import pandas as pd;iw=pd.read_parquet('data/WORK-593/raw/index_weight.parquet');a=set(iw[iw.index_code=='000300.SH'].trade_date);b=set(iw[iw.index_code=='000905.SH'].trade_date);print(len(a),len(b),len(a-b),len(b-a))"`（当前 202 202 0 0）。

#### S2 · `_ts_rank` 在窗口不满 20 个有效值时不置 NaN，而返回被压低的秩；复算脚本同缺陷 ⇒ 审计结构上看不见

- **位置**：`scripts/work593_audit.py:106-110`（`_ts_rank`）、`:156`（INV25）；`audit/recompute_work593.py:161-168`（`ts_rank_last`）
- **触发条件与后果**：`Σ_k 1{x_t ≥ x_{t−k}}/n`（k=0..19）与「窗口内 ≤ 当前值个数 / n」在**满窗**时严格等价（逐值验证 `max|diff| = 0.0`）；但窗口内有 NaN 时比较结果为 False、分母仍是 20，于是：① 预热期/停牌后复牌日的秩被系统性压低（真值应为 NaN 或按有效值归一）；② 当期值缺失时返回 **0.0** 而非 NaN。预注册 §4 要求「缺失一律置 NaN 并剔除」。两套实现写法一致，独立复算**结构上无法发现**这个错。
- **实测值**：INV25 评估窗内「入掩码且值有限」单元格 2,997,924 个，其中 **4.43%（132,852 个）** 的 20 日窗有效值不足 20 → 秩被压低；`ts_rank==0` 且当期值缺失的单元格在**入掩码集合内为 0**（被 `Y[t]` 有限性过滤），即 ② 本单未污染 IC。对池级影响 ≤ 1.3e-6/36 ≈ 4e-8。
- **可执行改法**：两处同时加 `min_periods=n`（`cnt` 除以**有效值个数**，有效值 < n 置 NaN），与 D5 的口径一致；必须两侧同改，否则复算会假报 blocking。
- **验证命令**：用一次性脚本算「`isfinite` 的 20 期滚动和 < 20」在 INV25 有效掩码单元格中的占比（应为 4.43%）。

#### S3 · 复算脚本把 `invariance.json` 列进「比对对象」，但从未比对它

- **位置**：`audit/recompute_work593.py:392`（`claim_files` 含 `invariance.json`）；全文无第二处 `invariance`
- **触发条件与后果**：报告的另一个头条数字「不变量通过 25/36 = 69.4%」完全不在 `blocking` 判据覆盖内，`can_enter_memo: true` 与它无关。读者据 `audit_object` 会以为它被复算过。
- **实测值**：`grep -n invariance audit/recompute_work593.py` 只命中 `:392`；`audit_findings.json` 的 findings 无一条涉及不变量。
- **可执行改法**：要么从 `claim_files` 删掉该文件，要么补一条复算（用 `ic_series.npz` 重算 36 条 E-vs-C 秩相关并比对 `rank_corr_mean_E_vs_C`）。
- **验证命令**：`grep -c invariance audit/recompute_work593.py`（应为 1）。

#### S4 · 报告把不变量判据归给「预注册 §6」，但预注册从未定义该判据

- **位置**：`scripts/work593_report.py:476`（「判据（预注册 §6）：① 尺度指数 k … ② 秩相关 ≥ 0.999」）
- **触发条件与后果**：`preregistration.md` §6 只有主检验量与判定阈值，全文没有 `0.999`、没有「不变量」判据（`grep -n "0.999\|不变量" preregistration.md` 仅命中 §11 的一处无关文本）。k≈0 与 0.999 是**看到数据之后**定的口径，却被写成预注册。后果：25/36 与论文 153/158（认证通过的表达式）不是同一把尺子。
- **实测值**：`report_data.json.invariance_summary` = 25 invariant / 11 rejected；PL 组 11 条全 rejected，PL11/INV24 走「k 不适用」分支。
- **可执行改法**：改为「判据（本单执行期定义，未预注册）」，并在 §10 增一条「不变量判据为事后口径，阈值 k≤1e-6 / 秩相关 ≥0.999 未经预注册」。
- **验证命令**：`grep -n "0.999" experiments/WORK-593/preregistration*.md`（应无输出）。

#### S5 · 「每日 ≥50 只不足则跳过并计数」没有实现，跳过的 20 天实际是标签洞，而报告把评估窗写成到 2026-11-13

- **位置**：`scripts/work593_audit.py:72-83`（`daily_ic` 只 `continue`，无计数、无落盘）
- **触发条件与后果**：预注册 §4 明文「不足则跳过该日并**计数**」，§8 又有「单日有效股票数 < 50 的交易日占比 > 20% → 停」这道闸门。代码既不出计数也不评估该闸门，`primary_test.json` 只给 `n_days = 3832`，读者无法判断 20 天缺口是流动性不足还是标签洞。
- **实测值**：36 条因子的 eval NaN 日**完全相同、都是 20 天**，且正好是 `2026-10-19 … 2026-11-13`（最后 20 个交易日，`shift(-20)` 无标签）。⇒ 因「<50 只」被跳过的交易日 = **0**（闸门实际通过，但未记录）。有效 IC 窗实为 2011-01-04 ~ **2026-10-16**。
- **可执行改法**：`daily_ic` 返回 `(ic, n_valid, reason)`，把 `n_lt50_days` / `n_no_label_days` 写进 `primary_test.json`；报告 `eval_window` 改为标签可得区间，或并列标注「评估掩码 3852 日，其中末 20 日无标签」。
- **验证命令**：`/home/moyongkai/.venvs/quantdb/bin/python -c "import numpy as np;z=np.load('experiments/WORK-593/ic_series.npz');ev=z['eval_mask'];print(int((ev&~np.isfinite(z['E|PL01'])).sum()))"`（=20）。

#### S6 · 组合器的日抽样种子用 `seed+1000`，与预注册 §9「两个随机源各自 `random.Random(seed)`」不符

- **位置**：`scripts/work593_audit.py:208`
- **触发条件与后果**：预注册 §9 写死「二者都用同一 seed ∈ {42,43,44}，各自用 `random.Random(seed)` 独立实例」。代码用 `seed+1000`。只影响次要口径（组合器），不改判词，但属未登记偏离，且使「同种子可复现」对第三方复刻者是错的。
- **实测值**：`combiner.json` 三种子 ΔIC = +0.005292 / +0.006465 / +0.004956，均值占比 **12.10%**（与 HEAD 的 12.1% 一致；`968c8d1` 的 `registry.md` 写 12.9% 是错的，已在 `bf07084` 修正）。
- **可执行改法**：改成 `random.Random(seed)`，或在 v1.1 偏离表登记「组合器抽样用 `seed+1000` 以避免与 bootstrap 共享流」。
- **验证命令**：`grep -n "seed + 1000" scripts/work593_audit.py`。

#### S7 · 复算侧「判词一致性」只比 2 条判据（缺 CI），与建模侧 3 条判据不等价

- **位置**：`audit/recompute_work593.py:378`
- **触发条件与后果**：`verdict_recomputed = pooled > 0 and 0.10 <= ratio <= 0.18`，不含「CI 不含 0」。若某次 ΔIC>0 且占比落区间但 CI 含 0，建模侧判「未复现」、复算侧判「复现成立」→ 报文会出现**假 blocking**（反向不会漏报：ΔIC<0 时两侧同为未复现）。本单未触发。
- **实测值**：该条 findings 为 `nit`，`{'recomputed': '未复现', 'claimed': '未复现'}`。
- **可执行改法**：把 bootstrap 的三种子 CI 一并纳入：`pooled>0 and all(ci_excludes_zero) and 0.10<=ratio<=0.18`。
- **验证命令**：`grep -n "verdict_recomputed" audit/recompute_work593.py`。

### 3.3 nit（6 条）

| # | 位置 | 问题 | 改法 |
|---|---|---|---|
| N1 | `scripts/work593_fetch.py:227` | `di = {...}` 定义后从未使用（死代码）；`:226-231` 的 `mask_cells` 对 2,505 段各扫一遍 6.1M 行（~10⁹ 次比较） | 删 `di`；用一次 `merge`/`groupby` 算完 |
| N2 | `scripts/work593_report.py:471` | 图 4 画的是由 `(boot_mean, boot_sd)` 拟合的**正态密度**，图注/轴标签却写「1000 次循环块重采样**分布**，阴影为 95% 分位区间」；且与图 2/3/5/6 一样只有单面板却标 `b/c/d` | 存下 bootstrap `means` 画直方图，或把图注改成「由 bootstrap 均值与标准差拟合的正态近似」；单面板去掉面板字母 |
| N3 | `experiments/WORK-593/preregistration.md:137-145` + 报告 §3 小字 | 并集对账表「延展 2 / 2010 后上市 755 / 2026-11 前无行情 126 / 剩余 130」**不是互斥分解**（755 与 126 是并集内全体子集、可重叠；2+755+126 ≫ 132），读者无法加总核验 | 明说「分量互有重叠，不是分区」，并补一个互斥三分表 |
| N4 | `experiments/WORK-593/preregistration.md:110`(§3 V1) vs `scripts/work593_represent.py:170-210` | 预注册 V1 写的是「`qlib.close × qlib.factor / factor(最后一日)` 对齐后与 `p_t^(E)` 比值」，实现改成「比收益 + volume 水平」；该改动只在 `failure_log.md` §4 出现，**未进 v1.1 偏离表** | 在 v1.1 偏离表补一条，写明 V1 口径变更及理由 |
| N5 | `scripts/work593_audit.py:430-434` + 报告判据表 | `c2` 改成「CI 不含 0（不分方向）」后，`verdict_reason` 的 `"CI含0或负"` 标签永不可能触发；判据表把整段为负的 CI 显示为「通过」（正文有一句解释，故仅 nit） | 标签改为「CI 含 0」；判据表 C2 行加注「不含 0 但在负侧」 |
| N6 | `scripts/work593_audit.py:211-216` | 组合器 z-score 用 `float32`，且均值/标准差在**掩码**上算（含当日无标签股票），与打分横截面口径不完全一致 | 已核查：z 值相对误差 ~1e-7，E/C 共用同一管线、共模误差抵消，ΔIC 三位有效数字不变，**不构成问题**，仅记录 |

---

## 4. 结论：能否进入研究备忘录

**不能直接进；修完 B1–B4 后可进（四条都不改动任何数字）。**

1. **数字层：站得住。**
   - 独立复跑**逐字节一致**（`audit_findings.json` 与两个 compare CSV 的 sha256 与 `968c8d1` 完全相同），交付物可重放；
   - 我另用独立实现重跑 bootstrap：种子 42/43/44 复现交付 CI 到 6 位小数，换 4 个新种子后 CI 移动 ~1e-4 但**全部不含 0 且整段为负**，块长 10/20/40 亦然 → 「未复现（方向反）」不依赖种子或块长；
   - 锚点恒等式（max 4.8e-16）、成交额恒等式（违规 0，max 4.1e-16）、标签跨表示一致（1.8e-15）成立；
   - 无前视（因子全用 `shift(+k)/rolling`，`shift(-20)` 只在标签）、无幸存者偏差（126 只退市/长停股保留至末次交易，掩码按快照时点生效）；
   - 主检验量确实是**因子级均值**而非组合器池级（36 条 `n_days` 相同，两种均值差 4e-14），占比 6.09% 不来自口径混用。
2. **表述与闸门层：不能带着进。**
   - B1：唯一验收闸门用的是**未登记、且比自己落盘规则宽 10⁷ 倍**的容差（1e-5 vs 1e-9），报告 §8 一边显示「最大相对差 7.25」一边写「blocking = 0」；
   - B2：报告与失败日志描述了**没有发生**的停牌处理（预注册 §4）；
   - B3：图注把 A 组 ΔIC 方向写反（11 条全负 → 「整体为正」）；
   - B4：`paper_comparison.csv` 不存在，`metrics_factors.csv` 等 4 个 CSV 未入库，验收链在仓库内断掉。

**一句话**：结论「未复现」可信、可重放；交付文本的可核验性不行。修 B1–B4（约 1 小时、不改数字）即可进备忘录。

---

## 5. 未确认项

| # | 事项 | 为何未确认 | 已知证据 |
|---|---|---|---|
| U1 | 预注册 v1.1（D1：C1 容差 1e-9 → 按字段分辨率）是否真在**看到 IC 之前**落盘 | `git log -- experiments/WORK-593/preregistration_v1.1.md` 只有 `a7483bb`、`968c8d1`，而 `a7483bb` **同时**含 `primary_test.json`、`report_data.json`、审计脚本 —— 没有任何提交把 v1.1 固定在结果之前；文件内的时间声明无法用 git 验证 | v1.0（`1c8eaac`）确在结果之前 ✅；D1 只改数据层闸门、不改主检验量与阈值，但方向上是「把一个曾判停的闸门放行」，值得留痕 |
| U2 | 「掩码股票-日」到底是 3,260,000（审计 `M.sum()`，日历口径）还是 3,180,685（manifest `mask_stock_days`，只数有行日） | 两个名字同一个意思、数值差 79,315，manifest 未写定义；`registry.md`/报告用前者 | 见 B2/S1；建议改名 `mask_cells_calendar` / `mask_cells_with_rows` |
| U3 | 论文 1,730 只的 130 只残差 | Qlib CSI300/CSI500 v2/v3 成分名单本机不存在（与 `failure_log.md` §3 一致） | 本地 1,862 / 延展 2 / 2010 后上市 755 / 2026-11 前无行情 126 均可复算；残差无法逐只归因，且对账表不是分区（N3） |
| U4 | 与 Qlib `$vwap` 逐值比对 | 本机 bundle 无 `vwap.day.bin`，日历止于 2020-09-25 | 报告已按「部分未取到」表述，未越界写成「不存在」✅ |
| U5 | 停牌日若真按预注册 §4 前向填充，主检验量会移动多少 | 需重跑全链（取数→表示→36 因子→bootstrap），超出「只审不改」范围 | 受影响面：2.43% 的成员股票-日、4.43% 的 INV25 单元格；INV25 只占池 1/36，ΔIC_pool = −0.0017 距 0 约 4.8 个 bootstrap σ，方向不太可能翻转 |

---

## 附：本次审查执行过的命令（可复现）

```
cp experiments/WORK-593/audit_findings.json experiments/WORK-593/audit_findings_firstrun.json
/home/moyongkai/.venvs/quantdb/bin/python audit/recompute_work593.py     # exit 0, 4:25, 3.79 GB
sha256sum experiments/WORK-593/audit_findings{.json,_firstrun.json}      # 相同
cp experiments/WORK-593/audit_findings_firstrun.json experiments/WORK-593/audit_findings.json
rm experiments/WORK-593/audit_findings_firstrun.json
```
（另有一次性的锚点/恒等式/ts_rank/bootstrap 抽查脚本，均已删除；未执行任何 git 写操作。）
