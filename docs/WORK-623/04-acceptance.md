# 04 · 验收对照（WORK-623）

统计时点：2026-11-27 01:41 CST。真库 `/srv/share/quantdb/db/quantdb.duckdb`；彩排库 `/srv/share/quantdb/staging/work623_demo/db/quantdb.duckdb`（合成 PDF，仅验证管线，**不是**真实抓取）。

## 1. 七条验收标准

| # | 标准 | 状态 | 实测/证据 |
|---|---|---|---|
| 1 | 端到端 ≥5 篇真实报告：PDF 落盘、pypdf 页数 >0、标题与站内一致 | **未达成（阻塞）** | 登录态不存在（`01-feasibility.md` §3：CDP 关闭、`secrets/` 不存在、WORK-624 `todo`），无真实 PDF。管线本身已用 5 篇合成 PDF 彩排通过（下表） |
| 2 | `select count(*) from report_fulltext` = 落盘 PDF 篇数 | 机制通过；真实数据 0=0 | 真库：`0 = 0`；彩排：`5 = 5`；闸门 C1 逐篇核差集，不只看总数 |
| 3 | 同批重复跑行数不变 | 机制通过（彩排两次） | `run1 inserted=5` → `run2 unchanged=5`，两次数均 5；真库两次均 0 |
| 4 | `failed.jsonl` 字段齐（report_id/URL/状态码/原因） | 通过（单测+闸门） | 单测构造：缺 meta、sha 不符、0 页、report_id 与文件名不符、bytes 非整数、meta 顶层非对象；C4 对行做字段校验并报缺字段行号 |
| 5 | 关联视图给实测匹配率（分子/分母/口径） | 真实 0/0；彩排 5/5 + adopt 后 5/6 | C6 输出 `numerator_linked / denominator_fulltext / rate / criteria / query`；真实库 `0/0, rate=null` |
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
PASS C1_rows_match_files   {"rows": 5, "pdf_files": 5, "duplicate_report_ids": []}
PASS C2_pdf_pages_positive {"rows": 5, "bad_report_ids": []}
PASS C3_sha_matches_disk   {"mismatch": [], "missing": []}
PASS C4_failed_log_schema  {"lines": 0, "bad_lines": []}
PASS C5_meta_completeness  {"pdf_files": 5, "problems": []}
INFO C6_link_rate          {"numerator_linked": 5, "denominator_fulltext": 5, "rate": 1.0}
PASS C7_change_log_integrity {"change_rows": 0, "problems": []}
```

手工收编也实测了一次（不是只有单测）：用 headless Chrome 把一段带标题文字的 HTML 打成**有文字层**的 PDF，放进 `_inbox/`，
`adopt --queue queue.jsonl` 以 `score=1.0` 配到 `report_id=5884540`，落盘为
`2026-11-16/5884540-开源证券-宁德时代-300750-Q3业绩超预期.pdf`；随后 `ingest` `scanned=6, inserted=1, unchanged=5`，
C1 `6=6`、C5 PASS、C6 `5/6 = 0.8333`（该篇为站点独家媒体报告，`report_meta` 无对应条目 → `v_report_fulltext_unlinked` 1 条，符合口径）。

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
$ /home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests -q
196 passed
$ … pytest db/tests/test_report_fulltext.py db/tests/test_work623_collector.py -q
34 passed
```

## 4.1 独立审查整改（round 1 → round 2）

| 审查项 | 处置 |
|---|---|
| B1 台账目录写死 PROBE_DIR，新落盘根首个请求 FileNotFoundError | `work537_probe._save_ledger` 按 `LEDGER_PATH` 父目录建目录；补单测 |
| B2 默认选择器含 `a[href^="/pdf"]`，点击会请求 robots 禁止路径 | 删除该默认选择器；点击前读 `href` 过 robots，违规即写 `failed.jsonl`（reason 含「下载控件指向 robots 禁止路径」）并跳过；runbook 同步 |
| B3 坏 meta（数值非整数/顶层非对象）让整批 traceback | `validate_artifact` 内部容错（`_int_field` + 顶层类型校验），`ingest` 与 C5 共用该函数，坏值只写失败台账；补 3 条单测 |
| S1 同 id 多文件被 `_disk_pdfs` 覆盖，C1 可伪通过 | `_disk_index` 保留多文件，C1 报 `duplicate_report_ids`；补单测 |
| S2 重抓换名残留旧 PDF | 写入成功后清理同 id 旧文件/旧 meta；补单测；采集器与 `adopt` 的 slug 算法统一并加比对测试 |
| S3 Windows 上 `/srv/share/...` 被解析到 `C:\srv` | 支持 `FXBAOGAO_ROOT`，runbook 给出 Windows 命令 |
| S4 `adopt` 坏 PDF 让整次退出 | round-2 复审指正后重做：`match_pdf` 本身也纳入单文件 try/except，坏文件写 `unmatched.jsonl` 后继续；补单测 |
| S5 `--dry-run` 把 robots 不可读当允许 | 统一 `check_robots()`：不可读=禁止；dry_run 报告带 `robots_status` |
| N1–N4 | 0 页用例补齐；`failed.jsonl` 时间带时区；`CdpDriver.cdp_url` 记录进证据；sheet 文件名与 adopt 一致 |

round-2 复审（`70ac26e`）追加：S4 实际未修（`match_pdf` 在 try 外）→ 已重做并补单测；清理旧文件的 glob 收到日期目录（不再误删 `_inbox/` 手工下载）；`--dry-run` 对 robots 禁止/不可读的 URL 不再导航；robots 拦截的 `failed.jsonl` 统一记 `http_status=0`（目标 URL 未发请求）；补 `CdpDriver.download` 的 `path_guard` 直测（stub page，不依赖 Playwright）。

## 5. 未验证项（明确列出，不冒充已验证）

| 未验证 | 原因 | 谁/何时验证 |
|---|---|---|
| Playwright + CDP 真实下载链路 | 无登录态、本机无 Playwright、桌面机不可达 | 登录后第一条命令 `fetch --dry-run` |
| 真实下载控件选择器 | 匿名页不渲染 | 同上，快照 + `--selector` |
| 真实下载额度/风控阈值 | 需登录 | 首次 `fetch --limit 5` 观察 |
| `adopt` 用真实站点 PDF 匹配 | 只用 Chrome 生成的有文字层 PDF 验过（score 1.0）；站点真实版式未验 | 首批手工下载后跑 `adopt --dry-run` |
| 真实报告与 report_meta 的关联率 | 无真实正文 | 首批入库后 C6 出数 |
