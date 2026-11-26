# 04 · 验收对照（WORK-623）

统计时点：2026-11-27 01:41 CST。真库 `/srv/share/quantdb/db/quantdb.duckdb`；彩排库 `/srv/share/quantdb/staging/work623_demo/db/quantdb.duckdb`（合成 PDF，仅验证管线，**不是**真实抓取）。

## 1. 七条验收标准

| # | 标准 | 状态 | 实测/证据 |
|---|---|---|---|
| 1 | 端到端 ≥5 篇真实报告：PDF 落盘、pypdf 页数 >0、标题与站内一致 | **未达成（阻塞）** | 登录态不存在（`01-feasibility.md` §3：CDP 关闭、`secrets/` 不存在、WORK-624 `todo`），无真实 PDF。管线本身已用 5 篇合成 PDF 彩排通过（下表） |
| 2 | `select count(*) from report_fulltext` = 落盘 PDF 篇数 | 机制通过；真实数据 0=0 | 真库：`0 = 0`；彩排：`5 = 5`；闸门 C1 逐篇核差集，不只看总数 |
| 3 | 同批重复跑行数不变 | 机制通过（彩排两次） | `run1 inserted=5` → `run2 unchanged=5`，两次数均 5；真库两次均 0 |
| 4 | `failed.jsonl` 字段齐（report_id/URL/状态码/原因） | 通过（单测+闸门） | 单测构造 2 条失败：缺 meta、sha 不符；C4 对行做字段校验并报缺字段行号 |
| 5 | 关联视图给实测匹配率（分子/分母/口径） | 真实 0/0；彩排 5/5 | C6 输出 `numerator_linked=5, denominator_fulltext=5, rate=1.0` 与口径原文；真实库 `0/0, rate=null` |
| 6 | 交付说明可复现 | 通过 | `03-runbook.md`：命令、依赖、日志路径、失败处置、回滚 |
| 7 | 版权口径落进 README/交付说明 | 通过 | `docs/WORK-623/03-runbook.md` §8、`/srv/share/quantdb/README.md` §3.4、`DELIVERY.html` 页脚 |

**结论：1–5 的真实数据部分全部因「登录态未就绪」阻塞，不写成已达成。**

## 2. 彩排（合成 PDF，证明入库/视图/闸门可跑）

```
$ QUANTDB_HOME=/srv/share/quantdb/staging/work623_demo python - <<'PY'
from db.etl import report_fulltext as rf; print(rf.ingest())
PY
{"root": "…/staging/work623_demo/raw/reports_fulltext/fxbaogao", "scanned": 5, "inserted": 5,
 "updated": 0, "unchanged": 0, "failed": 0, "changes": 0}

$ 再跑一次
{"scanned": 5, "inserted": 0, "updated": 0, "unchanged": 5, "failed": 0, "changes": 0}
```

```
$ python -m db.checks.report_fulltext all
PASS C1_rows_match_files   {"rows": 5, "pdf_files": 5}
PASS C2_pdf_pages_positive {"rows": 5, "bad_report_ids": []}
PASS C3_sha_matches_disk   {"mismatch": [], "missing": []}
PASS C4_failed_log_schema  {"lines": 0, "bad_lines": []}
PASS C5_meta_completeness  {"pdf_files": 5, "problems": []}
INFO C6_link_rate          {"numerator_linked": 5, "denominator_fulltext": 5, "rate": 1.0}
PASS C7_change_log_integrity {"change_rows": 0, "problems": []}
```

## 3. 真库现状（表已建、空表）

```
$ python - <<'PY'
import duckdb; con=duckdb.connect('/srv/share/quantdb/db/quantdb.duckdb', read_only=True)
print(con.execute("select count(*) from report_fulltext").fetchone()[0],
      con.execute("select count(*) from v_report_fulltext_linked").fetchone()[0],
      con.execute("select count(*) from report_meta").fetchone()[0])
PY
0 0 68874
```

## 4. 回归（单测）

```
$ /home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests/test_report_fulltext.py db/tests/test_work623_collector.py -q
18 passed
```

## 5. 未验证项（明确列出，不冒充已验证）

| 未验证 | 原因 | 谁/何时验证 |
|---|---|---|
| Playwright + CDP 真实下载链路 | 无登录态、本机无 Playwright、桌面机不可达 | 登录后第一条命令 `fetch --dry-run` |
| 真实下载控件选择器 | 匿名页不渲染 | 同上，快照 + `--selector` |
| 真实下载额度/风控阈值 | 需登录 | 首次 `fetch --limit 5` 观察 |
| `adopt` 用真实 PDF 首页文字匹配 | 合成 PDF 无文字层 | 首批手工下载后跑 `adopt --dry-run` |
| 真实报告与 report_meta 的关联率 | 无真实正文 | 首批入库后 C6 出数 |
