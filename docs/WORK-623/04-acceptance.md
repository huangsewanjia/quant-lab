# 04 · 验收对照（WORK-623，官方 API 口径）

统计时点：2026-11-27 13:00 CST。真库 `/srv/share/quantdb/db/quantdb.duckdb`；彩排库 `/srv/share/quantdb/staging/work623_demo_api`（合成 PDF，只验证管线）。

**契约变更 01 之后，原浏览器/CDP 相关验收作废；本表按其新增三项（分页上限、额度消耗、code/msg 入台账）与原有 7 条一起判定。**

## 1. 原 7 条

| # | 标准 | 状态 | 实测/证据 |
|---|---|---|---|
| 1 | 端到端 ≥5 篇真实报告：PDF 落盘、pypdf 页数 >0、标题与站内一致 | **部分（本轮 1 篇真实）** | token 已到位（sha256 前 8 位 `19c8f87e`）。实测 1 篇：reportId 5913596，56 页、45,441,360 B、sha256 前 8 位 `a6b3612d`、http 200/code 0；`verify` 页数/字节/sha 全一致。≥5 篇本轮未做（派单只要求 1 篇端到端） |
| 2 | `select count(*) from report_fulltext` = 落盘 PDF 篇数 | **本轮不适用（不写库）** | 派单明确「不要写 `report_fulltext`，那是 WORK-628」；本轮只落 1 个 PDF 文件，未执行 ingest。库层与 C1 闸门仍在分支（上轮产物），真库此前为 0 行 |
| 3 | 同批重复跑行数不变 | **本轮不适用（不写库）** | 同上；采集侧幂等靠「同 id 已存在默认跳过」（`fetch` 单测覆盖，第二次零请求） |
| 4 | `failed.jsonl` 字段齐 | 通过（单测+闸门） | 字段 `report_id/url/http_status/code/msg/reason`；C4 逐行校验并报行号；单测覆盖坏 meta 6 类 + 业务错误 300011/300012/300021 |
| 5 | 关联视图给实测匹配率（分子/分母/口径） | **本轮不适用（不写库）** | C6 实现在分支；本轮未 ingest，未出数 |
| 6 | 交付说明可复现 | 通过 | `03-runbook.md`：token、check-auth、search、fetch、ingest、checks、日志、失败处置、回滚 |
| 7 | 版权口径落进 README/交付说明 | 通过 | `/srv/share/quantdb/README.md` §3.3、runbook §8、交付页页脚；`.gitignore` 排除 PDF 与 `secrets/` |

## 2. 契约变更新增三条

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| N1 | 分页上限 10 页的实测记录 | **实测 page1 = 20 条**；`--pages` 0/11 拒绝 | 真实 `POST /agent/search pageNum=1` → 20 条，原始回包 `_evidence/2026-11-27-api-real/search-page1-raw.json`；page 2–10 未取（不做无意义请求）；`--pages` 越界退出 2 |
| N2 | 下载额度消耗实测（连续取 N 篇，记录额度字段或报错原文） | **已实测 1 篇；回包无额度字段** | 1 篇真实下载：HTTP 200、`code=0`、`msg=ok`、`data` 为签名 PDF 地址；回包只有 `code/msg/data`，**没有额度字段**，扣减在服务端。`download` 原始回包落 `.download.json`；失败路径（300021/403）单测覆盖 |
| N3 | 失败时 `code` 与 `msg` 原文逐条入 `failed.jsonl` | 通过（单测；本轮无真实失败） | 300011（token/search）、300012（download 业务错）、300021（PDF 端点额度不足，含 `pdf_error` 落 `.download.json`） |

## 2.5 本轮真实端到端（1 篇）

```
check-auth : http=200 code=0 msg=ok bytes=54996  (token sha256_8=19c8f87e)
search     : POST /mofoun/agent/search {"keywords":"储能","pageNum":1} -> 20 条/页
             首条 reportId=5913596，title 含 <em>，清洗后为「构建新型电力系统的解决方案——电网、储能与水电的作用」
download   : GET /mofoun/agent/download?reportId=5913596 -> http=200 code=0 msg=ok
             data 为字符串签名地址；无额度字段
PDF        : 56 页（pypdf） / 45,441,360 B / sha256 前 8 位 a6b3612d / http 200
verify     : 页数 56/56、字节 45,441,360/45,441,360、sha256 一致
```

证据：`/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27-api-real/`（含 `summary.json`、search 原始回包、download 原包、meta、verify 输出）。
本轮**未执行 ingest、未写 `report_fulltext`**（按派单归 WORK-628）。

## 3. 彩排（合成 PDF，证明入库/视图/闸门在新 schema 下可跑）

```
$ QUANTDB_HOME=/srv/share/quantdb/staging/work623_demo_api python -m db.etl.report_fulltext ingest
{"scanned": 5, "inserted": 5, "updated": 0, "unchanged": 0, "failed": 0, "changes": 0}
$ 再跑一次
{"scanned": 5, "inserted": 0, "updated": 0, "unchanged": 5, "failed": 0, "changes": 0}

$ … -m db.checks.report_fulltext all
PASS C1 rows=5 pdf_files=5 duplicate=[]
PASS C2 … C5；INFO C6 numerator=5 denominator=5 rate=1.0；PASS C7
```

## 4. 真库现状（表已建，含新列，空表）

```
report_fulltext 列：report_id … site_url, pdf_url（旧表已 ALTER 补列，幂等）
rows = 0；v_report_fulltext_linked = 0；report_meta = 68874
checks C1–C7 全 PASS（空表），C6 INFO 0/0
```

## 5. 回归（单测）

```
$ /home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests -q
208 passed
$ … pytest db/tests/test_report_fulltext.py db/tests/test_work623_api.py -q
46 passed
```

## 6. 未验证项

| 未验证 | 原因 | 谁/何时验证 |
|---|---|---|
| token 有效性、VIP 的 API 权限 | token 未写入 | WORK-624 完成后 `check-auth` |
| 搜索成功响应的真实字段名 | 无 token | 首次 `search` 后对照 `search_raw/page1.json`，必要时扩字段变体 |
| 分页 1–10 的真实条数 | 无 token | 同上 |
| 下载额度字段与单篇扣减 | 无 token；不拿假 key 试 | 首次 `fetch --limit 5`，看 `.download.json` |
| 真实标题一致性 | 无真实 PDF | 首次 `fetch` 后 `verify` |
| 真实关联率 | 无真实正文 | 首批入库后 C6 |

## 7. 独立审查

浏览器版曾做两轮独立审查（`956cfef` → `70ac26e` → `9d379f4`）。契约变更 01 后代码整体重写（删除 CDP/sheet/adopt），**需按新 SHA 重新做独立审查**；本轮结果见交付评论与本单工作目录的审查记录（仓库内不放审查原文）。
