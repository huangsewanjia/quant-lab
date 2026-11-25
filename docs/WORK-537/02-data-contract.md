# WORK-537 · 阶段 2 数据契约（金融数据工程师）

输入=docs/WORK-537/01-hypothesis.md
本阶段产出什么=来源授权结论、字段字典、时点口径、快照标识与校验值、可重复运行命令
本阶段工具调用上限=探测脚本 200 次请求（硬上限，脚本内 `--max-requests` 强制）；上下文写入仅限摘要

## 1. 数据契约（先确认，后使用）

| 项 | 契约 |
|---|---|
| 数据对象 | 「渠道可抓性」实测记录 + Top-5 渠道的单篇公开正文 |
| 数据来源 | 29 个公开站点入口 URL（清单见 `scripts/work537_probe.py` 的 `CHANNELS`） |
| 授权范围 | **仅公开、无需账号、无需付费墙的页面**；遵守各站 robots.txt |
| 禁止 | 绕过登录/付费墙/验证码/Cloudflare；注册账号；订阅；抓 PDF 全文；全站爬 |
| 频率 | 本轮一次性快照；建议后续每周一次增量（见 brief） |
| 时点 | 2026-11-23（UTC+8）单次抓取；**发布日期取页面自身标注**，抓取时间不等于发布日期 |
| 主键 | `channel_id`（脚本内稳定标识） |

**授权结论**：29 个目标中，全部为面向公众开放的栏目页/接口页；本轮**未使用任何账号、Cookie、付费凭据或代理**。UA 为普通浏览器 UA（SEC 的 `data.sec.gov` 为 SEC 官方公开接口，公开文档要求声明式 UA——本轮实测浏览器 UA 即返回 200，未启用声明式 UA 变体）。robots.txt 判定结果逐条记录在 `02-data.csv` 的 `robots状态` / `robots命中规则` 列；命中 Disallow 的路径**不抓正文**。

### 1.1 判定口径（实测后定稿，审计阶段修正过 4 处）

| 指标 | 判定口径 | 判定值 |
|---|---|---|
| HTTP 可达 | 单次 GET 的状态码 | 200 / 3xx / 403 / 404 / 5xx / 超时 |
| 可解析列表 | 返回体含**可解析的标题 + 日期**（各 1 条真实样例）——**这是 issue 的验收口径，也是「直连」的判据** | 是 / 否 |
| 列表日期来源 | 列表页日期**只取可见文本**；HTML 元数据（如 `dateCreated=2012-01-30`）与服务端时间戳（如 `<!-- 2026-11-23 at server -->`）单列为参考项，**不充当发布日期** | 列表可见文本 / HTML 元数据 / 未取到 |
| 无年份日期 | 「Nov 22」「9 November」按页内最新完整日期推断年份；比参考日晚 45 天以上判为上一年 | ISO 日期（推断，来源标注为「列表可见文本」） |
| 是否需 JS | **取不到「标题+日期」= 列表未在 HTML 里渲染**；另：可见文本 <800 字符，或正文已很长时页面上出现 JS 提示语 | 需JS / 不需JS |
| 抓取合规 | robots.txt 按 **落地 URL 的 host** 取（不同 host 的 robots 不通用）；按 RFC 9309 取最具体 UA 组、最长匹配优先；命中 Disallow 的路径**根本不发正文请求** | Allow / Disallow / robots 不可读 |
| 是否含评级/目标价 | **可见文本全文**出现 rating / price target / 目标价 等字样；**未取到原文的渠道一律记「未取到原文」**，不记「无」 | 有 / 部分 / 无 / 未取到原文 |
| 建议 | 直连＝采；需JS/403/需登录/出口不可达＝观察；robots 禁或不可读＝不采 | 采 / 观察 / 不采 |

## 2. 字段字典（`docs/WORK-537/02-data.csv`）

| 字段 | 含义 | 单位/取值 | 缺失规则 | 时点口径 |
|---|---|---|---|---|
| 渠道 | 渠道显示名 | 文本 | 不允许空 | — |
| 归属机构/媒体 | 运营主体 | 文本 | 不允许空 | — |
| 类型 | 渠道类别 | 投行官网/财经媒体/监管披露/聚合平台/社区 | 不允许空 | — |
| 入口URL | 人工可见的入口 | 文本（URL） | 不允许空 | — |
| 最终URL | 跟随重定向后的落地页 | URL | 不允许空 | 抓取瞬间 |
| HTTP状态 | 响应状态码 | 整数 | 不允许空 | 抓取瞬间 |
| 重定向链 | 实际尝试过的 URL 及状态 | 文本 | 不允许空 | 抓取瞬间 |
| Content-Type | 响应类型 | 文本 | 允许空 | 抓取瞬间 |
| 字节数 | 响应体字节 | 整数 | 允许空 | 抓取瞬间 |
| 页面标题 | HTML `<title>`（JSON 接口取业务字段） | 文本 | 允许空（记「未取到」） | 页面标注 |
| 页面日期(首个) | 页面文本中出现的第一个可解析日期 | ISO 日期 | 允许空 | **页面自身标注** |
| 页面日期(页内最新) | 页面文本中出现的最大日期，近似最新文章日 | ISO 日期 | 允许空 | **页面自身标注** |
| 可见文本长度 | 去标签后可见文本字符数 | 整数 | 不允许空 | 抓取瞬间 |
| 是否需JS | 正文是否需 JS 渲染 | 是/否 | 不允许空 | 抓取瞬间 |
| 是否检出付费墙标记 | 首屏是否出现订阅/登录提示 | 是/未检出 | 不允许空 | 抓取瞬间 |
| robots状态 | robots.txt 的 HTTP 状态 | 整数 | 不允许空 | 抓取瞬间 |
| robots命中规则 | 命中的 Disallow/Allow 规则原文 | 文本 | 允许空（无规则） | 抓取瞬间 |
| 可抓性 | 综合判定 | 直连/需JS/403/需登录/需登录·付费墙/robots禁/404入口失效/5xx/超时 | 不允许空 | 抓取瞬间 |
| 实测证据 | 状态码 + 标题/日期 或 失败原文（≤80 字） | 文本 | **不允许空** | 抓取瞬间 |
| 是否含评级/目标价 | 页面是否出现评级/目标价字样 | 有/部分/无/未取到原文 | 不允许空 | 页面标注 |
| 内容形态/更新频率/覆盖范围 | 渠道属性（人工判读 + 页面佐证） | 文本 | 不允许空 | 页面标注 |
| 抓取时间 / UA | 本轮抓取时刻与所用 UA | ISO 时间 / 文本 | 不允许空 | 抓取瞬间 |

**时点口径要点**：`页面日期(页内最新)` 是页面上出现的最大日期，**近似**最新文章日期，不等于逐条核对过每篇文章的发布日期；`日期=未取到` 的渠道不下任何「更新频率」的量化结论。

## 3. 抓取正文样例的字段（`raw/foreign_research/<channel>/<date>/<slug>.meta.json` 对应的 `meta.json`）

| 字段 | 含义 | 缺失规则 |
|---|---|---|
| title / page_date | 文章标题 / 页面日期 | 允许空，记「未取到」 |
| url / entry_url | 详情页 URL / 渠道入口 URL | 不允许空 |
| fetched_at | 抓取时刻（UTC+8） | 不允许空 |
| http_status | 状态码 | 不允许空 |
| sha256 | **md 正文**的 sha256 | 不允许空 |
| chars | md 字符数 | 不允许空（>500 为验收门） |
| js_needed / paywall_marker | 该页是否需 JS / 是否检出付费墙标记 | 不允许空 |

## 4. 可重复运行命令

```bash
cd /srv/share/quantdb/code/quant-lab
# 1) 全量探测（robots 优先；同域名「上次请求完成后再等 ≥1.05s」）
python3 scripts/work537_probe.py probe --force --max-requests 200
# 2) 抓正文样例（抓正文前再查 robots，被禁则不发请求）
python3 scripts/work537_probe.py sample --channels <id1,id2,...> --tries 3
# 3) 判据修正后离线重判（0 请求，用已保存快照重放判定）
python3 scripts/work537_probe.py reclass
# 4) 审计：静态门 / 台账核查 / 独立 curl 复跑
python3 scripts/work537_audit.py gates
python3 scripts/work537_audit.py ledger
python3 scripts/work537_audit.py replay --channels gs,jpm,db,hibor,citi --samples <meta1>,<meta2>
# 5) 配图与单文件 HTML
python3 scripts/work537_figures.py && python3 scripts/work537_build_html.py
```

产物：
- 机器证据：`docs/WORK-537/02-data.csv`（全部 29 行的实测字段）
- 交付清单：`docs/foreign_research_channels.csv`（issue 要求的表头）
- 一页结论：`docs/foreign_research_brief.md` + `docs/foreign_research_brief.html`
- 原始探测日志与列表页 HTML：`/srv/share/quantdb/raw/foreign_research/_probe/`（**不入 public 仓库**）

## 5. 快照标识与校验值

见 `docs/WORK-537/02-data.sha256`（覆盖交付 CSV、机器证据表、探测日志、请求台账与 9 篇正文样例）。

**本轮请求量台账**（issue 上限 200）——**可核对口径**（2026-11-26 补正，依据文件见每行）：

| 段 | 实际发出 | 凭据 |
|---|---|---|
| round 1 全量扫描（robots 判定生效前） | **46** | `_probe/probe_log_run1_robots_bug.json` 的 `tried` 46 条，**全部实际发出**（0 条 robots禁） |
| round 2 全量扫描（robots 优先 + 判据修正） | **29** | `_probe/probe_log.json` 的 `tried` 42 条 − 13 条「robots禁」（未发出）= 29 |
| 审计独立复跑（curl） | **12** | `_probe/audit_ledger.json`（12 条，带时间戳） |
| **实际发出下界** | **≥87** | 46 + 29 + 12。**两轮的 robots.txt 抓取（每轮触及 31 个 host）不计入 `tried`**，故真实请求数**严格大于** 87 |
| 台账可核对条目 | **85** | `_probe/request_ledger.json`：73 条 `reconstructed`（round-2 的 42 条 `tried` + 31 条 robots）+ 12 条审计原始记录（带时间戳）。**台账条目 ≠ 实际请求数**：台账未覆盖 round-1，且含 13 条未发出的 robots禁 条目 |
| 185 | — | **不可由台账复现的上界估计**（早期按脚本内计数器逐段累加），**不再作为主口径** |

> 复算命令（0 请求，只读本地文件）：`python3 docs/WORK-537/verify_corrections.py`

## 6. 已知缺口

- 部分渠道入口 URL 为本轮**自找**（大管家基线里 BofA 的 URL 404），入口可变性是固有问题，已在各行 `备注` 标注。
- 中文聚合站（慧博/发现报告/萝卜投研）多为登录后可见，若实测为登录墙，按「人工看」处理，不做绕过。
- 单次快照不反映长期可用性（限流/反爬策略会变），结论以「2026-11-23 实测」为准。
- **round-1 的 5 次请求发生在 robots 判定生效之前**：`www.barrons.com/` → HTTP 401、
  `www.barrons.com/market-data` → HTTP 200（正文已取到）、`www.finra.org/{finra-data,
  media-center/newsroom, rules-guidance}` → HTTP 403 ×3。其中**取到正文的 1 次未进入任何分析、
  未入样例、未入结论**；round-2 起 robots 优先，未再发出正文请求。
- 请求台账：**第 1 轮无逐条台账**（当时未实现 ledger）；台账文件另因 `_save_ledger()`
  覆盖式落盘丢失过时间戳（已改为追加式，并按 `probe_log.json` 重建）。
  审计项 A-6 / A-8 记录在案。
