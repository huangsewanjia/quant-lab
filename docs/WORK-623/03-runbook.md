# 03 · 跑法（WORK-623）

没参与过的人照下面顺序做即可复现。所有路径为绝对路径。

## 0. 前置：登录态

1. 用户在自己电脑（DESKTOP-TS1FCDR）用独立 profile 启动 Chrome（由 WORK-624 完成）：

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\fw\fxbaogao-profile" https://www.fxbaogao.com/
```

2. 在该窗口登录，确认右上角显示「高级VIP」。
3. 采集机上安装依赖（只装 Python 包，不下载浏览器内核，因为用的是上面这个 Chrome）：

```
python -m venv C:\fw\venv-623
C:\fw\venv-623\Scripts\pip install playwright pypdf
```

## 1. 采集（DESKTOP-TS1FCDR）

把仓库放到桌面机能访问的路径（Samba 共享或 git clone），在仓库根目录执行：

```bash
# 1) 先诊断：登录态 + robots + 详情页 + 下载控件候选，不点击、不下载
python scripts/fxbaogao_fulltext.py fetch --input docs/WORK-623/sample_candidates.csv \
    --limit 1 --dry-run --cdp http://127.0.0.1:9222
# 产物：/srv/share/quantdb/raw/reports_fulltext/fxbaogao/dry_run_report.json
# 看 login_state.advanced_vip 是否为 true；controls 里是否出现 ≥1 个选择器

# 2) 正式抓取（默认只认高级 VIP；单轮 20 篇、间隔 1.05s）
python scripts/fxbaogao_fulltext.py fetch --input docs/WORK-623/sample_candidates.csv \
    --limit 20 --cdp http://127.0.0.1:9222 --out /srv/share/quantdb/raw/reports_fulltext/fxbaogao/batch-$(date +%F).jsonl
```

失败明细固定写 `/srv/share/quantdb/raw/reports_fulltext/fxbaogao/failed.jsonl`，每行：

```json
{"report_id": 5884540, "url": "https://www.fxbaogao.com/detail/5884540", "http_status": 200,
 "reason": "download_control_not_found（页面快照: …/_diagnostics/5884540.html）", "path": "", "ts": "…"}
```

选择器不匹配时，用快照里的真实控件覆盖：

```bash
python scripts/fxbaogao_fulltext.py fetch --input … --limit 20 \
    --selector 'a[href^="/pdf"]' --selector 'button.download-btn'
```

## 2. 手工兜底（不装 Playwright、或自动化被阻断）

```bash
# 生成单文件清单（断网可读，含筛选与文件名复制）
python scripts/fxbaogao_fulltext.py sheet --input candidates.csv --out /srv/share/quantdb/raw/reports_fulltext/fxbaogao/sheet.html
```

用户打开 `sheet.html` → 点标题 → 点站点「下载」→ 把 PDF 原样放进
`/srv/share/quantdb/raw/reports_fulltext/fxbaogao/_inbox/`（Windows 侧对应映射到 quantdb 共享盘的同一目录）。

## 3. 收编与入库（Mo-server）

```bash
cd /srv/share/quantdb/code/quant-lab
PY=/home/moyongkai/.venvs/quantdb/bin/python

# 手工下载的 PDF -> 规范目录（文件名自动改成 <report_id>-<slug>.pdf）
$PY -m db.etl.report_fulltext adopt --queue candidates.jsonl --inbox /srv/share/quantdb/raw/reports_fulltext/fxbaogao/_inbox
# 匹配不上/并列的写 unmatched.jsonl，不猜

# 入库（可重复跑）
$PY -m db.etl.report_fulltext ingest

# 对账
$PY -m db.checks.report_fulltext all
```

`ingest` 输出示例（彩色空库/彩排实测）：

```json
{"root": "…/fxbaogao", "scanned": 5, "inserted": 5, "updated": 0, "unchanged": 0, "failed": 0, "changes": 0}
```

再跑一次：

```json
{"root": "…/fxbaogao", "scanned": 5, "inserted": 0, "updated": 0, "unchanged": 5, "failed": 0, "changes": 0}
```

## 4. 抽查（验收标准 1）

```bash
$PY scripts/fxbaogao_fulltext.py verify --meta "/srv/share/quantdb/raw/reports_fulltext/fxbaogao/2026-11-16/5884540-xxx.meta.json"
```

输出把「落盘标题 / 重取标题 / 是否一致 / pypdf 页数」打一行；任一不一致退出码 1。

## 5. 日志与证据在哪

| 内容 | 路径 |
|---|---|
| 请求台账（每次 HTTP） | `/srv/share/quantdb/raw/reports_fulltext/_probe/request_ledger.json` |
| 失败台账 | `/srv/share/quantdb/raw/reports_fulltext/fxbaogao/failed.jsonl` |
| 收编未匹配 | `/srv/share/quantdb/raw/reports_fulltext/fxbaogao/unmatched.jsonl` |
| 登录态证据 | `/srv/share/quantdb/raw/reports_fulltext/fxbaogao/login_state.json` |
| 控件诊断快照 | `/srv/share/quantdb/raw/reports_fulltext/fxbaogao/_diagnostics/<id>.html` |
| 对账报告 | `/srv/share/quantdb/meta/checks/report_fulltext.json` |
| 可行性原始证据 | `/srv/share/quantdb/raw/reports_fulltext/_evidence/2026-11-27/` |

## 6. 常见失败怎么办

| 现象 | 先查 | 处置 |
|---|---|---|
| 退出码 3「登录态不是高级 VIP」 | `login_state.json` | 让用户在 Chrome 里重新登录/续费；不要在未登录时硬试 |
| `download_control_not_found` | `_diagnostics/<id>.html` | 找真实按钮选择器，用 `--selector` 覆盖；不要改成裸请求 `/pdf` |
| PDF 校验失败：不是 PDF | 文件头 | 多半下到了登录页/错误页；确认登录态后重抓，该条已在 failed.jsonl |
| 页数 0 | 文件 | 空壳 PDF，删掉重抓；不会入库 |
| adopt unmatched | `unmatched.jsonl` 的 top3 | 补队列标题或人工按 report_id 重命名放进规范目录后直接 `ingest` |

## 7. 回滚

```sql
-- 只回滚本单对象，不碰既有 20 张表与行业层
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

目录回滚：`rm -rf /srv/share/quantdb/raw/reports_fulltext/fxbaogao`（正文 PDF 与 meta 一并删除；该目录不在 git 内，仓库不受影响）。

## 8. 版权口径

正文 PDF 仅限内部检索与研究使用，**不得对外分发、不得作为引用来源转载、不得整段复制进对外材料**。
仓库是 public：`*.pdf` 与 `raw/` 已被 `.gitignore` 排除，正文与凭据一律不入 git。
