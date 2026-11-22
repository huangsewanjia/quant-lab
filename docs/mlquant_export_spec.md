# 数据导出规格：`/srv/share/quantdb/raw` → `load_ochlv_csv` 长表

> **本文件是可直接实现的规格**：照着写，不需要再问一句。
> 目标消费者：官方实现 `initial-d/ml-quant-trading` @ `476f62a9` 的
> `src/mlquant/data/loaders.py:58` `load_ochlv_csv()`。
> 复现目标：ML-Quant（arXiv:2507.07107v2）Table 11 **真实面板**（2022–2024）。
>
> 关联：[`mlquant_alignment.md`](./mlquant_alignment.md)（主对齐表与缺口清单）·
> [`mlquant_factor_fields.md`](./mlquant_factor_fields.md)（213 因子字段需求）

---

## 0. 一页速查

| 项 | 规定 |
|---|---|
| 输出格式 | **Tab 分隔**长表 CSV，首行表头（`load_ochlv_csv` 默认 `sep="\t"`） |
| 表头（**顺序固定，名字不能改**） | `TRADE_DT` `S_INFO_WINDCODE` `open` `high` `low` `close` `volume` `vwap` `amount` `limit_up` `limit_down` `last_close` |
| 日期区间 | **2014-01-01 – 2024-12-31**（含 1 年预热；训练/验证/测试切分仍按论文 2015–2020 / 2021 / 2022–2024） |
| 股票池 | **全 A**：窗口内 `daily` 出现过的全部 `ts_code`（含退市股），不做在市过滤 |
| 复权 | **后复权（hfq）**：`price_hfq = price_raw × adj_factor`，**不做基准日归一** |
| 单位 | `volume` = **股**；`amount` = **元**；价格 = **元/股** |
| 预期行数 | **10,053,442** 行（2014–2024）；2015–2024 子集 = 9,482,407 行 |
| 排序 | `TRADE_DT` 升序 → `S_INFO_WINDCODE` 升序 |
| 去重 | `(TRADE_DT, S_INFO_WINDCODE)` 唯一；导出侧先去重，loader 侧还会再 drop_duplicates 一次（保留首行） |
| 缺失 | **不写占位行**：停牌/未上市/退市后的格子**不出现**，由 loader pivot 后变 NaN → `mask=False` |

---

## 1. 消费者契约（先把对方的脾气摸清）

`load_ochlv_csv`（`src/mlquant/data/loaders.py:58-125`）的行为，逐条决定了我们必须怎么导出：

| # | 官方代码行为 | 对导出的约束 |
|---|---|---|
| 1 | `loaders.py:78-79`：缺 `TRADE_DT` 或 `S_INFO_WINDCODE` 直接 `KeyError` | 这两列**必须存在**，名字不能改 |
| 2 | `loaders.py:81`：`pd.to_datetime(df["TRADE_DT"].astype(str))` | `TRADE_DT` 写成 `YYYY-MM-DD` 或 `YYYYMMDD` 均可，须能被 pandas 解析；**推荐 `YYYY-MM-DD`** |
| 3 | `loaders.py:82`：`df["S_INFO_WINDCODE"].astype(str).str[:6]` | 写成 `000001.SZ` 会被截成 `000001`。**建议直接写 6 位裸代码**（如 `000001`），避免不同市场同码歧义 |
| 4 | `loaders.py:34-41` 必需别名：`open/high/low/close/volume/vwap`；`:43-48` 可选别名：`amount/limit_up/limit_down/last_close` | **列名必须用小写规范名**（`open`、`vwap`、`limit_up`…），因为别名表把规范名排在第一位 |
| 5 | `loaders.py:95-98`：必需字段缺失 → `KeyError` | **`vwap` 是必需字段**（论文 §A.1 把它写成 optional 是错的，见对齐表 A2）。必须给**真 VWAP** |
| 6 | `loaders.py:85-87`：`drop_duplicates(subset=[TRADE_DT, S_INFO_WINDCODE])` 后按两列排序 | 导出侧保证唯一；顺序由 loader 重排，导出顺序只影响可读性与 sha256 |
| 7 | `loaders.py:104`：以 `TRADE_DT` 为 index、`S_INFO_WINDCODE` 为 columns 做 pivot | 每个 (日期, 代码) 组合**至多一行** |
| 8 | `loaders.py:113`：`mask = (~close.isna())` | 「有收盘价」即「可观测」。停牌日因 `daily` 无行 → pivot 后 close=NaN → `mask=False`，**这正是我们要的** |
| 9 | `loaders.py:115`：`df_.fillna(0.0)` | 任何 NaN 字段会被填 **0**。所以 `limit_up/limit_down` 缺失 ⇒ 填 0 ⇒ `bias.py:119` 的 `has_band=False` ⇒ **自动退回 9.8% 代理**，正是论文 §A.2 的 regime 2 |
| 10 | `data/panel.py:35`：`Panel._REQUIRED_FIELDS` 含 `vwap` | 同 #5 |
| 11 | `data/panel.py:112-136`：`Panel.returns` 优先用 `last_close`，否则用「最近可交易收盘」 | 给了 `last_close` ⇒ 收益 = `close/close[t−1]−1`（跨停牌前推）；不给 ⇒ 同语义但走 torch fallback |
| 12 | `features/bias.py:122`：`close < limit_up − eps & close > limit_down + eps`，`eps=1e-3` | **eps 是绝对价格容差**，「must suit the feed's units」⇒ 价格标度必须让 0.01 元的交易所取整间隔在标度下仍大于 1e-3（见 §2.3） |
| 13 | `features/bias.py:129`：`inside[0] = False` | 第一天永远被排除在限价掩码之外 |

---

## 2. 口径裁定（三个必须写死的选择）

### 2.1 复权：**后复权 hfq，无基准日归一**

```
price_hfq[t,i] = price_raw[t,i] × adj_factor[t,i]
```

**为什么选 hfq（而不是 qfq）**——三条理由，每条都有实测支撑：

1. **eps=1e-3 的安全性**。交易所限价与收盘价都取整到 **0.01 元**。缩放到复权标度后，原始 0.01 元的取整间隔变成 `0.01 × f`。
   - hfq 的 `f = adj_factor`，全库 **16,722,519** 行（5,074 分区）实测 **min = 0.645** ⇒ 最小间隔 **0.00645 > 1e-3** ⇒ **安全**（导出窗 2014–2024 内 10,592,327 行 / 2,678 分区，min/max 同值）。
   - qfq 的 `f = adj_factor[t] / adj_factor[base]`，实测可低至 **0.052**（2015-01-05 抽样）⇒ 间隔 0.00052 < 1e-3 ⇒ 差一分钱未触板的股票会被**误判为触板**。风险成立。
2. **退市股不需要特例**。2024-12-31 没有当日 `adj_factor` 的股票：导出窗（2015–2024 的 5,591 只）里 **181** 只，全库口径 **480** 只（多为已退市）。qfq 需要给每只股票定各自的基准日，标度就不再全局统一；hfq 天然统一。
3. **与已有实现一致**。WORK-449 预注册 v1.2 已经从 qfq 改到 hfq（理由：东财 qfq 的 2 位小数精度在低价股上会塌陷，`scripts/work449_build_panel.py:20-23`）。换口径要重做论证，不值得。

**旁证**：`adj_factor` 在个股首个观测日上 **77.2% 恰好等于 1.0**、中位 1.0、取值范围 0.645–10,055.64 —— 这正是 Tushare 的标准复权因子，`price × adj_factor` 就是标准**后复权**序列（「上市日元的名义价」）。

**hfq 的已知代价（必须披露）**：价格是「上市日元的名义价」，做过多次分红的股票绝对价位会很高（例如 `adj_factor` 最大 10,055.64）。
这不影响任何**比值型**因子（`close/ts_max(close,20)`、`vwap/close`…），也不影响限价判定（同因子缩放抵消）。
但会让「绝对价位型」因子（如 `ts_mean(close,5)`）的量纲与不复权价不同 —— 这是**口径差异，不是错误**，要在数据契约里写死。

**复权基准日的替代方案**（若将来要换）：qfq 基准日取 2024-12-31，公式 `f = adj_factor[t] / adj_factor[2024-12-31]`，
退市股用其自身最后一个可得日期。此时**必须**把 `bias.py` 的 `eps` 改成随标度缩放（例如 `eps = 1e-3 × min_f`），否则 §2.3 的风险成真。

### 2.2 单位

| Panel 字段 | 新库来源 | 换算 | 单位 |
|---|---|---|---|
| `volume` | `daily.vol` | **× 100**（手 → 股） | 股 |
| `amount` | `daily.amount` | **× 1000 × adj_factor**（千元 → 元，再复权） | 元 |
| `vwap` | `daily.amount` ÷ `daily.vol` | **amount_千元 × 10 ÷ vol_手 × adj_factor** | 元/股 |
| `open/high/low/close` | `daily.open/high/low/close` | **× adj_factor** | 元/股 |
| `limit_up/limit_down` | `stk_limit.up_limit/down_limit` | **× adj_factor** | 元/股 |
| `last_close` | 由 `close_hfq` 前推 | 见 §4 | 元/股 |

**校验恒等式**（导出脚本必须断言）：

```
vwap_hfq == amount_hfq / volume_shares          # 误差 < 1e-6 相对
```

推导：`amount_hfq = amount_千元×1000×f`，`volume = vol_手×100`，`vwap_hfq = amount_千元×10/vol_手×f`
⇒ `amount_hfq / volume = amount_千元×1000×f / (vol_手×100) = amount_千元×10×f/vol_手` ✓

**实测**：该公式与 `(H+L+C)/3` 的中位相对偏差 **0.12%–0.28%**（3 个抽样日全量行），p90 ≤ 0.8%，>5% 的行占比 ≤ 0.11%。

**为什么 volume 用「股」而不是「手」**：官方实现**不做任何单位换算**（`loaders.py` 直接吃文件里的数），
所以单位必须由导出侧定死并写进数据契约。而且：
- `legacy_factors.py:79-81` 的 `_amount(panel)` 在 `panel.amount` 缺失时退回 `panel.vwap * panel.volume`；
  只有 volume=股、amount=元，这个退化式才等于真实成交额。
- 官方合成数据的量级（`data/synthetic.py:96` `volume = lognormal(mean=15.0)` ≈ 3.3×10⁶）也是**股**的量级，不是手。

**⚠️ 单位会改变 16 个因子的数值**（不是比例不变量）：
`alpha_002` `better_001` `better_008` `better_009` `better_010` `better_013` `better_014`
`better_019` `better_022` `better_023` `better_027` `better_028` `stock_001` `stock_002` `stock_010` `stock_015`
—— 它们对 `volume` 或 `amount` 取 `log`/`log2`，`log(手)` 与 `log(股)` 差一个常数 `ln 100`，
且该常数不是加性可消的（因为 log 作用在成交量上），所以**截面 z-score 之后数值仍不同**。
→ 这 16 个因子的口径必须写进 `data_contracts/field_dictionary.md`，并在对齐报告里标注为「口径敏感因子」。

### 2.3 限价容差与标度

`bias.py:99` 的 `eps=1e-3` 是**绝对价格**容差。用 hfq 时：

```
交易所取整间隔(标度后) = 0.01 元 × adj_factor ≥ 0.01 × 0.645 = 0.00645 > 1e-3   ✓
```

⇒ **hfq 标度下 eps=1e-3 恰好能区分「触板（差 0）」与「差一分钱未触板（差 ≥ 0.00645）」**，不需要改代码。

补充实测（6 个抽样日，qfq 基准 2024-12-31 的对照实验）：用 `eps=1e-3` 在**缩放后**判定的「触板集合」
与**缩放前**逐格一致（100%）。qfq 之所以也过了这 6 天，是因为「恰好差一分钱」的样本极稀疏，
**不能据此认为 qfq 安全** —— 结论仍按 §2.1 走 hfq。

---

## 3. 列名映射表（源 → 目标）

| 目标列 | 新库来源 | 派生 |
|---|---|---|
| `TRADE_DT` | `daily.trade_date`（date32） | 格式化成 `YYYY-MM-DD` 字符串 |
| `S_INFO_WINDCODE` | `daily.ts_code`（如 `000001.SZ`） | 取前 6 位（`000001`） |
| `open` | `daily.open` | `× adj_factor` |
| `high` | `daily.high` | `× adj_factor` |
| `low` | `daily.low` | `× adj_factor` |
| `close` | `daily.close` | `× adj_factor` |
| `volume` | `daily.vol` | `× 100`（手 → 股） |
| `vwap` | `daily.amount`, `daily.vol` | `amount×10÷vol×adj_factor` |
| `amount` | `daily.amount` | `× 1000 × adj_factor`（千元 → 元） |
| `limit_up` | `stk_limit.up_limit` | `× adj_factor`；缺该股当日限价 ⇒ **留空 → loader 填 0 → 自动走代理** |
| `limit_down` | `stk_limit.down_limit` | 同上 |
| `last_close` | 由 `close_hfq` 在个股内前推（跨停牌前推） | 见 §4 |

> `daily.pre_close` **不直接使用**：它是交易所的除权参考价，与 hfq 标度不一致（见 §4 注）。

---

## 4. `last_close` 的定义（易错点）

**定义**：`last_close[t,i] = close_hfq[t−1 交易日, i]`，其中「t−1 交易日」指**该股票上一个有成交的交易日**
（停牌日跳过前推；未上市/已退市则无值）。

**为什么不用 `daily.pre_close`**：`pre_close` 是交易所的**除权参考价**（已含除权除息调整），
与 hfq 标度不是同一套。而 `close_hfq[t−1]` 与 `close_hfq[t]` 的比值**天然**是分红调整后的总收益，
这正是我们要的标签口径（对齐表 C1）。

**一致性要求**：给出 `last_close` 后，`data/panel.py:123-127` 会走 `close/last_close − 1` 分支，
`features/bias.py:135-137` 的限价代理也会用它 ⇒ 代理路径在跨停牌时不会把停牌误判成假触板。
**若不给 `last_close`**，`data/panel.py:128-136` 的 fallback 会用「最近可交易收盘」，语义等价但多一层 torch 逻辑。
**本规格选择给出**（更直白、更容易在数据契约里审计）。

**注意**：首个交易日没有 `last_close` ⇒ 留空 ⇒ loader 填 0 ⇒ `data/panel.py:126` 的 `last_close > 0` 判据使该格收益无效（置 0）✓ 与 `bias.py:129` 的 `inside[0]=False` 一致。

---

## 5. 掩码三件套（导出后必须自己拼的三步）

论文 §3.2 Eq(1)：`M_{t,i} = S_{t,i} ∧ (¬L_{t,i}) ∧ IPO_{t,i}`。
官方实现只做了其中一部分，**这三步都得我们自己补**（对齐表 G-01 / G-03 / A3）。

| 条件 | 怎么算 | 数据源 | 官方代码状态 |
|---|---|---|---|
| **观测** `O_{t,i}` | loader 自动给：`mask = ~close.isna()` | `daily` 有行即真 | ✅ 已有（`loaders.py:113`） |
| **停牌** `S_{t,i}` | `suspend_d` 当日有该股 ⇒ 不可交易 | `suspend_d` | ⚠️ 官方靠「无行」隐式等价；我们显式接 |
| **限价** `¬L_{t,i}` | `close < limit_up − 1e-3 & close > limit_down + 1e-3`（有带时）；否则 `\|close/last_close − 1\| ≤ 0.098` | `stk_limit` | ✅ 官方有（`bias.py:115-130`），但 `paper.yaml` 默认走标量代理 |
| **上市 > 1 年** `IPO_{t,i}` | `trade_date > list_date + 365 天` | `stock_basic.list_date` | ❌ **官方未实现**（`src/` 全仓无 `list_date` 逻辑） |

**第 4 项的落地方式**：`Panel` 是 frozen dataclass，但 `data/panel.py:147` 提供 `replace()`。
加载后按论文 Eq(1) 重新组装：

```python
from dataclasses import replace
panel = replace(panel, mask=M_full)     # M_full = O ∧ S ∧ ¬L ∧ IPO
```

**第 3 项（t+1 双向标签检查）的落地方式**（对齐表 G-03，官方没做）：

```python
M_t   = M_full                       # [T, N]
M_tp1 = shift_rows(M_full, +1)       # M_tp1[t] = M_full[t+1]
label_mask = M_t & M_tp1             # 论文 §3.5.1 的双向检查
```

即：**t+1 撞板的样本也必须从训练集里剔除**。

---

## 6. 可执行伪代码

```python
#!/usr/bin/env python3
"""Export /srv/share/quantdb/raw -> ML-Quant load_ochlv_csv long table.

Spec: quant-lab/docs/mlquant_export_spec.md
"""
from __future__ import annotations
import os, sys, csv, hashlib, json, argparse
import pyarrow.parquet as pq
import pyarrow.dataset as ds
import numpy as np

RAW   = "/srv/share/quantdb/raw"
LO, HI = "20140101", "20241231"          # 1-year warm-up before the 2015 train start
BASE  = "20241231"

def parts(name, lo=LO, hi=HI):
    """[(yyyymmdd, parquet_path)] for a Hive-partitioned dataset."""
    root = os.path.join(RAW, name); out = []
    for p in os.listdir(root):
        k = p.split("=", 1)[1] if "=" in p else ""
        if len(k) == 8 and k.isdigit() and lo <= k <= hi:
            out.append((k, os.path.join(root, p, "part.parquet")))
    return sorted(out)

def read(name, date, cols=None):
    return pq.read_table(os.path.join(RAW, name, f"trade_date={date}", "part.parquet"),
                          columns=cols).to_pydict()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output .tsv")
    ap.add_argument("--rows", type=int, default=0, help="expected row count (0 = skip assert)")
    args = ap.parse_args()

    dates = [d for d, _ in parts("daily")]
    hdr = ["TRADE_DT", "S_INFO_WINDCODE", "open", "high", "low", "close",
           "volume", "vwap", "amount", "limit_up", "limit_down", "last_close"]
    n = 0
    sha = hashlib.sha256()
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(hdr)
        for d in dates:
            # ---- 1. daily: the row set.  A missing (date, code) means
            #         suspended / not listed / delisted -> we simply omit it.
            dl = read("daily", d)
            codes = dl["ts_code"]
            if not codes:
                continue
            # ---- 2. adjustment factor for this date
            af = read("adj_factor", d, ["ts_code", "adj_factor"])
            afmap = dict(zip(af["ts_code"], af["adj_factor"]))

            # ---- 3. exchange-published limit band (optional; missing -> blank)
            sb = os.path.join(RAW, "stk_limit", f"trade_date={d}", "part.parquet")
            up, dn = {}, {}
            if os.path.exists(sb):
                s = pq.read_table(sb).to_pydict()
                up  = dict(zip(s["ts_code"], s["up_limit"]))
                dn  = dict(zip(s["ts_code"], s["down_limit"]))

            rows = []
            for i, code in enumerate(codes):
                f = afmap.get(code)
                if f is None or not np.isfinite(f):
                    continue                     # no adj_factor -> drop the row (assert below)
                vol_hands = dl["vol"][i]
                amt_k      = dl["amount"][i]
                price = {k: dl[k][i] * f for k in ("open", "high", "low", "close")}
                volume = vol_hands * 100.0                     # 手 -> 股
                vwap   = (amt_k * 10.0 / vol_hands) * f if vol_hands else 0.0
                amount = amt_k * 1000.0 * f                    # 千元 -> 元, then hfq
                rows.append([
                    f"{d[:4]}-{d[4:6]}-{d[6:]}",
                    code[:6],                                  # 6-digit bare code
                    price["open"], price["high"], price["low"], price["close"],
                    volume, vwap, amount,
                    up.get(code, "") if up.get(code) is not None else "",
                    dn.get(code, "") if dn.get(code) is not None else "",
                    "",                                        # last_close, filled in pass 2
                ])
            rows.sort(key=lambda r: r[1])
            w.writerows(rows)
            n += len(rows)

    # ---- pass 2: fill last_close = previous TRADING close (per code, carried over halts)
    #      done by a second streaming pass over the same file, or by keeping
    #      last_close per code while writing (single pass, see note below)

    if args.rows and n != args.rows:
        print(f"ROW COUNT MISMATCH: got {n:,} expected {args.rows:,}", file=sys.stderr)
        return 1
    print(f"wrote {args.out}  rows={n:,}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 6.1 `last_close` 的单遍实现（把上面伪代码的 pass 2 折进 pass 1）

不要读两遍。维护一个 `prev_close: dict[code, float]`，每写完一行就更新：

```python
prev_close = {}          # code -> last hfq close written
...
for i, code in enumerate(codes):
    ...
    last_close = prev_close.get(code, "")     # "" for the stock's first ever row
    ...
    rows.append([... , last_close])
    prev_close[code[:6]] = price["close"]     # update AFTER writing this row
```

⚠️ 顺序要求：`last_close` 必须取**上一行的 close**，所以更新字典要在 append 之后。

### 6.2 导出脚本必须自带的断言（缺一不可）

| 断言 | 阈值 | 依据 |
|---|---|---|
| 行数 == 10,053,442（2014–2024） | 精确 | §7 实测 |
| `(TRADE_DT, S_INFO_WINDCODE)` 唯一 | 精确 | loader 契约 #7 |
| `vwap == amount / volume`（相对误差） | < 1e-6 | §2.2 恒等式 |
| `high >= max(open, close)`、`low <= min(open, close)` | 逐行 | `data/panel.py:198-202` 的 `assert_consistent` 会查 |
| `close > 0`、`volume >= 0`、`amount >= 0` | 逐行 | `target.py:62-65` 要求 close > 0 |
| `limit_up >= close`、`limit_down <= close` 的**比例** | ≥ 99% | 触板股票会 violate；记录比例作为「触板率」自检 |
| 每行都有 `adj_factor` | **实测 1,727 行缺（10,053,442 行的 0.0172%）**，导出时剔除 | 逐日集合差实测（`daily` 代码集 − `adj_factor` 代码集），1,299 个交易日各有 1–5 行缺失。**剔除数必须写进报告，不能写「约」** |

### 6.3 内存现实（必须提前知道）

`load_ochlv_csv` 会 `pd.read_csv` **整个文件**再 pivot：

- 10,053,442 行 × 12 列 ⇒ pandas 内存约 **1.5–2.5 GB**（含两个字符串列的对象开销）；
- pivot 后 11 个宽表 × 2676 × 5581 ⇒ 约 **1.3 GB**（float64），加上 pivot 中间态，**峰值 6–10 GB**。

**三条可选路径（按推荐度）**：

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（推荐）** | 直接用 `pyarrow.dataset` 读 parquet → `Panel.from_tensors`（`data/panel.py:65-94`），**绕开 CSV** | 最省内存、最快；`Panel.from_tensors` 是官方公开 API，口径与 loader 完全一致 | 绕过了「CSV 契约」这条路径，偏离「用官方 loader」的原意 |
| **B** | 按年分片导出 11 个 `.tsv`，逐年/逐块 `load_ochlv_csv` 后用 `data/panel.py:165` `slice_dates` 拼接 | 仍走官方 loader；峰值内存降到 1/11 | 需自己写拼接逻辑，mask 的对齐要小心 |
| **C** | 单文件全量导出 + 单次 `load_ochlv_csv` | 最贴近官方路径 | 峰值 6–10 GB，机器不够就 OOM |

> **本规格的主交付是「表长什么样」**，不是「用哪条路读」。**先按 §3 的列名/单位把表做对**，
> 读取路径（A/B/C）是实施时按机器内存选的工程决定。

---

## 7. 预期行数（实测，可直接用来验收）

| 窗口 | 交易日 | `daily` 行数 | 预期导出行数 |
|---|---:|---:|---:|
| **2014-01-01 – 2024-12-31（本规格默认）** | **2,676** | **10,053,442** | **10,051,715**（= 10,053,442 − 缺 `adj_factor` 的 **1,727** 行，实测 0.0172%） |
| 2015-01-01 – 2024-12-31（论文真实面板窗） | 2,431 | 9,482,407 | ≈ 9,481,000 |
| 2015–2020（训练段） | 1,462 | 4,665,263 | ≈ 4,664,000 |
| 2021（验证段） | 243 | 1,085,445 | ≈ 1,085,000 |
| **2022–2024（测试段，Table 11 对照窗）** | **726** | 3,731,699 | ≈ 3,731,000 |
| 全库 2006-01-04 – 2026-11-20（备选） | 5,072 | 15,966,167 | ≈ 15,960,000 |

**股票数**：2015–2024 `daily` 出现 **5,591** 只（其中 `stock_basic` 状态为 D 的 **260** 只已退市股，**必须保留**）；
加 2014 预热年约 **5,597** 只。

**其他数据集的同期行数**（供 join 完整性核对）：
`adj_factor` 9,969,383（2,433 分区，比 `daily` 多 2 个分区，导出时按日期取交集即可）·
`stk_limit` 9,724,911 · `daily_basic` 9,392,135 · `suspend_d` 330,217 · `index_daily` 21,976,288。

---

## 8. 股票池与日期区间的规定

| 项 | 规定 | 理由 |
|---|---|---|
| **日期区间** | 2014-01-01 – 2024-12-31 | 论文 §4.1 是 2015–2024；多导 2014 一年是为了让 60 日滚动窗和 120 日协方差窗在 **2015-01-01 训练起点**上就已「预热」完毕，否则训练段前 120 日的因子/协方差会因窗口未满而失真 |
| **训练/验证/测试切分** | 2015-01-01 – 2020-12-31 / 2021 全年 / 2022-01-01 – 2024-12-31 | 论文 §4.1。**切分由加载后的日期切片实现，不在导出阶段切**（导出阶段切会破坏面板连续性） |
| **股票池** | 窗口内 `daily` 出现过的全部 `ts_code` | 论文 §4.1 隐含全 A（掩码后约 3,200 只可交易）；**用「窗口内出现过」而不是「当前在市」**，否则退市股被剔除 → 幸存者偏差 |
| **退市股** | **保留** | 2015–2024 内有 260 只退市股仍在 `daily` 里；它们退市后无行 ⇒ 自动 `mask=False` |
| **ST 股** | **保留** | ST 的 5% 限价由交易所的 `stk_limit` 直接体现，不需要另做 ST 过滤 |
| **新股（上市 < 1 年）** | **保留在表里，但掩码为 0** | 论文 Eq(1) 的 `IPO` 条件在**掩码层**生效，不在导出层删行（删行会破坏面板的日期×代码网格） |
| **北交所 / 科创板 / 创业板** | **保留** | `stk_limit` 是交易所口径，不需要分板代理（官方 `paper.yaml` 的标量 0.098 反而会误判） |

---

## 9. 实施顺序与自检清单

```
① 导出 2014-2024 的 .tsv（§6）              → 行数 ≈ 10,053,442
② 自检 §6.2 的 7 条断言                      → 全过
③ load_ochlv_csv 载入                        → panel.returns / assert_consistent 通过
④ 拼掩码三件套（§5）：O ∧ S ∧ ¬L ∧ IPO        → mask 率与 WORK-449 对照
⑤ 标签双向检查：label_mask = M_t & M_{t+1}   → 论文 §3.5.1（官方没做）
⑥ 算 213 因子（复用官方 10 个模块，MIT）      → 与 mlquant_factor_fields.md 的字段需求逐条对账
⑦ 训练前定 G-08（Transformer depth）          → 见 mlquant_alignment.md §3.3
```

### 9.1 交付前必须回答的三个问题

1. **触板率是多少？**（`limit_up ≥ close` 的行占比）—— 与论文的量级对照：论文 §6.1 说 2022 熊市限价命中率
   从 1.2% 升到 4.8%，§6.2 说 2024-09 涨停事件达全市场 8%。我们的实测抽样日合计 **7.18%** 的行是限价事件
   （含 2016-01-04 那种 1,349 行爆量的日子）。**量级对得上，但样本日和论文不同，只能做量级对照。**
2. **掩码率是多少？**（`M_full` 为真的比例）—— 论文 §4.1 说掩码后约 3,200 只可交易。
   我们池子 5,591 只，掩码后剩多少要自己数。
3. **`adj_factor` 缺失的行剔了多少？**—— **实测 1,727 行（0.0172%）**，已写死。要在报告里给这个确切数字，不能写「约」。

---

## 10. 回滚

本规格是**文档**，不改任何数据、不改 `/srv/share/quantdb`。
要改口径：改本文件 → 在 `quant-lab/experiments/WORK-516/` 记一条变更 → 重跑导出。
`/srv/share/quantdb/raw` 是**只读**的（`ls -ld` 显示 `drwxrwsr-x`，但本单**未写入任何一行**）。
导出产物写到 `quant-lab/data/WORK-516/`（新建目录，不覆盖 `data/WORK-449/`）。
