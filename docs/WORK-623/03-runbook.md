# 03 · 跑法（WORK-623，官方 API）

没参与过的人照下面顺序做即可复现。所有路径为绝对路径。

## 0. 前置：token

用户（WORK-624）写入单行 API Key：

```
/srv/share/quantdb/secrets/fxbaogao_api_key     # 目录 700、文件 600，单行
```

写入后先验证（一次搜索，**不扣下载额度**）：

```bash
cd /srv/share/quantdb/code/quant-lab
PY=/home/moyongkai/.venvs/quantdb/bin/python
$PY scripts/fxbaogao_fulltext.py check-auth
```

- 退出码 0：`check_auth.json` 里 `ok=true`、`code=200`、`token_sha256_8=…`。
- 退出码 4：token 无效（`code`/`msg` 原文在 `check_auth.json`，如 `300011 请传入正确Authorization`）。
- 退出码 3：token 文件不存在/为空/含空白，或权限不合规（文件必须 600、所在目录 700）——此时**一个请求都不会发**。

## 1. 搜索候选

```bash
$PY scripts/fxbaogao_fulltext.py search \
    --keywords "储能 市场化" --time last3mon --pages 3 \
    --out /srv/share/quantdb/raw/reports_fulltext/fxbaogao/candidates-$(date +%F).jsonl
```

- `--pages` 1–10（官方上限；超过直接报错，需改检索条件而不是翻页）。
- 产物：候选 JSONL（`report_id/title/org/page_date/url/site_url/raw_item`）、`search_raw/pageN.json`（原始回包）、`search_meta.json`。
- 某页失败：写 `failed.jsonl`（`code`/`msg` 原文）并停止后续页，退出码 1。

## 2. 取 PDF（会扣下载权益）

```bash
$PY scripts/fxbaogao_fulltext.py fetch --input <candidates.jsonl> --limit 5
```

- 默认单轮 5 篇、同域 ≥1.05s；已存在同 id PDF 默认跳过（不重复扣额度），`--force` 才重下。
- 产物：`<page_date>/<report_id>-<slug>.pdf` + `.meta.json` + `.download.json`（download 原始回包：`code/msg/data`；实测无额度字段）。
- 单篇失败写 `failed.jsonl`：
  `{"report_id":…, "url":"https://www.fxbaogao.com/view?id=…", "http_status":200, "code":300011, "msg":"请传入正确Authorization", "reason":"取 PDF 地址失败: …", "path":"", "ts":"…"}`

## 3. 入库与对账（Mo-server）

```bash
$PY -m db.etl.report_fulltext ingest
$PY -m db.checks.report_fulltext all
```

`ingest` 校验 meta 字段、sha256、字节数、pypdf 页数、report_id 与文件名一致；任一不过写 `failed.jsonl`，不入库。

`checks` 输出 C1–C7；C6 给关联率分子/分母/口径/查询（信息项，不判 FAIL）。

## 4. 本地复核（验收 1）

```bash
$PY scripts/fxbaogao_fulltext.py verify --meta "/srv/share/quantdb/raw/reports_fulltext/fxbaogao/2026-11-26/5913596-xxx.meta.json"
```

复核 pypdf 页数、sha256、字节数与 meta 是否一致；不一致退出码 1。**不访问网络、不消耗额度。**
标题一致性由 search 回包的 `title`（已去 `<em>`）与落盘 meta 对照；需要回原文时用 `site_url` 打开官网。

## 5. 日志与证据

| 内容 | 路径 |
|---|---|
| 请求台账 | `/srv/share/quantdb/raw/reports_fulltext/_probe/request_ledger.json` |
| token 验证结果 | `<root>/check_auth.json` |
| 搜索原始回包 | `<候选目录>/search_raw/page*.json` |
| 失败台账 | `<root>/failed.jsonl` |
| download 原始回包（`code/msg/data`，实测无额度字段） | `<date>/<id>-<slug>.download.json` |
| 对账报告 | `/srv/share/quantdb/meta/checks/report_fulltext.json` |
| API 文档/官方 MCP 源码留档 | `/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27-api/` |

## 6. 常见失败

| 现象 | 先查 | 处置 |
|---|---|---|
| 退出码 3 | token 文件 | 不存在/为空/含空白/权限不是 600（目录 700）；让用户按 WORK-624 写入；不要用假 key 试接口 |
| 退出码 4 / `code=300011` | `check_auth.json` 的 `msg` | 换 key 或确认高级 VIP 的 API 权限；不要把失败当成功 |
| `没有 PDF 地址（可能是额度不足…）` | `.download.json` 原始回包 | 实测正常回包也没有额度字段（只有 `code/msg/data`）；对照 `code/msg` 与站内会员中心，不要猜数值 |
| PDF 拉取失败 | `failed.jsonl` 的 `http_status`/`path` | 地址可能带时效；重跑 `fetch`（已存在会跳过） |
| 入库失败 `sha256 不符` | 文件与 meta | 半截下载会先被 `%PDF`/页数拦住；修好重下 |

## 7. 回滚

```sql
DROP VIEW  IF EXISTS v_report_fulltext_unlinked;
DROP VIEW  IF EXISTS v_report_fulltext_linked;
DROP VIEW  IF EXISTS v_report_fulltext_link_candidates;
DROP VIEW  IF EXISTS v_report_fulltext;
DROP TABLE IF EXISTS report_fulltext_changes;
DROP TABLE IF EXISTS report_fulltext;
DROP MACRO IF EXISTS fx_norm_title;
DROP MACRO IF EXISTS fx_norm_org;
DROP MACRO IF EXISTS fx_strip_text;
DROP MACRO IF EXISTS fx_strip_site;
```

目录：`rm -rf /srv/share/quantdb/raw/reports_fulltext/fxbaogao`（PDF/meta 不在 git 内）。
代码：分支 `agent/work-623-fxbaogao` 未合并主干，`git checkout <基线>` 即回滚。

## 8. 版权与凭据

- 正文仅限内部检索与研究使用：不得对外分发、不得作为引用来源转载、不得整段复制进对外材料。
- API Key 只放 `/srv/share/quantdb/secrets/fxbaogao_api_key`（600/700）；日志、issue、交付说明只写路径与 sha256 前 8 位；禁止写进代码、shell 历史、`.env` 或被 git 跟踪的文件。
- 仓库是 public：`*.pdf` 与 `secrets/` 已被 `.gitignore` 排除。
