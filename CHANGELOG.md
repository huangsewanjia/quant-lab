# Changelog

## 2026-11-27 · WORK-623 API 版审查修复（round 1 → round 2）

- round 1（`f7ba957`，NEEDS WORK）：PDF 端点失败时台账写 download 的 `code=200`，服务端额度错误丢失 → 改为 download 一成功即落 `.download.json`、PDF 失败解析其 `code/msg` 并写 `pdf_error`；`--pages` 越界报错；`code` 字符串归一化；token 权限硬校验；org 可选。
- round 2（`c9a181e`，NEEDS WORK）：非 2xx 的 PDF 回包正文在 `body` 而非 `raw`，仍读不到 → 改为 `raw or body.encode()`；`no_pdf_url` 分支也落 `.download.json`（`reason=no_pdf_url`）；token 权限按符号链接真实目标判定；bool `code` 判失败；C6 口径补「机构为空不参与关联」。测试 44 条（26 + 18），全套 206 passed。
- 第三轮评审按守则「最多 2 轮，第 3 轮前停下问用户」未执行；上述修复由新增单测覆盖。

## 2026-11-27 · WORK-623 契约变更 01：改走官方 API（浏览器方案作废）

按大管家 01:40 契约变更重做：CDP 复用桌面 Chrome、cookie 文件、sheet 手点、adopt 收编全部**删除**（首版从未取到真实数据），采集改为官方 HTTP API。

新增/重写：

- `scripts/fxbaogao_fulltext.py`：`check-auth`（一次搜索验 token，不扣下载额度）、`search`（`pageNum` 1–10，原始回包落 `search_raw/page*.json`）、`fetch`（`GET /mofoun/agent/download` 取地址 → 拉 PDF → `%PDF`/pypdf 页数校验 → 落盘 + `meta.json` + `download.json`）、`verify`（官网详情页标题核对）。无 Playwright、无浏览器。
- `scripts/work537_probe.py`：HTTP 基础增加 `http_request`（GET/POST/Authorization/二进制），`http_get` 保持原签名；台账新增 `method`。
- `db/schema_report_fulltext.sql`：`report_fulltext` 增加 `site_url`（官方回查链接）与 `pdf_url`，并对已建旧表 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 补列。
- `db/etl/report_fulltext.py`：删除 `adopt`（手工收编作废）；入库校验增加 `site_url` 缺失按 id 补齐、失败台账带 `code/msg`。
- `db/checks/report_fulltext.py`：C4 要求 `report_id/url/http_status/code/msg/reason` 六字段。
- 测试：`test_work623_api.py`（26 条，FakeProbe 覆盖业务错误 300011/300012/300021、HTTP 403 额度错、额度无地址、非 PDF、跳过已存在、分页越界、token 权限/目录/符号链接）+ `test_report_fulltext.py`（18 条）；全套 206 passed。

口径要点：

- 成败看 `body.code`（0/200 成功）而不是 HTTP 状态：实测业务错误是 **HTTP 200 + code=300011**。
- token 只读 `/srv/share/quantdb/secrets/fxbaogao_api_key`，日志只写 sha256 前 8 位；无 token 时 `check-auth` 退出码 3 且**零请求**。
- 已存在同 id PDF 默认跳过（避免重复扣下载权益），`--force` 才重下。
- 每篇保留 `site_url=https://www.fxbaogao.com/view?id=<id>` 与 `pdf_url`；download 原始回包留 `.download.json` 作额度证据。

实测与限制：token 未写入（`secrets/` 未建），真实搜索/下载/额度/分页条数**未取到**；真库 schema 已应用（0 行），合成 PDF 彩排 5 篇两次 `5→5`、C1–C7 PASS、关联 5/5。


## 2026-11-27 · WORK-623 发现报告 VIP 正文采集 → quantdb 入库通道

新增（采集，DESKTOP-TS1FCDR 侧）：

- `scripts/fxbaogao_fulltext.py`：正文渠道 `fxbaogao_vip` 的批量入口——`candidates`（列表页→候选 CSV）、`plan`（候选→队列，补标题/机构/日期）、`fetch`（CDP 驱动用户已登录 Chrome，逐条落 PDF + meta.json，失败写 `failed.jsonl`）、`sheet`（单文件待下载清单）、`verify`（标题/页数抽查）、`robots`（判定打印）。节流、robots、请求台账复用 `scripts/work537_probe.py`，不新造框架。
- `scripts/work537_probe.py`：登记 `fxbaogao_vip` 渠道；默认 `probe` 跳过 `*_vip`，WORK-537 既有渠道表不受影响。

新增（入库，Mo-server 侧）：

- `db/schema_report_fulltext.sql`：`report_fulltext`（唯一键 `report_id`）、`report_fulltext_changes`（sha256 变更留痕）、`v_report_fulltext`、`v_report_fulltext_link_candidates`、`v_report_fulltext_linked`、`v_report_fulltext_unlinked`；标题/机构归一化只在 SQL 宏里定义（`fx_norm_title` / `fx_norm_org`）。
- `db/etl/report_fulltext.py`：`ingest`（扫描落盘 PDF，sha256/字节/页数三项对不上就写 `failed.jsonl`，幂等 `INSERT OR REPLACE`）、`adopt`（手工下载收编，首页文字匹配，歧义不猜）。
- `db/checks/report_fulltext.py`：C1–C7 对账闸（行数=文件数、页数>0、sha 对磁盘、失败台账字段、meta 完整、关联率分子/分母/口径、变更留痕一致性）。
- `db/tests/test_report_fulltext.py`（17 条）、`db/tests/test_work623_collector.py`（17 条）：离线测试，34 passed（全套 196 passed）。**（浏览器版已作废，测试文件已删除，见上条）**

实测与限制（见 `docs/WORK-623/01-feasibility.md`）：

- `robots.txt`（2026-11-27，124 B，sha256 `4ad59986…`）含 `Disallow: /pdf`：本通道 HTTP 客户端不请求 `/pdf`，PDF 只能由用户登录浏览器会话或手动点击产生。
- 匿名详情页 HTTP 200 / 85,752 B，无站内 `/pdf` 链接（0 处）。
- 登录态不存在（本机 CDP 9222 关闭、`secrets/` 未建、WORK-624 `todo`）：真实下载与 1–5 条验收**未执行**，不冒充已达成。
- 真库已建表（0 行）；合成 PDF 彩排 5 篇：ingest 两次 5→5、C1–C7 全 PASS、关联 5/5。
- 手工收编实测：headless Chrome 生成有文字层 PDF → `adopt` score 1.0 落盘 → `ingest` 6 篇、C6 5/6（1 篇为站点独家，未关联）。

独立审查（round 1，commit `956cfef`）3 个 blocking 已修：

- B1 请求台账目录：`_save_ledger` 改按 `LEDGER_PATH` 父目录建目录（原写死 `PROBE_DIR`，新落盘根首个请求即 FileNotFoundError）。
- B2 下载控件：删除默认 `a[href^="/pdf"]` 选择器，点击前用 `path_guard` 校验 `href`，指向 robots 禁止路径则判失败并提示 `sheet` 手点。
- B3 坏 meta：数值字段容错解析、顶层非对象直接判失败，`ingest`/C5 外层捕获异常，不再让整批 traceback。
- 另修 S1–S5（同 id 多文件 C1 伪通过、重抓换名残留、Windows 落盘根、adopt 坏 PDF、dry-run robots 口径）与 N1–N4。

独立审查（round 2，commit `70ac26e`）1 个 blocking 已修：`adopt` 的 `match_pdf` 原先在 try 外，坏 PDF 仍让整批退出 → 纳入单文件容错并补单测；同时收紧旧文件清理到日期目录（不再误删 `_inbox/`）、dry-run 不再导航 robots 禁止 URL、robots 拦截统一记 `http_status=0`，并补 `CdpDriver.download` 的 `path_guard` 直测。


## 2026-11-26 · WORK-537 补正（定向，0 新请求）

按验收意见做三条定向补正，只改文字与 CSV，**未发出任何新探测请求**：

- 请求量改为**可核对口径**：台账 85 条（73 重建 + 12 带时间戳）／实际发出 ≥87 次（round1 46 + round2 29 + 审计 12，均不含 robots.txt 抓取）；185 降级为「不可由台账复现的上界估计」。
- 如实写入 round-1 的 **5 次 robots 违规相关请求**（Barron's 401/200、FINRA 403×3），并新增结论行「本次发生 1 次 robots 违规（round 1，已修正；抓取内容未进入任何分析、未入样例）」；删除原文「未发任何正文请求 / 零次」。
- CSV 两行填真实状态码：Barron's（round-1 401 与 200）、FINRA（round-1 403×3）；Reuters/Bloomberg/FT/WSJ 措辞改为「无 HTTP 响应（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）」。

配套：`docs/WORK-537/verify_corrections.py`（0 请求复算请求量口径）；三个脚本的仓库路径改为**跟随脚本所在仓库**，可在 git worktree 中安全运行；`probe_log*.json` 与 `request_ledger.json` 逐字节不变（sha256 可核）。

## 2026-11-23 · WORK-537 外资研报公开层渠道清单与可抓性实测

新增：

- `docs/foreign_research_channels.csv`：29 个外资研报公开层渠道清单（12 列，实测证据列无空；采 13 / 观察 14 / 不采 2）。
- `docs/foreign_research_brief.md` + `docs/foreign_research_brief.html`：一页结论（配图 base64 内嵌，可打印）。
- `docs/WORK-537/`：五阶段落盘（`01-hypothesis.md`、`02-data-contract.md`、`02-data.csv`、`02-data.sha256`、`03-analysis.md`、`04-audit.md`、`05-memo.md`）。
- `scripts/work537_probe.py`：渠道可抓性探测（robots 优先、同域节流、请求台账、离线重判 reclass）。
- `scripts/work537_audit.py`：独立审计（curl 复跑、静态质量门 G1–G9、台账核查）。
- `scripts/work537_figures.py` / `scripts/work537_build_html.py`：配图与单文件 HTML 构建。

要点（实测结论）：

- 外资投行**评级/目标价在公开层实测不可得**（5 篇原文正文全文命中 0 处；唯一含完整评级的是中资平台转载）。
- 可机器采集入口 13 个，其中投行官网 7 个；建议每周一次增量（约 26 次请求/周）。

修复：

- robots 判定改用**落地 host** 的 robots.txt（原实现误用入口 host，把 BofA 误判为 robots 禁）。
- robots 分组解析按 RFC 9309 重写（原实现把 `googlebot` 组当 `*` 组，把 CNBC 误判为 robots 禁）。
- 「需 JS」判据改为 issue 口径「列表页能解析出标题+日期」（原按文本长度，把 CNBC/NYSE/东方财富误判为直连）。
- 支持无年份日期（“Nov 22”），按页内最新日期推断年份。
- 同域节流改为按**请求完成**时刻计算（原按开始时刻，出现 0.25s 的完成间隔）。
- 请求台账改为追加式落盘（原覆盖式，跨进程丢失 98 条记录）。

## 2026-11-23 · WORK-529 定时更新

新增：

- `db/etl/incremental.py`：行情水位、财务披露窗口、指数成分月度窗口、watermark、幂等装载。
- `db/etl/weekly.py`：调用成功率、失败清单、日历缺口、行数变化的周报。
- `db/timers/`：`daily.sh`、`finance.sh`、`policy.sh`、`weekly.sh` 与 4 个 systemd service/timer。
- `db/timers/backup-*.sh` 与 `systemd/backup-nightly.*`：冷备逻辑进入稳定仓库；home 脚本变成 shim。
- `db/policy/fetch_policy.py`：已知 URL 优先跳过；`--reconcile` 有界补抓。
- `db/README.md` §11、`docs/WORK-529.md`、`DELIVERY.html`。

修复：

- `common.POLICY_DIR` 缺失（政策采集的路径常量）。
- `normalize_date_columns` 对「已存 DATE + 新到 YYYYMMDD」混合列会把旧值转成 NaT。
- `drop_null_pk_rows` 会把合成列 `dup_seq` 当自然键，增量合并时误删新行。
- 财务/指数成分增量改为「旧分区 + 新行 + 内容去重」，重复装载行数不变。

实测：

- `pytest db/tests -q` → 162 passed。
- 行情增量在收盘前 no-op；模拟收盘后无数据返回 75 且不写空分区。
- 财务 2026-11-13 单日实测四次抓取 + dividend 21 个分区，重复跑行数不变。
- 冷备 rsync + `SHA256SUMS` 155,640 个文件校验通过。
- 四个 timer `systemctl --user list-timers` 均 active。
- 第五轮独立定向复审 `ec2fa63`：PASS，0 blocking。

## 2026-11-22 · WORK-513 建库 P1

新增 `db/`：

- `db/schema.sql`：15 张 P0 表，主键、单位注释、PIT 列。
- `db/etl/`：Tushare 增量 ETL、限流、并发、manifest、断点、失败清单；新浪单股冒烟。
- `db/checks/`：manifest、日历勾稽、主键、OHLC、跨源抽样、每日行数 6 项质量闸。
- `db/ql/` 与 `db/bin/ql`：只读查询 CLI，默认 200 行，table/json/csv。
- `db/policy/`：六源白名单政策采集，双时间轴，正文 txt + meta.json，同文去重。
- `db/tests/`：分区幂等、断点、manifest、ql 上限、schema、政策抽取，14 项测试。

文档：

- `docs/INDEX.md`、`PROJECT.md`、`SPEC.md`、`ENVIRONMENT.md`、`TECH-NOTES.md`、`RUNBOOK.md`。
- `docs/decisions/0001-storage-layout.md`、`0002-policy-collection.md`。

实测：

- 600000.SH 20 年：4,808 行，4,808 个 raw 分区，32.5 MB Parquet。
- DuckDB 装载 5.2 s，ql 查询正常。
- 质量闸 6 项 PASS。
- 政策 5 篇入库，6 条 missing。
