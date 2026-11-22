# quant-lab

> 一个可复现的量化研究工作台。把「取数探测 + 口径裁定 + 独立复算」的重复劳动从一次性 issue 目录里抽出来，变成仓库里的常驻资产。

**本仓库是 public。行情快照、产品净值、图件产物一律不入库**（见 `.gitignore`）。仓库里只放：口径契约、实验台账、脚本、校验清单（manifest）与报告模板。

---

## 一、这个仓库做什么

| 做 | 不做 |
|---|---|
| 固定数据源的**可用性结论**（哪些能用、哪些被限流、哪些只有截面） | 不做数据源的爬虫维护；只登记「能不能用」与「为什么不能用」 |
| 固定**字段口径**（单位、时区、复权、缺失、对齐） | 不存原始行情；行情随时可重下，manifest 负责证明「用的是哪一版」 |
| 固定**指标定义**（年化、波动、夏普、回撤、换手、代理口径的已知偏差） | 不做投资建议、不评个股、不给目标价、不承诺收益 |
| 固定**实验台账**（参数 → 结果 → 结论状态 → 失败记录） | 不只登记成功的实验；被证伪的结论同样占一行 |
| 提供**端到端脚本**（取数 → 指标 → 配图 → 报告 → 独立复算） | 不提供「一键出结论」的魔法；每个数字都能追到脚本与快照 |
| 提供**独立复算闸门**（`audit/`） | 审计脚本不复用建模阶段的函数 |

## 二、目录约定

```
quant-lab/
├── README.md                    # 本文件：做什么 / 不做什么 / 目录约定
├── .gitignore                   # 排除行情快照、CSV、图件、报告成品
│
├── docs/                        # 流程说明与验证记录（人读）
│   ├── pipeline.md              # 端到端流程、每步的输入/输出/验收门
│   └── second_article_trial.md  # 第二篇文章的复用验证记录（前 vs 后实测）
│
├── data_contracts/              # 口径契约（机器与人都读，跨实验复用）
│   ├── data_source_registry.md  # 数据源可用性登记表 + 踩坑记录
│   ├── field_dictionary.md      # 字段字典：单位、时区、频率、缺失、复权、对齐
│   └── metric_definitions.md     # 指标定义 + 已知偏差（成交额代理等）
│
├── experiments/                 # 实验台账（每次实验一行 + 该次产出）
│   ├── registry.md              # 参数-结果登记表（模板 + 已完成实验）
│   ├── WORK-404/                # 首次实验：文章数字主张的可复现性核验
│   │   ├── snapshot_manifest.json  # 快照清单（sha256、行数、区间）—— 路径为仓库相对
│   │   ├── metrics.json            # 全部计算结果与出处
│   │   ├── metrics_table.csv       # 人读指标表
│   │   └── audit_findings.json     # 独立复算结论
│   └── WORK-413-S2/             # 第二次实验：第二篇同类文章的复用验证（省了多少）
│       ├── README.md            #   证据目录说明 + 复跑命令
│       ├── source_article.md     #   原文正文（逐字照抄）
│       ├── snapshot_manifest.json  # 快照清单（3 条指数 × 131 行）
│       ├── metrics.json            # 6 条数字主张的判定表 D1–D6 + 跨源校验
│       ├── probe_armA_no_registry.py  # ARM A（前）：无登记表的取数探测路线
│       ├── armA_result.json          #   其实测输出（6 次 / 2.50 s）
│       ├── probe_armB_registry_guided.py  # ARM B（后）：有登记表的取数 + 口径裁定
│       └── armB_result.json              #   其实测输出（5 次 / 1.18 s）
│                                    #   （复跑输出写到 rerun/，.gitignore 排除）
│
├── audit/                       # 独立复算闸门
│   ├── README.md                # 审计规则与调用契约
│   └── audit_recompute.py       # 独立复算脚本（禁止 import 建模阶段函数）
│
├── scripts/                     # 端到端脚本（仓库相对路径，无写死的任务目录）
│   ├── fetch_market_data.py     # 取数 → data/<EXP>/*.csv + snapshot_manifest.json
│   ├── build_analysis.py        # 指标 → metrics.json + metrics_table.csv
│   ├── build_figures.py         # 配图 → figures/*.png|pdf|svg
│   └── build_report.py          # 单文件 HTML 报告（图片 base64 内嵌）
│
├── templates/                   # 报告模板
│   └── report_template.html     # WORK-404 模板，作为新文章的参照实现
│
├── data/                        # 行情快照（.gitignore 排除；不进 public 仓库）
│   └── <EXP>/                   #   按实验 id 分目录，避免不同实验互相覆盖
└── figures/                     # 图件产物（.gitignore 排除；由 build_figures.py 重建）
```

**三条硬约定**

1. **实验目录 = 实验 id**。`experiments/<EXP>/` 放该次实验的加工层产出与校验清单；`data/<EXP>/` 放该次实验的原始层快照。两者同名，对齐即可比。
2. **原始层只追加，加工层可重建**。`data/<EXP>/*.csv` 写盘后不改写；`experiments/<EXP>/` 下的 `metrics.json` 等可由脚本重建。快照身份由 `snapshot_manifest.json` 的 sha256 锁定。
3. **对外数字只认审计产出**。任何要出现在对外材料里的数字，必须能由 `audit/audit_recompute.py` 在同一份快照上独立复算出来。

## 三、跑一遍

环境：Python 3.13 + pandas / numpy / matplotlib / requests。Windows PowerShell 或 Git Bash 均可。

```bash
# 1. 取数：写 data/<EXP>/*.csv 与 experiments/<EXP>/snapshot_manifest.json
python scripts/fetch_market_data.py --exp WORK-404

# 2. 指标：写 experiments/<EXP>/metrics.json 与 metrics_table.csv
python scripts/build_analysis.py --exp WORK-404

# 3. 配图：写 figures/*.png|pdf|svg 与对齐/碰撞审计 JSON
python scripts/build_figures.py --exp WORK-404

# 4. 独立复算：写 experiments/<EXP>/audit_findings.json（退出码 0 = 无 blocking）
python audit/audit_recompute.py --exp WORK-404

# 5. 报告：写 experiments/<EXP>/report.html（单文件，双击即开）
python scripts/build_report.py --exp WORK-404
```

`--exp` 省略时默认取环境变量 `QUANTLAB_EXP`，再默认 `WORK-404`。

**以上五条命令不需要任何环境变量。** 五个脚本都在 `main()` 入口把 `sys.stdout` / `sys.stderr` 显式切到 UTF-8（`_force_utf8_stdio()`），Windows 默认的 GBK(cp936) 控制台、Git Bash、以及「输出重定向到文件或管道」三种情形都能直接跑完并返回真实退出码。审计侧的契约与调用方式见 `audit/README.md`。

**取数窗口**：`scripts/fetch_market_data.py` 顶部的 `START` / `END` 是硬编码的实验窗口。换实验时改这两个常量，并用新的 `--exp` 目录，避免污染既有快照。

**配图的对齐审计**依赖 `nature-figure` 技能的脚本。路径可用环境变量 `NATURE_FIGURE_SCRIPTS` 指定；找不到时 `build_figures.py` 会打印一条提示并跳过对齐门（图仍然生成），不会中断流水线。

## 四、已经跑了哪些实验

`experiments/registry.md` 的每一行是一次实验。两行都已完成：

- **`WORK-404`**（2026-11-15）：核验一篇中国证券报刊的量化私募行业调查稿，看它的数字主张能否被公开数据独立复现。
  结论摘要：原文是行业调查稿、**不含可编码策略**；可复现的是它的市场事实层主张——六条数字主张中三条成立、两条被实测证伪、一条因缺产品级数据不可复现。两条被证伪的结论同样登记在册。
- **`WORK-413-S2`**（2026-11-16）：**quant-lab 建仓后的首篇复用验证**。核验界面新闻同类型调查稿（2026-11-12）的 6 条数字主张，并**量化「仓库化到底省了多少」**。
  结论摘要：索引层可核 / 产品层不可核的分界线与 WORK-404 **同构，未重新推导**；口径裁定组合试算 **42 → 7 次**、取数探测 HTTP 请求 **6 → 5 次**（均为本轮 A/B **实测**，ARM A 的 6 次里有 4 次注定失败）；另附 WORK-404 的 **重建基线 ≈51 次**（**重建值，非实测**，仅作跨任务参照）。三个数字各自的出处、窗口与证据强度见 `docs/second_article_trial.md` §九。本行同时带一个**已知缺口**：S2 的判定表未过 `audit/audit_recompute.py` 独立复算。

## 五、政策原文库

`policy/` 是宏观 + 5 行业（储能/电力设备、新能源车、半导体、医药、有色/锂）的
官方政策原文采集层：白名单 YAML、双时间轴（发布日 / 生效日）、申万一级行业标签、
断点续跑、parquet 索引与 WeKnora 同步。数据落 `/srv/share/quantdb/policy`，
正文不进 public 仓库。入口与字段见 [`policy/README.md`](policy/README.md)。

## 六、加一个新实验

1. 在 `experiments/registry.md` 顶部插入一行空行（模板已在文件里）。
2. 复制 `templates/report_template.html` 改写，或直接跑 `scripts/build_report.py` 验证模板可用。
3. 改 `scripts/fetch_market_data.py` 的窗口常量，用新 `--exp` 取数。
4. 跑完五步，把参数、结果、结论状态（含失败与被证伪项）填回 `registry.md`。
5. 口径有新增或推翻时，**同步改 `data_contracts/` 下的三份契约**——契约才是复用的单位。

## 七、边界

- 本仓库只做可复现的量化研究与工程沉淀，**不产出投资建议、评级、目标价或收益承诺**。
- 任何对外报告首页须保留三行免责：本报告为内部研究模拟、撰写方非持牌机构、不构成投资建议与收益承诺。
- 「原文说的」与「实测的」必须能分开：前者进 `registry.md` 的引用栏，后者进 `metrics.json`，两者由 `audit_recompute.py` 分别校验。
- 不接入付费或受限数据源，不绕过登录、验证码、签名、付费墙与接口限流。数据源不可用时记为「未取到」，不用代理指标冒充原始字段。
