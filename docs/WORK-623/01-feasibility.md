# 01 · 可行性实测（WORK-623，官方 API 口径）

统计时点：2026-11-27 02:10–13:00 CST　主机：Mo-server（Linux）
证据：`/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27-api/`（接口文档与 MCP 源码）+
`/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27-api-real/`（真实端到端：check-auth / search / download / verify，sha256 见 `summary.json`）

> 契约变更 01（大管家 2026-11-27 01:40）已把「CDP 复用桌面 Chrome / cookie 文件 / robots 降级人工点」整节作废，改走官方接口。本文只覆盖 API 口径；浏览器方案在本单**从未取到真实数据**，相关代码已删除。

## 1. 接口事实（官方文档 + 官方 MCP 源码）

| 项 | 值 | 来源 |
|---|---|---|
| Base | `https://api.fxbaogao.com`（`47.117.79.47`，本机直连） | 文档页 `agent-interface-document`（2026-09-14 更新） |
| 鉴权 | `Authorization: Bearer <API Key>` | 同上 |
| 搜索 | `POST /mofoun/agent/search`，`keywords` / `orgNames[]` / `startTime` / `endTime` / `pageNum`（**1–10**） | 同上 |
| 段落 | `POST /mofoun/agent/paragraph`，`reportId` / `keyword` | 同上 |
| 取 PDF 地址 | `GET /mofoun/agent/download?reportId=<id>`；拉 PDF 扣下载权益 | 同上 |
| 搜索返回结构 | `data` 为 list，或 `data.dataList` 为 list（列表项含 `reportId`） | 官方 MCP 源码 `src/fxbaogao_mcp/server.py`（PyPI `fxbaogao-mcp` 1.0.3，sha256 `d7215097…`） |
| PDF 地址字段 | 递归找 `pdfUrl/url/fileUrl/downloadUrl`；相对路径拼 `https://dr.fxbaogao.com/` | 同上 |
| 业务错误 | HTTP 200 + `body.code=300011` + `msg="请传入正确Authorization"` | 大管家 2026-11-27 01:37 实测（写入派单评论） |
| MCP 端点 | `https://api.fxbaogao.com/mcp/`（无 token → 405，仅 POST） | 同上 |

文档页已在证据目录留档（`api-doc.html`，sha256 `2011c52d…`）。文档页只有能力说明，**没有逐字段响应样例**；响应字段名以官方 MCP 源码 + 容错变体实现，真实报文样例要等 token 到位才能落盘。

## 2. 本机实测

| 项 | 结果 |
|---|---|
| `getent hosts api.fxbaogao.com` | `47.117.79.47` |
| `GET https://api.fxbaogao.com/mcp/` | HTTP 405（端点在，只收 POST） |
| PyPI `fxbaogao-mcp` | 200，sdist 1.0.3 已下载并阅读源码 |
| token 文件 `/srv/share/quantdb/secrets/fxbaogao_api_key` | **已到位**：35 B、权限 600、sha256 前 8 位 `19c8f87e` |
| `check-auth`（真实 token） | 退出码 0；HTTP 200、`code=0`、`msg=ok`、54,996 B |
| 搜索（真实） | `keywords=储能, pageNum=1` → 20 条/页；字段 `reportId/title/orgName/industryName/pageNum/pubTime/pubTimeStr/whitelistRequired/paragraphs` |
| 下载（真实，1 篇） | reportId 5913596 → HTTP 200、`code=0`、PDF 地址（签名串）→ 56 页、45,441,360 B、sha256 前 8 位 `a6b3612d`；`pageNum` 字段=56 与 pypdf 一致 |

## 3. 仍未取到 / 已实测

| 问题 | 状态 |
|---|---|
| token 有效、API 权限 | **已实测**：`code=0`，20 条/页 |
| 搜索响应字段 | **已实测**：`reportId, title, orgName, industryName, pageNum, pubTime, pubTimeStr, whitelistRequired, paragraphs`；`title` 含 `<em>` 高亮标签（采集器已清洗） |
| 下载响应 | **已实测**：`data` 是**字符串 PDF 地址**（带 `auth_key` 签名），不是对象；`urls[0].field="data"` |
| 下载额度字段 | 回包里**没有额度字段**（只有 `code/msg/data`）；扣减在服务端，无法从本接口读到剩余额度——实测只下 1 篇 |
| 分页 2–10 的每页条数 | 只实测 page1=20 条；其余页未取（避免无意义请求） |
| 失败样本（真实） | 未制造（不主动打假 key/越权），失败路径由单测覆盖 300011/300012/300021/403 |

## 4. 对实现的影响

1. 成败判定看 `body.code`（0/200 成功），HTTP 状态只作证据；`code/msg` 原样进 `failed.jsonl`。
2. token 只从 secrets 文件读，日志只写路径与 sha256 前 8 位；`check-auth` 用一次搜索验证（不扣下载额度）。
3. `fetch` 默认 `--limit 5`、同域 ≥1.05s、已存在文件默认跳过（避免重复扣额度）；`--force` 才重下。
4. 每篇写 `site_url = https://www.fxbaogao.com/view?id=<id>` 与 `pdf_url`，`download` 原始回包留 `<id>-<slug>.download.json`（额度字段证据）。
