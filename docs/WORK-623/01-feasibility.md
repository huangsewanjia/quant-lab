# 01 · 可行性实测（WORK-623，官方 API 口径）

统计时点：2026-11-27 02:10–02:15 CST　主机：Mo-server（Linux）
证据：`/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27-api/`
（`api-doc.html`、`fxbaogao_mcp-1.0.3.tar.gz`、`check_auth_no_token.json`；sha256 见 `manifest.json`）

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
| token 文件 `/srv/share/quantdb/secrets/fxbaogao_api_key` | **不存在**（`secrets/` 目录也未建） |
| `check-auth`（无 token） | 退出码 **3**，写 `check_auth.json`（ok=false），**零 HTTP 请求**（台账无文件） |

## 3. 未取到（不是不存在）

| 问题 | 状态 |
|---|---|
| token 是否有效、高级 VIP 的 API 权限是否已开 | 未取到（token 未写入） |
| 搜索成功响应的完整字段（标题/机构/日期的真实键名） | 未取到（无 token；按官方 MCP 源码 + 变体容错实现） |
| 下载额度字段名与单篇扣减数 | 未取到（需真实下载；本单不拿假 key 试） |
| 分页 1–10 的真实返回条数 | 未取到（等 token；参数校验与本地上限已实现） |

## 4. 对实现的影响

1. 成败判定看 `body.code`（0/200 成功），HTTP 状态只作证据；`code/msg` 原样进 `failed.jsonl`。
2. token 只从 secrets 文件读，日志只写路径与 sha256 前 8 位；`check-auth` 用一次搜索验证（不扣下载额度）。
3. `fetch` 默认 `--limit 5`、同域 ≥1.05s、已存在文件默认跳过（避免重复扣额度）；`--force` 才重下。
4. 每篇写 `site_url = https://www.fxbaogao.com/view?id=<id>` 与 `pdf_url`，`download` 原始回包留 `<id>-<slug>.download.json`（额度字段证据）。
