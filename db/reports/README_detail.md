# 研报正文 EPS 抽取（子单 B）

> 补 `report_meta` 的历史缺口：2023–2024 年研报在列表接口里被按发布年阶梯截断三年 EPS/PE（2023 当年空 99.97%，2024 次年空 99.99%）。本目录**只抓研报详情页正文**，用规则优先 + 确定性兜底把三年 EPS 抽出来，落新表 `report_eps_extracted`；**不改** `report_meta`、`consensus_eps`、`rating_change`，**不下载 PDF**。

数据仅限内部研究（研报有版权）：原始 HTML 只落本机 `/srv/share/quantdb/raw/`，`.gitignore` 已覆盖 `raw/reports`；仓库里只有代码与本文档。

---

## 1. 入口与边界

| 项 | 值 |
|---|---|
| 详情页 | 由 `info_code` 重建 URL（`report_meta.encode_url` 是加密 token，不是 URL；见 §2） |
| 抓取引擎 | Lightpanda 1.0.5，二进制 sha256 `0ec21fa4867ab9e82b35285ff4ab0e7336c6c1d89f26fe991f27d73c8a678bd7`（WORK-540 已验）。`/tmp` 会被清空，本单落盘在 `/srv/share/quantdb/staging/tools/lightpanda-1.0.5/lightpanda-x86_64-linux`，每次开工先核 sha256 |
| 正文容器 | `<div id="ctx-content" class="ctx-content">`；抽取前先切出该容器（`slice_body_html`），避免页面外壳的「最新研报」「机构一致看多」等表格把别的 EPS 混进来 |
| 目标篇数 | 2023 年 22,074 + 2024 年 19,934 = **42,008 篇** |
| 正文落盘 | `raw/reports/detail/<publish_date>/<info_code>/body.html.gz` |
| 审计记录 | 同目录 `meta.json`（URL/状态码/字节/sha256/抓取时间/robots 判定/失败分类） |
| 抽取产物 | `meta/reports/detail/extracted/<publish_date>/<info_code>.json` |
| 进度日志 | `meta/reports/detail/progress.jsonl`（每次尝试一行，追加写） |
| 运行清单 | `meta/reports/detail/runs/<run_id>.json` |
| 结构化表 | `report_eps_extracted`（主键 `info_code, fiscal_year`） |

**不做**：不绕过验证码、不登录、不换 IP、不下载 PDF 全文、不把正文对外转载。

---

## 2. 详情页 URL 不是 `encode_url`

`report_meta.encode_url` 是上游的加密串（形如 `9XOvc0O1K2WYB/UcSrypqKEfEKhu3reerb8BSz/3OME=`），不是链接。`fetch_detail.py` 用 `info_code` 重建，模板在 `URL_TEMPLATES`：

| 模板 | URL |
|---|---|
| `info`（默认） | `https://data.eastmoney.com/report/info/{info_code}.html` |
| `zw_infocode` | `https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info_code}` |
| `zw_encode` | `https://data.eastmoney.com/report/zw_stock.jshtml?encodeUrl={urlencode(encode_url)}` |

`probe` 子命令对同一批研报依次试三个模板，实测哪个返回可解析正文就锁进 worklist（`url_template` 字段），全量抓取只用一个模板，不做运行期试探。`fetch --url-template <name>` 可在不重写 worklist 的前提下覆盖模板。

---

## 3. 反爬契约（代码强制，不是注意事项）

七条全部落在 `fetch_detail.py` 里，改参数等于改代码：

| # | 规则 | 实现位置 |
|---|---|---|
| 1 | 并发 = 1 | 单进程顺序 for 循环，无线程池；Lightpanda 一次只开一个 URL；`RunLock`（`fcntl.flock`，`meta/reports/detail/.fetch.lock`）保证同一时刻只有一个 `fetch`/`probe` 进程，第二个进程直接退出 |
| 2 | 间隔 ≥ 3 s + 0~2 s 抖动；每 500 篇歇 10–20 min | `RateLimiter`（`MIN_INTERVAL_S=3.0`、`JITTER_MAX_S=2.0`、`REST_EVERY=500`、`REST_MIN/MAX=600/1200`） |
| 3 | 只在 22:00–07:00（Asia/Shanghai）跑 | `in_window()`；`fetch`/`probe` 入口与每篇之前都检查，窗口关闭即停 |
| 4 | 先查 robots.txt，禁抓路径零请求 | `fetch_robots()` + `parse_robots()`（404 视为全允许；5xx/不可达视为全禁）；`robots.allows(url)` 在请求前判定，被禁记为 `robots-disallowed` 且不发请求 |
| 5 | 单一 UA，不轮换、不用代理 | `USER_AGENT = quantdb-research/1.0 (internal research; low-rate; ...)`，全流程唯一；Lightpanda 参数禁止 `Mozilla` 伪装 |
| 6 | 403/429/验证码/登录墙 → 停 30 min 指数退避，连续 3 次停手 | `BLOCKING_CLASSES` + `BLOCK_BACKOFF_S=(1800, 3600, 7200)` + `MAX_CONSECUTIVE_BLOCKS=3` |
| 7 | 每日 ≤ 5,000 篇、单 run ≤ 3,000 篇、断点续跑 | `DAILY_CAP`/`RUN_CAP`（CLI 只能调低）；`doc_done()` 按 `meta.json` + `body.html.gz` sha256 判重 |

`stats`/`plan`/`extract`/`build` 不联网，白天可跑。

---

## 4. 复跑命令

环境：`/home/moyongkai/.venvs/quantdb/bin/python`（Python 3.14，duckdb 2.0.1）。仓库根目录执行。

```bash
export LIGHTPANDA_BIN=/tmp/work540-clean/bin/lightpanda-x86_64-linux   # 或用本任务落盘的副本

# 0) 抽样工作清单（读库，不联网）
python db/reports/fetch_detail.py plan --stocks 600519,002594,300750 \
  --start 2023-01-01 --end 2024-12-31 --limit 200 \
  --worklist /srv/share/quantdb/meta/reports/detail/worklist-stage0-200.jsonl

# 1) 先探 URL 模板（每篇 3 个模板各一次；只在窗口内跑）
python db/reports/fetch_detail.py probe --info-code AP202301011581615358

# 2) 抓正文（断点续跑；窗口/日限/单 run 限自动生效）
python db/reports/fetch_detail.py fetch \
  --worklist /srv/share/quantdb/meta/reports/detail/worklist-stage0-200.jsonl --resume --verbose

# 3) 抽取 + 落表（不联网）
python db/reports/extract_eps.py extract --worklist /srv/share/quantdb/meta/reports/detail/worklist-stage0-200.jsonl
python db/reports/extract_eps.py build
python db/reports/extract_eps.py stats

# 4) 覆盖度
python db/reports/fetch_detail.py stats
```

---

## 5. 表结构 `report_eps_extracted`

| 列 | 含义 |
|---|---|
| `info_code` | 研报 ID，与 `report_meta.info_code` 对应 |
| `fiscal_year` | 预测财年（发布年 +0/+1/+2，取自 `report_meta.fiscal_year_*`） |
| `eps` | 每股收益预测值（元） |
| `stock_code` / `ts_code` | 标的（便于 join `stock_basic`） |
| `publish_date` | 研报发布日 |
| `source` | `body_table`（正文表格）/ `body_text`（正文段落） |
| `raw_text` | 该值所在的**源行原文**（表格行或整句） |
| `char_offset` | `raw_text` 在归一化正文文本中的字符偏移 |
| `confidence` | `rule`（表格 / 年份区间模板）/ `nlp`（单年份句式兜底） |
| `extractor` | 具体规则名：`table_eps_row` / `text_range` / `text_list` / `text_single_year` |
| `extract_version` | 抽取器版本 |
| `extracted_at` | 抽取时间（UTC） |

主键 `(info_code, fiscal_year)`。**无出处的值不入表**：`raw_text` 为空或 `char_offset` 越界时该值被丢弃。

### 正文切分

`slice_body_html()` 从 `#ctx-content` 切出正文容器；容器不存在时退回整页并记 `body_scope=whole-document`（可在 `sample` 里看到）。抽取只在这个片段上做，页面外壳的表格不参与。

### 抽取优先级

1. `table_eps_row`（`rule`）：`<tr>` 首列命中 `EPS`/`每股收益`/`摊薄每股收益`，列头是财年（`2024E`/`2024年`），取对应单元格数字；
2. `text_range`（`rule`）：`2023-2025年EPS分别为1.2/1.5/1.8元` 这类显式年份区间 + 等长数值；
3. `text_list`（`rule`）：`2023/2024/2025年EPS分别为…元`，句内至少两个年份锚点才绑定；
4. `text_single_year`（`nlp`）：`2024年EPS为2.35元`，仅在 1–3 都没有该财年时兜底。

同一财年多个候选时按上述顺序取第一个。数值绝对值 > 1000 或非有限值直接丢弃。**不用 LLM 猜数**；年份无法绑定则不产出（例如只提「参考 2023 年」再给三个数，属歧义，不猜）。

---

## 6. 失败分类

| 分类 | 含义 | 处理 |
|---|---|---|
| `ok` | 正文已落盘 | 计入抓取成功 |
| `403-WAF` | 上游拒绝 | 停 30 min 指数退避；连续 3 次停手回报 |
| `429` | 限流 | 同上 |
| `captcha` | 验证码 / 人机验证页（含 Lightpanda `bot_challenge`）。判定只看**可见文本**：正常页面 JS 配置里也含 `captcha` 字样，用整页原文做子串匹配会误报 | 同上，**不绕过** |
| `login` | 登录墙 | 同上，**不登录** |
| `pdf-only` | 返回 PDF 而非 HTML | 记失败，不解析 PDF（需另定合规口径） |
| `timeout` | 导航超时 | 下轮重试 |
| `schema-changed` | 200 但没有可解析正文（可见文本 < 200 字），或非 200/非 403/429 | 记失败，需人工看样张 |
| `robots-disallowed` | robots.txt 禁止 | **零请求**，只记录 |

---

## 7. 回滚

```bash
duckdb /srv/share/quantdb/db/quantdb.duckdb -c "DROP TABLE IF EXISTS report_eps_extracted;"
rm -rf /srv/share/quantdb/raw/reports/detail
rm -rf /srv/share/quantdb/meta/reports/detail
```

只删本单新增资产；`report_meta`、`consensus_eps`、`rating_change` 与既有 P0 表不受影响。回退代码即回退 `db/reports/fetch_detail.py`、`db/reports/extract_eps.py`、`db/reports/tests/test_*`、本文件。

---

## 8. 已知限制

- 只覆盖详情页 HTML 正文；**PDF 正文里的表格不解析**。若上游正文页只给摘要、EPS 表只在 PDF 里，本路线在该篇上产出 0 值（计入 `no-eps`，在报告里单列）。
- 券商表述差异大（`EPS`/`每股收益`/`摊薄每股收益`/`归母每股收益`），规则覆盖不到时宁可空也不猜。
- 抽取质量按抽样人工对照衡量，不宣称全量准确率。
- 42,008 篇在「每日 ≤5,000、单 run ≤3,000、22:00–07:00」下不可能一夜跑完；按单 run 3,000 篇估算需约 14 个夜间批次，manifest 记进度、可断点续跑。
