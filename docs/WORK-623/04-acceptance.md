# 04 · 验收对照（WORK-623，官方 API 口径）

统计时点：2026-11-27 02:20 CST。真库 `/srv/share/quantdb/db/quantdb.duckdb`；彩排库 `/srv/share/quantdb/staging/work623_demo_api`（合成 PDF，只验证管线）。

**契约变更 01 之后，原浏览器/CDP 相关验收作废；本表按其新增三项（分页上限、额度消耗、code/msg 入台账）与原有 7 条一起判定。**

## 1. 原 7 条

| # | 标准 | 状态 | 实测/证据 |
|---|---|---|---|
| 1 | 端到端 ≥5 篇真实报告：PDF 落盘、pypdf 页数 >0、标题与站内一致 | **未达成（阻塞）** | token 未写入（`secrets/fxbaogao_api_key` 不存在）；`check-auth` 退出码 3、零请求。管线用 5 篇合成 PDF 彩排通过；`verify` 用官网详情页标题核对已实现 |
| 2 | `select count(*) from report_fulltext` = 落盘 PDF 篇数 | 机制通过；真实数据 0=0 | 真库 `0 = 0`；彩排 `5 = 5`；C1 逐篇差集 + 同 id 多文件检测 |
| 3 | 同批重复跑行数不变 | 机制通过（彩排两次） | `run1 inserted=5` → `run2 unchanged=5`，两次均 5 |
| 4 | `failed.jsonl` 字段齐 | 通过（单测+闸门） | 字段 `report_id/url/http_status/code/msg/reason`；C4 逐行校验并报行号；单测覆盖坏 meta 6 类 + 业务错误 300011/300012/300021 |
| 5 | 关联视图给实测匹配率（分子/分母/口径） | 真实 0/0；彩排 5/5 | C6 输出 `numerator_linked=5, denominator_fulltext=5, rate=1.0` 与口径原文；真库 `0/0, rate=null` |
| 6 | 交付说明可复现 | 通过 | `03-runbook.md`：token、check-auth、search、fetch、ingest、checks、日志、失败处置、回滚 |
| 7 | 版权口径落进 README/交付说明 | 通过 | `/srv/share/quantdb/README.md` §3.3、runbook §8、交付页页脚；`.gitignore` 排除 PDF 与 `secrets/` |

## 2. 契约变更新增三条

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| N1 | 分页上限 10 页的实测记录 | 参数与拒绝已实现；**真实返回条数未取到** | `search_page` 对 page=0/11 抛 `ValueError`（单测 `test_search_page_rejects_page_out_of_range`）；`--pages` 上限 10；真实每页条数需 token 后写入 `search_raw/pageN.json` |
| N2 | 下载额度消耗实测（连续取 N 篇，记录额度字段或报错原文） | **未取到（无 token）** | `download` 一成功就落 `<id>-<slug>.download.json`（含 code/msg/urls/raw）；PDF 端点返回额度错误时把该回包的 `code/msg` 写进 `failed.jsonl` 并追加 `pdf_error` 到 `.download.json`（单测 300021「下载额度不足」）；真实扣减数需 token 后实测 |
| N3 | 失败时 `code` 与 `msg` 原文逐条入 `failed.jsonl` | 通过（单测） | 300011（token/search）、300012（download 业务错）、300021（PDF 端点额度不足，含 `pdf_error` 落 `.download.json`） |

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
202 passed
$ … pytest db/tests/test_report_fulltext.py db/tests/test_work623_api.py -q
40 passed
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
