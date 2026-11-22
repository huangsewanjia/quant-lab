# 政策原文库（P1）

官方白名单政策原文采集：宏观 + 5 个行业，时间窗 2016-01-01 起，正文纯文本 +
发布/生效双时间轴 + 申万一级行业标签。数据落在 `/srv/share/quantdb/policy`，
仓库只放代码、配置、测试与本文档（public 仓库，正文与 HTML 不入库）。

## 1. 源清单

白名单在 [`sources.yaml`](sources.yaml)，按发布主体限定，不做站内全量镜像。

| 源 id | 发布主体 | 列表入口 | 列表方式 | 时间覆盖 |
|---|---|---|---|---|
| `gov_gw` | 国务院 | 中国政府网·国务院政策文件库 | 官方 JSON 检索 API | 2016–2026 全量 |
| `gov_bm` | 国务院各部门 | 中国政府网·部门政策文件库 | 官方 JSON 检索 API | 2016–2026 全量 |
| `ndrc` | 国家发展改革委 | `xxgk/zcfb/{fzggwl,tz,gg,ghwb}` | 静态 HTML + `index_N.html` | 站点仅保留最近约 9 页 |
| `mof` | 财政部 | `zhengwuxinxi/zhengcefabu` | 静态 HTML + `index_N.htm` | 站点仅保留最近约 19 页 |
| `pbc` | 中国人民银行 | 条法司「规章」「公告」 | 静态 HTML | 站点仅保留最近 1–2 页 |
| `mnr` | 自然资源部 | `gk/tzgg` 通知公告（按标题过滤） | 静态 HTML | 站点仅保留最近约 35 页 |

行业覆盖关系：

| 行业 | 主要来源 |
|---|---|
| 储能 / 电力设备 | `gov_bm`（发改委、能源局、工信部）、`ndrc` 通知/公告 |
| 新能源车 | `gov_bm`（工信部、财政部）、`mof` |
| 半导体 | `gov_bm`（工信部、发改委、国务院） |
| 医药 | `gov_bm`（药监局、卫健委、医保局）；药监局与卫健委官网有 412 反爬，未直连 |
| 有色 / 锂 | `gov_bm`（工信部、自然资源部、发改委）、`mnr` |

## 2. 目录与字段

```
/srv/share/quantdb/policy/
├── raw/<源>/<yyyy>/<doc_id>.txt        # 正文纯文本，保留段落
├── raw/<源>/<yyyy>/<doc_id>.html.gz    # 原始 HTML（不存 PDF 原件）
├── raw/<源>/<yyyy>/<doc_id>.meta.json  # 单篇元数据
├── meta/index.parquet                  # 全量汇总（字段见下）
├── meta/stats.json                     # 源×年矩阵、生效日覆盖率、unlabeled 比例
├── meta/source-year.csv                # 源×年篇数矩阵
├── meta/effective-coverage.csv         # 每源生效日覆盖
├── meta/duplicates.jsonl               # 去重记录（含 reprint_of）
├── meta/rejects.jsonl                  # 未入库原因
├── meta/state/<源>.json                # 断点队列（--resume 使用）
├── meta/runs/<ts>.json                 # 每次运行汇总
└── meta/sample-*.csv                   # 人工抽查样本
```

`doc_id = sha256(source_id + "\n" + url)[:16]`，URL 不变则 id 稳定。

| 字段 | 说明 |
|---|---|
| `doc_id` | 稳定主键 |
| `title` | 标题（去掉站点后缀与文号尾巴） |
| `agency` | 发文机关；取自列表元数据或正文「发文机关」 |
| `document_number` | 文号；缺失写 `unknown` 并计数 |
| `publish_date` | 发布日（YYYY-MM-DD） |
| `effective_date` | 生效日；**原文未写明写 `unknown`，禁止用发布日顶替** |
| `expiry_date` | 失效日；未写明写 `unknown` |
| `industry_tags` | 申万一级行业，可多值；无命中写 `["unlabeled"]` |
| `topic_tags` | 主题标签（货币政策/财政政策/产业政策等） |
| `tag_basis` | 打标依据（`keyword` / `agency:<行业>` / `none`） |
| `url` | 原文 URL |
| `fetched_at` | 抓取时间（UTC） |
| `sha256` | 正文 sha256 |
| `text_bytes` / `text_chars` | 正文字节数 / 字符数 |
| `source_column` | 来源栏目 |

## 3. 打标规则

- 关键词表在 [`tagging.py`](tagging.py)，申万一级 31 类 + 主题 10 类。
- 命中门槛：标题命中，或正文命中同一行业关键词 ≥ 3 次；每次最多保留 3 个行业
  （按「标题命中优先、正文次数」排序）。
- 机关先验只用于明确的专管机关（能源局→电力设备、药监局/卫健委/医保局→医药生物、
  证监会→非银金融）。
- 都不命中写 `unlabeled`，不做语义猜测；比例由 `meta/stats.json` 统计。

## 4. 复跑命令

```bash
PY=/home/moyongkai/.venvs/quantdb/bin/python

# 全量回填（2016-01-01 起），断点续跑
$PY policy/collect.py --backfill --resume --source all

# 单源增量（默认从上次成功日往前 3 天）
$PY policy/collect.py --source gov_gw --resume

# 只列举不抓正文（排查选择器 / 栏目改版）
$PY policy/collect.py --source ndrc --list-only

# 重建索引与统计；--retag 用当前规则重打标签
$PY policy/indexer.py --retag --sample 30

# 同步到 WeKnora「政策原文库」
$PY policy/sync_weknora.py --limit 0

# 收尾跨源去重（并发跑多个源之后跑一次）
$PY policy/dedupe.py

# 测试
$PY -m pytest policy/tests -q
```

> 不同源并发跑时，各自的去重集合是启动时的快照，可能漏掉同一时段另一源的转载；
> 收尾跑 `dedupe.py` 会按 sha256 合并并写 `duplicate_of`。生产调度建议按源串行。

## 5. 质量门与已知缺口

- 入库门槛：标题 ≥ 6 字、正文 ≥ 400 字符、发布日可解析。低于门槛写
  `meta/rejects.jsonl`，不硬塞。
- 去重：同一 URL、同一正文 sha256、同一强文号（含年份的机关文号）只留一份；
  转载件写 `meta/duplicates.jsonl` 的 `duplicate_of`，规范件 `reprint_count` 累加。
- `effective_date` 是硬口径：`unknown` 单独计数，回测按 `effective_date` 入样时
  必须显式剔除 `unknown`。
- **PDF 附件只抽正文，不存原件**：发改委令、部分部委通知的 HTML 只有封面页，正文在
  PDF 附件。采集器在 HTML 正文 < 400 字符时下载附件、用 `pypdf` 抽文本，正文标记
  `text_source=html+pdf`；PDF 文件本身不落盘。OFD 与扫描件（无文字层）仍会以
  「正文过短」拒收，数量见 `meta/rejects.jsonl`。
- 药监局（nmpa.gov.cn）、卫健委（nhc.gov.cn）返回 412 反爬，未绕过；医药行业
  以 `gov_bm` 中的部门文件覆盖。
- `ndrc` / `mof` / `pbc` / `mnr` 的列表页只保留最近若干页，2016–2024 的
  对应主体文件由 `gov_bm`（部门政策文件库）补齐；源×年矩阵中为 0 的年份即此原因。
- `gov_bm` 2016–2017 只有个位数：政府网部门文件库 2018 年改版后迁移，早期条目未回填；
  这两年由 `gov_gw`（国务院文件）与 `ndrc` 覆盖。

## 6. WeKnora

- 知识库「政策原文库」id：`308ead6b-636b-4937-addd-b1ddf6271a95`。
- MCP 只能 `add_document` 到已有库，不能建库；本单用 WeKnora REST
  `POST /knowledge-bases` 建库，再按 Markdown 逐篇写入。
- 每篇 Markdown 头部带发文机关、文号、发布日、生效日、失效日、行业/主题标签、
  原文链接与 `doc_id`；`effective_date=unknown` 在正文前显式声明。
- 同步是幂等的：已存在标题跳过，追加日志在 `meta/weknora-sync.jsonl`。
