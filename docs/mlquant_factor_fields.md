# ML-Quant 213 因子 · 字段需求清单

> 生成方式：对官方实现 `initial-d/ml-quant-trading` @ `476f62a9` 做 **AST 静态分析**，
> 逐个解析 `@register_legacy_factor("<name>")` 装饰的函数体，沿**跨模块辅助函数**
> （`legacy_factors.py` 的 `_close_loc` / `_amount` / `_ret`）递归追踪 `panel.<字段>` 访问。
> 机器可读版：[`mlquant_factor_fields.csv`](./mlquant_factor_fields.csv)；分析脚本见文末「复算方法」。

**判定口径**：`requires_fields` 列只列**该因子函数直接/间接读取的行情字段**，
不含 `panel.mask`（掩码本身不是数据字段）。`reads_panel_amount=Y` 表示因子走
`legacy_factors.py:79-81` 的 `_amount()`，它在 `panel.amount` 存在时用成交额、
否则退回 `vwap × volume` —— 这两个口径数值不同，是**换值不换字段**的依赖（见缺口清单 G-03）。

## 一、总账（确定数字）

| 口径 | 因子数 | 说明 |
|---|---:|---|
| 只靠 OHLCV（open/high/low/close/volume）即可算出 | **155** | 不需要 vwap，也不需要 amount |
| 需要 vwap（volume 已有） | **43** | 新库需派生 vwap |
| 需要 vwap **且**读取 `panel.amount` | **15** | 给了 amount 用 amount，不给则退化为 vwap×volume |
| 需要**库外字段**（限价、停牌、流通市值等）才能算 | **0** | 213 个因子没有一个读 `limit_up`/`limit_down`/`last_close` |
| **合计** | **213** | 与论文 Table 1 的 9+28+21+50+22+14+30+5+28+6 = 213 一致 |

- 需要 `vwap` 的因子：**58** 个（= 43 + 15）
- 需要 `open` 的因子：**32** 个 —— 现有 WORK-449 面板**没有 open 列**，这是硬缺口（见缺口清单 G-02）
- 需要 `amount`（或其 vwap×volume 退化式）的因子：**15** 个

## 二、按家族分布

| 家族 | 论文 Table 1 计数 | 代码模块 | 家族主题 | OHLCV 即可 | 需 vwap | 需 vwap+amount |
|---|---:|---|---|---:|---:|---:|
| `alpha_*` | 9 | `src/mlquant/features/_factors_alpha101.py` | Alpha101 子集（论文 §3.4：只取 9 条） | 9 | 0 | 0 |
| `better_*` | 28 | `src/mlquant/features/_factors_better.py` | VWAP 偏离 / 量价动量 | 8 | 10 | 10 |
| `best_*` | 21 | `src/mlquant/features/_factors_best.py` | 收盘位置（close-location）动量 | 16 | 5 | 0 |
| `old_*` | 50 | `src/mlquant/features/_factors_old.py` | 秩相关复合（legacy alpha 信号） | 33 | 17 | 0 |
| `stock_*` | 22 | `src/mlquant/features/_factors_stock.py` | 个股派生（波动 / CCI / 区间） | 17 | 3 | 2 |
| `extra_*` | 14 | `src/mlquant/features/_factors_extra.py` | 换手与成交额 | 10 | 2 | 2 |
| `add_*` | 30 | `src/mlquant/features/_factors_add.py` | 复合补充因子 | 25 | 5 | 0 |
| `change_*` | 5 | `src/mlquant/features/_factors_change.py` | 短窗加速度 | 5 | 0 | 0 |
| `original_*` | 28 | `src/mlquant/features/_factors_original.py` | 收盘/成交量直接统计 | 28 | 0 | 0 |
| `cs_rank_*` | 6 | `src/mlquant/features/_factors_market.py` | 截面市场宽度 | 4 | 1 | 1 |
| **合计** | **213** | 10 个模块 | — | **155** | **43** | **15** |

> 家族计数与论文 Table 1 逐项一致；官方 `legacy_factors.py:3` 的 docstring 写的是
> 「Ports all ~150 factors」，与实际注册的 213 个不符 —— 以代码为准，docstring 过时。

## 三、逐因子明细（213 行）

| # | 因子名 | 家族 | 代码位置 `path:line` | 需要字段 | 读 `panel.amount` | 新库来源 |
|---:|---|---|---|---|---|---|
| 1 | `alpha_001` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:21` | close | N | daily.close |
| 2 | `alpha_002` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:29` | open+close+volume | N | daily.open; daily.close; daily.vol(手→股) |
| 3 | `alpha_003` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:44` | open+volume | N | daily.open; daily.vol(手→股) |
| 4 | `alpha_004` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:53` | low | N | daily.low |
| 5 | `alpha_006` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:61` | open+volume | N | daily.open; daily.vol(手→股) |
| 6 | `alpha_007` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:68` | close | N | daily.close |
| 7 | `alpha_012` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:78` | close+volume | N | daily.close; daily.vol(手→股) |
| 8 | `alpha_053` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:87` | high+low+close | N | daily.high; daily.low; daily.close |
| 9 | `alpha_101` | `alpha_*` | `src/mlquant/features/_factors_alpha101.py:95` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 10 | `better_001` | `better_*` | `src/mlquant/features/_factors_better.py:21` | open+close+volume | N | daily.open; daily.close; daily.vol(手→股) |
| 11 | `better_002` | `better_*` | `src/mlquant/features/_factors_better.py:31` | open+close+volume | N | daily.open; daily.close; daily.vol(手→股) |
| 12 | `better_003` | `better_*` | `src/mlquant/features/_factors_better.py:42` | open+high+low+close+vwap | N | daily.open; daily.high; daily.low; daily.close; daily.amount÷daily.vol(派生) |
| 13 | `better_004` | `better_*` | `src/mlquant/features/_factors_better.py:52` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 14 | `better_005` | `better_*` | `src/mlquant/features/_factors_better.py:62` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 15 | `better_006` | `better_*` | `src/mlquant/features/_factors_better.py:72` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 16 | `better_007` | `better_*` | `src/mlquant/features/_factors_better.py:80` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 17 | `better_008` | `better_*` | `src/mlquant/features/_factors_better.py:89` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 18 | `better_009` | `better_*` | `src/mlquant/features/_factors_better.py:97` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 19 | `better_010` | `better_*` | `src/mlquant/features/_factors_better.py:113` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 20 | `better_011` | `better_*` | `src/mlquant/features/_factors_better.py:129` | high+low+close | N | daily.high; daily.low; daily.close |
| 21 | `better_012` | `better_*` | `src/mlquant/features/_factors_better.py:149` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 22 | `better_013` | `better_*` | `src/mlquant/features/_factors_better.py:166` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 23 | `better_014` | `better_*` | `src/mlquant/features/_factors_better.py:174` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 24 | `better_015` | `better_*` | `src/mlquant/features/_factors_better.py:182` | vwap | N | daily.amount÷daily.vol(派生) |
| 25 | `better_016` | `better_*` | `src/mlquant/features/_factors_better.py:204` | vwap | N | daily.amount÷daily.vol(派生) |
| 26 | `better_017` | `better_*` | `src/mlquant/features/_factors_better.py:224` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 27 | `better_018` | `better_*` | `src/mlquant/features/_factors_better.py:245` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 28 | `better_019` | `better_*` | `src/mlquant/features/_factors_better.py:266` | volume | N | daily.vol(手→股) |
| 29 | `better_020` | `better_*` | `src/mlquant/features/_factors_better.py:280` | close+volume | N | daily.close; daily.vol(手→股) |
| 30 | `better_021` | `better_*` | `src/mlquant/features/_factors_better.py:293` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 31 | `better_022` | `better_*` | `src/mlquant/features/_factors_better.py:301` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 32 | `better_023` | `better_*` | `src/mlquant/features/_factors_better.py:312` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 33 | `better_024` | `better_*` | `src/mlquant/features/_factors_better.py:323` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 34 | `better_025` | `better_*` | `src/mlquant/features/_factors_better.py:335` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 35 | `better_026` | `better_*` | `src/mlquant/features/_factors_better.py:347` | high+low+close | N | daily.high; daily.low; daily.close |
| 36 | `better_027` | `better_*` | `src/mlquant/features/_factors_better.py:358` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 37 | `better_028` | `better_*` | `src/mlquant/features/_factors_better.py:374` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 38 | `best_001` | `best_*` | `src/mlquant/features/_factors_best.py:19` | high+low+close | N | daily.high; daily.low; daily.close |
| 39 | `best_002` | `best_*` | `src/mlquant/features/_factors_best.py:26` | high+low+close | N | daily.high; daily.low; daily.close |
| 40 | `best_003` | `best_*` | `src/mlquant/features/_factors_best.py:33` | high+low+close | N | daily.high; daily.low; daily.close |
| 41 | `best_004` | `best_*` | `src/mlquant/features/_factors_best.py:40` | high+low+close | N | daily.high; daily.low; daily.close |
| 42 | `best_005` | `best_*` | `src/mlquant/features/_factors_best.py:47` | high+low+close | N | daily.high; daily.low; daily.close |
| 43 | `best_006` | `best_*` | `src/mlquant/features/_factors_best.py:54` | high+low+close | N | daily.high; daily.low; daily.close |
| 44 | `best_007` | `best_*` | `src/mlquant/features/_factors_best.py:61` | high+low+close | N | daily.high; daily.low; daily.close |
| 45 | `best_008` | `best_*` | `src/mlquant/features/_factors_best.py:68` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 46 | `best_009` | `best_*` | `src/mlquant/features/_factors_best.py:75` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 47 | `best_010` | `best_*` | `src/mlquant/features/_factors_best.py:85` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 48 | `best_011` | `best_*` | `src/mlquant/features/_factors_best.py:92` | open+close | N | daily.open; daily.close |
| 49 | `best_012` | `best_*` | `src/mlquant/features/_factors_best.py:100` | open+close | N | daily.open; daily.close |
| 50 | `best_013` | `best_*` | `src/mlquant/features/_factors_best.py:108` | open+close | N | daily.open; daily.close |
| 51 | `best_014` | `best_*` | `src/mlquant/features/_factors_best.py:116` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 52 | `best_015` | `best_*` | `src/mlquant/features/_factors_best.py:126` | high+low+close | N | daily.high; daily.low; daily.close |
| 53 | `best_016` | `best_*` | `src/mlquant/features/_factors_best.py:133` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 54 | `best_017` | `best_*` | `src/mlquant/features/_factors_best.py:141` | close | N | daily.close |
| 55 | `best_018` | `best_*` | `src/mlquant/features/_factors_best.py:148` | close | N | daily.close |
| 56 | `best_019` | `best_*` | `src/mlquant/features/_factors_best.py:155` | close | N | daily.close |
| 57 | `best_020` | `best_*` | `src/mlquant/features/_factors_best.py:162` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 58 | `best_021` | `best_*` | `src/mlquant/features/_factors_best.py:169` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 59 | `old_027` | `old_*` | `src/mlquant/features/_factors_old.py:19` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 60 | `old_028` | `old_*` | `src/mlquant/features/_factors_old.py:29` | open+volume | N | daily.open; daily.vol(手→股) |
| 61 | `old_029` | `old_*` | `src/mlquant/features/_factors_old.py:38` | close+volume | N | daily.close; daily.vol(手→股) |
| 62 | `old_030` | `old_*` | `src/mlquant/features/_factors_old.py:46` | close+volume | N | daily.close; daily.vol(手→股) |
| 63 | `old_031` | `old_*` | `src/mlquant/features/_factors_old.py:57` | close+volume | N | daily.close; daily.vol(手→股) |
| 64 | `old_032` | `old_*` | `src/mlquant/features/_factors_old.py:69` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 65 | `old_033` | `old_*` | `src/mlquant/features/_factors_old.py:81` | open+close | N | daily.open; daily.close |
| 66 | `old_034` | `old_*` | `src/mlquant/features/_factors_old.py:88` | close | N | daily.close |
| 67 | `old_035` | `old_*` | `src/mlquant/features/_factors_old.py:105` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 68 | `old_036` | `old_*` | `src/mlquant/features/_factors_old.py:116` | open+close | N | daily.open; daily.close |
| 69 | `old_037` | `old_*` | `src/mlquant/features/_factors_old.py:128` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 70 | `old_038` | `old_*` | `src/mlquant/features/_factors_old.py:142` | open+close+vwap | N | daily.open; daily.close; daily.amount÷daily.vol(派生) |
| 71 | `old_039` | `old_*` | `src/mlquant/features/_factors_old.py:153` | close+volume | N | daily.close; daily.vol(手→股) |
| 72 | `old_040` | `old_*` | `src/mlquant/features/_factors_old.py:168` | high+volume | N | daily.high; daily.vol(手→股) |
| 73 | `old_041` | `old_*` | `src/mlquant/features/_factors_old.py:179` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 74 | `old_042` | `old_*` | `src/mlquant/features/_factors_old.py:186` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 75 | `old_043` | `old_*` | `src/mlquant/features/_factors_old.py:196` | close+volume | N | daily.close; daily.vol(手→股) |
| 76 | `old_044` | `old_*` | `src/mlquant/features/_factors_old.py:208` | high+volume | N | daily.high; daily.vol(手→股) |
| 77 | `old_045` | `old_*` | `src/mlquant/features/_factors_old.py:216` | close+volume | N | daily.close; daily.vol(手→股) |
| 78 | `old_046` | `old_*` | `src/mlquant/features/_factors_old.py:227` | close | N | daily.close |
| 79 | `old_047` | `old_*` | `src/mlquant/features/_factors_old.py:239` | high+low+close | N | daily.high; daily.low; daily.close |
| 80 | `old_048` | `old_*` | `src/mlquant/features/_factors_old.py:250` | close+volume | N | daily.close; daily.vol(手→股) |
| 81 | `old_049` | `old_*` | `src/mlquant/features/_factors_old.py:260` | close | N | daily.close |
| 82 | `old_050` | `old_*` | `src/mlquant/features/_factors_old.py:269` | volume+vwap | N | daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 83 | `old_051` | `old_*` | `src/mlquant/features/_factors_old.py:281` | close | N | daily.close |
| 84 | `old_052` | `old_*` | `src/mlquant/features/_factors_old.py:290` | high+low+close | N | daily.high; daily.low; daily.close |
| 85 | `old_053` | `old_*` | `src/mlquant/features/_factors_old.py:303` | close | N | daily.close |
| 86 | `old_054` | `old_*` | `src/mlquant/features/_factors_old.py:312` | open+close | N | daily.open; daily.close |
| 87 | `old_055` | `old_*` | `src/mlquant/features/_factors_old.py:326` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 88 | `old_056` | `old_*` | `src/mlquant/features/_factors_old.py:340` | close+volume | N | daily.close; daily.vol(手→股) |
| 89 | `old_057` | `old_*` | `src/mlquant/features/_factors_old.py:356` | close | N | daily.close |
| 90 | `old_058` | `old_*` | `src/mlquant/features/_factors_old.py:364` | close+volume | N | daily.close; daily.vol(手→股) |
| 91 | `old_059` | `old_*` | `src/mlquant/features/_factors_old.py:372` | close+volume | N | daily.close; daily.vol(手→股) |
| 92 | `old_060` | `old_*` | `src/mlquant/features/_factors_old.py:382` | open+close | N | daily.open; daily.close |
| 93 | `old_061` | `old_*` | `src/mlquant/features/_factors_old.py:397` | vwap | N | daily.amount÷daily.vol(派生) |
| 94 | `old_062` | `old_*` | `src/mlquant/features/_factors_old.py:405` | volume+vwap | N | daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 95 | `old_063` | `old_*` | `src/mlquant/features/_factors_old.py:414` | volume+vwap | N | daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 96 | `old_064` | `old_*` | `src/mlquant/features/_factors_old.py:425` | open+low+volume | N | daily.open; daily.low; daily.vol(手→股) |
| 97 | `old_065` | `old_*` | `src/mlquant/features/_factors_old.py:437` | open+volume+vwap | N | daily.open; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 98 | `old_066` | `old_*` | `src/mlquant/features/_factors_old.py:448` | vwap | N | daily.amount÷daily.vol(派生) |
| 99 | `old_067` | `old_*` | `src/mlquant/features/_factors_old.py:455` | high+volume+vwap | N | daily.high; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 100 | `old_068` | `old_*` | `src/mlquant/features/_factors_old.py:468` | high+close+volume | N | daily.high; daily.close; daily.vol(手→股) |
| 101 | `old_069` | `old_*` | `src/mlquant/features/_factors_old.py:480` | close+volume | N | daily.close; daily.vol(手→股) |
| 102 | `old_070` | `old_*` | `src/mlquant/features/_factors_old.py:492` | vwap | N | daily.amount÷daily.vol(派生) |
| 103 | `old_071` | `old_*` | `src/mlquant/features/_factors_old.py:499` | close+volume | N | daily.close; daily.vol(手→股) |
| 104 | `old_072` | `old_*` | `src/mlquant/features/_factors_old.py:509` | low+volume+vwap | N | daily.low; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 105 | `old_073` | `old_*` | `src/mlquant/features/_factors_old.py:526` | vwap | N | daily.amount÷daily.vol(派生) |
| 106 | `old_074` | `old_*` | `src/mlquant/features/_factors_old.py:535` | close+volume | N | daily.close; daily.vol(手→股) |
| 107 | `old_075` | `old_*` | `src/mlquant/features/_factors_old.py:544` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 108 | `old_076` | `old_*` | `src/mlquant/features/_factors_old.py:559` | volume+vwap | N | daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 109 | `stock_001` | `stock_*` | `src/mlquant/features/_factors_stock.py:19` | open+close+volume | N | daily.open; daily.close; daily.vol(手→股) |
| 110 | `stock_002` | `stock_*` | `src/mlquant/features/_factors_stock.py:29` | open+close+volume | N | daily.open; daily.close; daily.vol(手→股) |
| 111 | `stock_003` | `stock_*` | `src/mlquant/features/_factors_stock.py:39` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 112 | `stock_004` | `stock_*` | `src/mlquant/features/_factors_stock.py:53` | close | N | daily.close |
| 113 | `stock_005` | `stock_*` | `src/mlquant/features/_factors_stock.py:63` | close+volume | N | daily.close; daily.vol(手→股) |
| 114 | `stock_006` | `stock_*` | `src/mlquant/features/_factors_stock.py:71` | close | N | daily.close |
| 115 | `stock_007` | `stock_*` | `src/mlquant/features/_factors_stock.py:79` | low+close+volume | N | daily.low; daily.close; daily.vol(手→股) |
| 116 | `stock_008` | `stock_*` | `src/mlquant/features/_factors_stock.py:96` | open+close | N | daily.open; daily.close |
| 117 | `stock_009` | `stock_*` | `src/mlquant/features/_factors_stock.py:112` | close | N | daily.close |
| 118 | `stock_010` | `stock_*` | `src/mlquant/features/_factors_stock.py:124` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 119 | `stock_011` | `stock_*` | `src/mlquant/features/_factors_stock.py:132` | high+low+close | N | daily.high; daily.low; daily.close |
| 120 | `stock_012` | `stock_*` | `src/mlquant/features/_factors_stock.py:144` | volume | N | daily.vol(手→股) |
| 121 | `stock_013` | `stock_*` | `src/mlquant/features/_factors_stock.py:157` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 122 | `stock_014` | `stock_*` | `src/mlquant/features/_factors_stock.py:169` | close+volume | N | daily.close; daily.vol(手→股) |
| 123 | `stock_015` | `stock_*` | `src/mlquant/features/_factors_stock.py:177` | close+volume+vwap | Y | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 124 | `stock_016` | `stock_*` | `src/mlquant/features/_factors_stock.py:193` | high+low+close | N | daily.high; daily.low; daily.close |
| 125 | `stock_017` | `stock_*` | `src/mlquant/features/_factors_stock.py:216` | high+close+volume+vwap | N | daily.high; daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 126 | `stock_018` | `stock_*` | `src/mlquant/features/_factors_stock.py:227` | close | N | daily.close |
| 127 | `stock_019` | `stock_*` | `src/mlquant/features/_factors_stock.py:238` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 128 | `stock_020` | `stock_*` | `src/mlquant/features/_factors_stock.py:247` | close+volume | N | daily.close; daily.vol(手→股) |
| 129 | `stock_021` | `stock_*` | `src/mlquant/features/_factors_stock.py:255` | high+low | N | daily.high; daily.low |
| 130 | `stock_022` | `stock_*` | `src/mlquant/features/_factors_stock.py:264` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 131 | `extra_001` | `extra_*` | `src/mlquant/features/_factors_extra.py:19` | high+low+close | N | daily.high; daily.low; daily.close |
| 132 | `extra_002` | `extra_*` | `src/mlquant/features/_factors_extra.py:26` | close+volume+vwap | N | daily.close; daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 133 | `extra_003` | `extra_*` | `src/mlquant/features/_factors_extra.py:41` | open+close | N | daily.open; daily.close |
| 134 | `extra_004` | `extra_*` | `src/mlquant/features/_factors_extra.py:49` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 135 | `extra_005` | `extra_*` | `src/mlquant/features/_factors_extra.py:60` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 136 | `extra_006` | `extra_*` | `src/mlquant/features/_factors_extra.py:69` | volume | N | daily.vol(手→股) |
| 137 | `extra_007` | `extra_*` | `src/mlquant/features/_factors_extra.py:77` | high+low | N | daily.high; daily.low |
| 138 | `extra_008` | `extra_*` | `src/mlquant/features/_factors_extra.py:88` | high+low+close | N | daily.high; daily.low; daily.close |
| 139 | `extra_009` | `extra_*` | `src/mlquant/features/_factors_extra.py:101` | close | N | daily.close |
| 140 | `extra_010` | `extra_*` | `src/mlquant/features/_factors_extra.py:115` | close | N | daily.close |
| 141 | `extra_011` | `extra_*` | `src/mlquant/features/_factors_extra.py:123` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 142 | `extra_012` | `extra_*` | `src/mlquant/features/_factors_extra.py:130` | open+high | N | daily.open; daily.high |
| 143 | `extra_013` | `extra_*` | `src/mlquant/features/_factors_extra.py:137` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 144 | `extra_014` | `extra_*` | `src/mlquant/features/_factors_extra.py:144` | high+low+close | N | daily.high; daily.low; daily.close |
| 145 | `add_001` | `add_*` | `src/mlquant/features/_factors_add.py:19` | close+volume | N | daily.close; daily.vol(手→股) |
| 146 | `add_002` | `add_*` | `src/mlquant/features/_factors_add.py:30` | close+volume | N | daily.close; daily.vol(手→股) |
| 147 | `add_003` | `add_*` | `src/mlquant/features/_factors_add.py:40` | close+volume | N | daily.close; daily.vol(手→股) |
| 148 | `add_004` | `add_*` | `src/mlquant/features/_factors_add.py:52` | high+low+close+volume | N | daily.high; daily.low; daily.close; daily.vol(手→股) |
| 149 | `add_005` | `add_*` | `src/mlquant/features/_factors_add.py:62` | close | N | daily.close |
| 150 | `add_006` | `add_*` | `src/mlquant/features/_factors_add.py:74` | close | N | daily.close |
| 151 | `add_007` | `add_*` | `src/mlquant/features/_factors_add.py:86` | close+volume | N | daily.close; daily.vol(手→股) |
| 152 | `add_008` | `add_*` | `src/mlquant/features/_factors_add.py:95` | close | N | daily.close |
| 153 | `add_009` | `add_*` | `src/mlquant/features/_factors_add.py:103` | high+low | N | daily.high; daily.low |
| 154 | `add_010` | `add_*` | `src/mlquant/features/_factors_add.py:111` | high+low+close | N | daily.high; daily.low; daily.close |
| 155 | `add_011` | `add_*` | `src/mlquant/features/_factors_add.py:121` | volume+vwap | N | daily.vol(手→股); daily.amount÷daily.vol(派生) |
| 156 | `add_012` | `add_*` | `src/mlquant/features/_factors_add.py:127` | open+close+vwap | N | daily.open; daily.close; daily.amount÷daily.vol(派生) |
| 157 | `add_013` | `add_*` | `src/mlquant/features/_factors_add.py:138` | high+low+vwap | N | daily.high; daily.low; daily.amount÷daily.vol(派生) |
| 158 | `add_014` | `add_*` | `src/mlquant/features/_factors_add.py:145` | close | N | daily.close |
| 159 | `add_015` | `add_*` | `src/mlquant/features/_factors_add.py:153` | open+close | N | daily.open; daily.close |
| 160 | `add_016` | `add_*` | `src/mlquant/features/_factors_add.py:161` | vwap | N | daily.amount÷daily.vol(派生) |
| 161 | `add_017` | `add_*` | `src/mlquant/features/_factors_add.py:169` | vwap | N | daily.amount÷daily.vol(派生) |
| 162 | `add_018` | `add_*` | `src/mlquant/features/_factors_add.py:177` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 163 | `add_019` | `add_*` | `src/mlquant/features/_factors_add.py:184` | open+close | N | daily.open; daily.close |
| 164 | `add_020` | `add_*` | `src/mlquant/features/_factors_add.py:192` | close | N | daily.close |
| 165 | `add_021` | `add_*` | `src/mlquant/features/_factors_add.py:202` | close+volume | N | daily.close; daily.vol(手→股) |
| 166 | `add_022` | `add_*` | `src/mlquant/features/_factors_add.py:209` | volume | N | daily.vol(手→股) |
| 167 | `add_023` | `add_*` | `src/mlquant/features/_factors_add.py:219` | close | N | daily.close |
| 168 | `add_024` | `add_*` | `src/mlquant/features/_factors_add.py:227` | close | N | daily.close |
| 169 | `add_025` | `add_*` | `src/mlquant/features/_factors_add.py:235` | close | N | daily.close |
| 170 | `add_026` | `add_*` | `src/mlquant/features/_factors_add.py:245` | volume | N | daily.vol(手→股) |
| 171 | `add_027` | `add_*` | `src/mlquant/features/_factors_add.py:255` | close+volume | N | daily.close; daily.vol(手→股) |
| 172 | `add_028` | `add_*` | `src/mlquant/features/_factors_add.py:265` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 173 | `add_029` | `add_*` | `src/mlquant/features/_factors_add.py:272` | open+high+low+close | N | daily.open; daily.high; daily.low; daily.close |
| 174 | `add_030` | `add_*` | `src/mlquant/features/_factors_add.py:279` | close | N | daily.close |
| 175 | `change_001` | `change_*` | `src/mlquant/features/_factors_change.py:14` | close | N | daily.close |
| 176 | `change_002` | `change_*` | `src/mlquant/features/_factors_change.py:24` | close | N | daily.close |
| 177 | `change_003` | `change_*` | `src/mlquant/features/_factors_change.py:32` | volume | N | daily.vol(手→股) |
| 178 | `change_004` | `change_*` | `src/mlquant/features/_factors_change.py:40` | high+low | N | daily.high; daily.low |
| 179 | `change_005` | `change_*` | `src/mlquant/features/_factors_change.py:49` | close | N | daily.close |
| 180 | `original_001` | `original_*` | `src/mlquant/features/_factors_original.py:16` | close | N | daily.close |
| 181 | `original_002` | `original_*` | `src/mlquant/features/_factors_original.py:25` | close | N | daily.close |
| 182 | `original_003` | `original_*` | `src/mlquant/features/_factors_original.py:33` | close | N | daily.close |
| 183 | `original_004` | `original_*` | `src/mlquant/features/_factors_original.py:41` | close | N | daily.close |
| 184 | `original_005` | `original_*` | `src/mlquant/features/_factors_original.py:47` | volume | N | daily.vol(手→股) |
| 185 | `original_006` | `original_*` | `src/mlquant/features/_factors_original.py:53` | close+volume | N | daily.close; daily.vol(手→股) |
| 186 | `original_007` | `original_*` | `src/mlquant/features/_factors_original.py:59` | close | N | daily.close |
| 187 | `original_008` | `original_*` | `src/mlquant/features/_factors_original.py:65` | volume | N | daily.vol(手→股) |
| 188 | `original_009` | `original_*` | `src/mlquant/features/_factors_original.py:71` | close+volume | N | daily.close; daily.vol(手→股) |
| 189 | `original_010` | `original_*` | `src/mlquant/features/_factors_original.py:77` | close+volume | N | daily.close; daily.vol(手→股) |
| 190 | `original_011` | `original_*` | `src/mlquant/features/_factors_original.py:83` | close | N | daily.close |
| 191 | `original_012` | `original_*` | `src/mlquant/features/_factors_original.py:89` | volume | N | daily.vol(手→股) |
| 192 | `original_013` | `original_*` | `src/mlquant/features/_factors_original.py:95` | close | N | daily.close |
| 193 | `original_014` | `original_*` | `src/mlquant/features/_factors_original.py:101` | volume | N | daily.vol(手→股) |
| 194 | `original_015` | `original_*` | `src/mlquant/features/_factors_original.py:107` | close | N | daily.close |
| 195 | `original_016` | `original_*` | `src/mlquant/features/_factors_original.py:115` | close | N | daily.close |
| 196 | `original_017` | `original_*` | `src/mlquant/features/_factors_original.py:123` | close | N | daily.close |
| 197 | `original_018` | `original_*` | `src/mlquant/features/_factors_original.py:131` | close | N | daily.close |
| 198 | `original_019` | `original_*` | `src/mlquant/features/_factors_original.py:139` | close | N | daily.close |
| 199 | `original_020` | `original_*` | `src/mlquant/features/_factors_original.py:147` | close | N | daily.close |
| 200 | `original_021` | `original_*` | `src/mlquant/features/_factors_original.py:155` | close | N | daily.close |
| 201 | `original_022` | `original_*` | `src/mlquant/features/_factors_original.py:163` | close | N | daily.close |
| 202 | `original_023` | `original_*` | `src/mlquant/features/_factors_original.py:171` | close | N | daily.close |
| 203 | `original_024` | `original_*` | `src/mlquant/features/_factors_original.py:179` | close | N | daily.close |
| 204 | `original_025` | `original_*` | `src/mlquant/features/_factors_original.py:187` | close | N | daily.close |
| 205 | `original_026` | `original_*` | `src/mlquant/features/_factors_original.py:195` | close | N | daily.close |
| 206 | `original_027` | `original_*` | `src/mlquant/features/_factors_original.py:203` | close | N | daily.close |
| 207 | `original_028` | `original_*` | `src/mlquant/features/_factors_original.py:211` | close | N | daily.close |
| 208 | `cs_rank_amount` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:58` | volume+vwap | Y | daily.vol(手→股); daily.amount÷daily.vol(派生); daily.amount(千元→元) |
| 209 | `cs_rank_avg` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:50` | close+vwap | N | daily.close; daily.amount÷daily.vol(派生) |
| 210 | `cs_rank_close` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:18` | close | N | daily.close |
| 211 | `cs_rank_high` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:34` | high+close | N | daily.high; daily.close |
| 212 | `cs_rank_low` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:42` | low+close | N | daily.low; daily.close |
| 213 | `cs_rank_open` | `cs_rank_*` | `src/mlquant/features/_factors_market.py:26` | open+close | N | daily.open; daily.close |

## 四、复算方法

```bash
# 依赖：python>=3.9，仅用标准库 ast（不需要 torch）
python scripts/enumerate_mlquant_factors.py \
    --repo /path/to/ml-quant-trading \
    --out   docs/mlquant_factor_fields.csv
```

脚本做的事，逐条可审：

1. 扫 `src/mlquant/features/_factors_*.py`，取 `@register_legacy_factor("name")` 装饰的函数 → 因子名 + 定义行号；
2. 在函数体 AST 里找 `panel.<attr>` 形式的属性访问，命中行情字段白名单即计入；
3. 沿同模块辅助函数**递归**展开（例如 `_close_loc` → high/low/close）；
4. 再沿**跨模块**辅助函数展开（`_factors_extra.py` 等从 `legacy_factors` 导入的 `_close_loc` / `_amount`）；
5. `panel.returns` 归到 `close` + `last_close`；`panel.mask` 不计入字段。

**人工抽检**（本轮做过，四处与 AST 结论一致）：

| 因子 | 源码 | AST 结论 | 人工核对 |
|---|---|---|---|
| `better_001` | `_factors_better.py:20-28` | open+close+volume | `panel.open / delay(close,1) * log2(volume+1)` ✓ |
| `extra_001` | `_factors_extra.py:18-22` | close+high+low | `-_close_loc(panel)` → (2C−H−L)/(H−L) ✓ |
| `extra_002` | `_factors_extra.py:24-37` | close+vwap+volume | `vwap−close` 的 ts_max/ts_min + `delta(volume,3)` ✓ |
| `original_001` | `_factors_original.py:15-21` | close | `delta(close,1)/close` → `ts_std(...,20)` → `cs_zscore` ✓ |
| `cs_rank_amount` | `_factors_market.py:57-61` | amount(+vwap+volume 退化) | `_amount(panel)` ✓ |
