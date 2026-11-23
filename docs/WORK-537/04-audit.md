# WORK-537 · 阶段 4 模型风控审计（独立复跑）

输入=docs/WORK-537/02-data.csv、docs/foreign_research_channels.csv、scripts/work537_probe.py、raw/foreign_research/（台账 + 9 篇正文样例）
本阶段产出什么=静态质量门实测、curl 独立复跑结果、频控/请求量/合规核查、问题清单（blocking/suggestion/nit）与关闭状态
本阶段工具调用上限=审计独立请求 12 次（5 渠道 × (robots+正文) + 2 篇样例正文）+ 频控回归 2 次 = 14 次

> 审计纪律：本阶段**不复用**探测脚本的网络实现与结论。复跑一律用 **curl 子进程**（独立客户端），
> 标题重新解析后与 CSV 比对；robots 判定在审计侧**重新取**并**重新判定**。

## 1. 审计对象

| 项 | 值 |
|---|---|
| 被审交付物 | `docs/foreign_research_channels.csv`（29 行）、`docs/WORK-537/02-data.csv`（29 行 × 25 列） |
| 被审代码 | `scripts/work537_probe.py`、`scripts/work537_audit.py` |
| 数据快照 | `/srv/share/quantdb/raw/foreign_research/`（`_probe/probe_log.json`、`request_ledger.json`、9 篇正文样例 + meta.json） |
| 测量日期 | 2026-11-23（UTC+8） |
| 复跑命令 | `python3 scripts/work537_audit.py gates` / `ledger` / `replay --channels gs,jpm,db,hibor,citi --samples <ms>,<hsbc>` |

## 2. 静态质量门（实测值 vs 阈值）

| 门 | 实测 | 阈值 | 结论 |
|---|---|---|---|
| G1 CSV 数据行 | 29 行 | ≥25 行 | PASS |
| G2 `实测证据` 列空值 | 0 个 | 0 个 | PASS |
| G3 必含渠道齐全 | 缺 0 个（29/29 全在） | 0 缺失 | PASS |
| G4 `建议` 列取值 | 全部 ∈ {采, 观察, 不采} | 同 | PASS |
| G5 两份 CSV 行数一致 | 29 vs 29 | 相等 | PASS |
| G6 样例 sha256 与文件一致 | 9/9 一致 | 全部一致 | PASS |
| G7 样例字符数 >500 | 最小 2,742（HSBC），0 个 ≤500 | >500 | PASS |
| G8 脚本无账号/凭据/绕过痕迹 | 命中 0（无 Cookie/Authorization/password/token 等） | 0 | PASS |
| G9 未把 PDF 作为入口 | 0 个 | 0 | PASS |

**G8 是「不绕过访问控制」的机器证据**：探测脚本全程只用普通浏览器 UA，
不发送 Cookie、不带 Authorization、不带任何账号凭据、不注册、不订阅。

## 3. 独立复跑（curl，与探测脚本 urllib 独立实现）

| 目标 | 记录状态 / 重跑状态 | 记录标题 | 重跑标题 | 标题一致 | robots（审计侧重判） |
|---|---|---|---|---|---|
| goldmansachs.com/insights | 200 / 200 | Insights \| Goldman Sachs | 同 | ✅ | 无匹配规则(允许) |
| jpmorgan.com/insights | 200 / 200 | Business Insights, Trends and Perspectives from J.P. Morgan | 同 | ✅ | 无匹配规则(允许) |
| dbresearch.com/…/RI_DBI.alias | 200 / 200 | dbInsights - Research Institute - Deutsche Bank Research Institute | 同 | ✅ | Allow:/ |
| hibor.com.cn | 200 / 200 | 慧博投研资讯-专业的投资研究报告大数据平台… | 同 | ✅ | Allow:/ |
| citigroup.com/global/insights | 200 / 200 | Citi Institute \| Insights \| Citi | 同 | ✅ | Allow:/ |

**验收抽查（issue 要求「随机抽 2 篇用浏览器/curl 打开 URL 能看到同样标题」）**：

| 样例 | 复取状态 | 落盘标题 | 复取标题 | 一致 | 复取可见正文字符 |
|---|---|---|---|---|---|
| Morgan Stanley Ideas | HTTP 200 | Healthcare Leaders Discuss the Forces Reshaping the Industry \| Morgan Stanley | 同 | ✅ | 25,719 |
| HSBC GBM Insights | HTTP 200 | Financing the next wave of electrification | 同 | ✅ | 2,462 |

（另在采集阶段用 curl 独立核过 dbInsights 与 发现报告 两篇，标题逐字一致。）

**结论：可重现**。5 个渠道 + 4 篇样例的状态码与标题 100% 复现。

## 4. 频控、请求量与合规核查

### 4.1 请求总量（issue 上限 200）

| 段 | 请求数 | 凭据 |
|---|---|---|
| 第 1 轮全量扫描（修正 robots 逻辑前） | 75 | 脚本内计数器输出 `[done] 渠道 29 个，请求 75 次` |
| 第 2 轮全量扫描（robots 优先 + 判据修正） | 63 | 台账计数 |
| 5 渠道标签重探（reuters/bloomberg/ft/wsj/finra） | 5 | 台账计数 |
| 正文样例阶段（13 渠道 × (robots+正文)） | 26 | 台账计数 |
| dbInsights 正文重取（robots+正文） | 2 | 台账计数 |
| 频控回归测试（同域 2 次） | 2 | 台账记录 |
| **审计独立复跑** | **12** | `audit_ledger.json` |
| **合计** | **185** | **≤200，余量 15** |

**未发生**：全站爬、PDF 全文抓取、账号注册、付费订阅。

### 4.2 同域频控

- 审计侧 12 次请求（有时间戳）：**同域最小间隔 1.05s，无 <1.0s 的域名**。
- 探测期发现 1 次偏差并已修复（见 A-5）：`www.ubs.com` 上「robots.txt 完成 → 下一请求开始」间隔 0.25s。
  原因：初版节流按请求**开始**时刻记间隔，请求本身耗时 1.33s 时不满足更严的完成间隔口径。
- 修复后回归实测：**完成 → 开始间隔 1.05s ≥ 1.00s 阈值，PASS**
  （`docs/WORK-537/test_rate_limit.py`，实测输出 1.05s）。

### 4.3 robots 合规

- 探测脚本改为 **robots 优先**：先取该 host 的 robots.txt，被 Disallow 的路径**根本不发正文请求**。
- 台账交叉核对：**「robots 禁止/不可读」的渠道没有发出过正文请求**（Barron's、FINRA 的台账里只有 robots.txt 请求）。
- 审计侧重判 5 个渠道的 robots 规则，与探测侧结论一致（4 个允许、1 个无匹配规则即允许）。
- **未绕过任何 robots、付费墙、登录、验证码、Cloudflare 挑战**：403 与登录墙均如实记为失败原文。

## 5. 问题清单

| # | 级别 | 问题 | 实测值 vs 阈值 | 位置 | 改法 | 状态 |
|---|---|---|---|---|---|---|
| A-1 | blocking | robots.txt 取的是**入口 host**的规则，却用**落地 host**的路径去判定 → BofA 被误判「robots禁」 | 误判 1 个渠道（BofA）；落地 host 与入口 host 不同的渠道共 1 个 | `work537_probe.py: get_robots/probe_channel` | 改为按落地 URL 的 host 取 robots | **已关闭**：BofA 改判「直连」，独立复跑 200 一致 |
| A-2 | blocking | robots 分组解析缺陷：把 `User-agent: googlebot` 组当成 `*` 组 → CNBC 被误判「robots禁」 | 误判 1 个渠道（CNBC）；实测 CNBC `*` 组允许 `/investing/` | `work537_probe.py: parse_robots/select_group` | 按 RFC 9309 重写分组解析（最具体 UA 组优先） | **已关闭**：CNBC 改判「直连」，复跑 200 一致 |
| A-3 | blocking | 「需 JS」判据用「可见文本 <800 字符」→ 导航菜单很长的页面被误判为直连 | 误判 3 个渠道（CNBC、NYSE、东方财富研报中心），实测其列表页 0 个可解析日期 | `work537_probe.py: analyse_page/classify` | 改用 issue 口径：**列表页必须能解析出标题+日期** | **已关闭**：3 个渠道改判「需JS(列表未渲染)」 |
| A-4 | blocking | 年份缺失的日期（「Nov 22」「9 November」）不被识别 → CNBC 被误判 | 误判 1 个渠道；7 个渠道（bofa/cnbc/db/gs/hsbc/jpm/ms）使用该格式 | `work537_probe.py: all_dates` | 增加无年份日期模式，按页内最新日期推断年份，跨年回退 1 年 | **已关闭**：CNBC 恢复「直连」，日期显示 2026-11-22 |
| A-5 | blocking | 同域节流按请求**开始**时刻记间隔 → 出现 0.25s 的「完成→开始」间隔 | 实测 0.25s vs 阈值 1.00s（www.ubs.com） | `work537_probe.py: _throttle` | 改为按请求**完成**时刻记间隔 | **已关闭**：回归实测 1.05s ≥ 1.00s |
| A-6 | suggestion | `_save_ledger()` 覆盖式落盘，跨进程把 98 条台账冲掉 | 丢失 98 条（0.25s 级时间戳证据） | `work537_probe.py: _save_ledger` | 改为追加式合并；并按 `probe_log.json` 重建台账 | **已关闭**：台账重建为 73 条（reconstructed）+ 12 条审计原始记录 |
| A-7 | suggestion | UBS 的 fallback 列表含与 entry 相同的 URL → 一次重复 403 请求 | 重复请求 1 次（已计入 185 次总量） | `work537_probe.py: CHANNELS[ubs]` | fallback 改为不同路径 `/global/en/insights/` | **已关闭**（该次请求已发生，如实计入总量） |
| A-8 | suggestion | 第 1 轮 75 次扫描无逐条台账（当时未实现 ledger） | 75 条只有计数器输出 | 流程 | 后续单把 ledger 作为强制前置 | **未关闭（流程改进项，不影响本单结论）** |
| A-9 | nit | 样例「正文」为去标签后的可见文本，丢失段落结构 | 影响 NLP 用途，不影响本单字符数与关键词扫描结论 | `work537_probe.py: clean_text` | 后续如需结构化正文，保留 DOM | **未关闭（已记录）** |

**未关闭的 blocking：0 条。** A-8 / A-9 为流程与工程改进项，不影响本单结论成立。

## 6. 审计结论

- **可复现**：5 个渠道 + 4 篇样例用**独立客户端（curl）**复跑，状态码与标题 100% 复现。
- **可审计**：9 篇样例 sha256 全部自洽；请求总量 185 ≤ 200；同域间隔实测 ≥1.05s；robots 禁止路径零正文请求。
- **无绕过痕迹**：脚本无凭据/登录/付费墙绕过代码；403、登录墙、robots 禁止均按失败原文记录。
- **能否进入研究备忘录**：**可以**。但必须随备忘录一并披露：A-8（首轮 75 次无逐条台账）、
  阶段 3 §8（Reuters/Bloomberg/FT/WSJ 的站点侧行为未测、HSBC Global Research 条目未逐篇扫描）。
- **本审计未做的事**：未修改任何被审代码逻辑之外的数据、未替阶段 3 补结论、未做投资建议。

**审计对象**：WORK-537 交付物（29 渠道可抓性实测 + 9 篇正文样例）
**实际运行的质量门**：G1–G9 全 PASS；独立复跑 9/9 一致；频控与合规核查 PASS
**未确认项**：A-8、A-9；以及阶段 3 §8 列出的 7 项未取到/未验证。
