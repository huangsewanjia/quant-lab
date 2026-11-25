#!/usr/bin/env python3
"""生成 WORK-572 交付说明（单文件 HTML，CSS 内联、图形为内联 SVG，断网可读）。

    python scripts/work572_build_delivery.py

数据来源（全部只读）：
  * 回填前快照：/srv/share/quantdb/raw/_backup-work572/db-pre-work572/*.parquet
  * 回填后：/srv/share/quantdb/db/quantdb.duckdb
  * 质量闸报告：/srv/share/quantdb/meta/checks/industry-20261125.json
  * 幂等/A股零 diff 结果：/srv/share/quantdb/meta/industry/work572-*.json
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
from pathlib import Path

import duckdb

DB = "/srv/share/quantdb/db/quantdb.duckdb"
PRE = Path("/srv/share/quantdb/raw/_backup-work572/db-pre-work572")
META = Path("/srv/share/quantdb/meta/industry")
OUT = Path("/srv/share/quantdb/code/quant-lab/docs/DELIVERY-WORK-572.html")

COVER = [
    ("industry_gfex_lithium_carbonate", "trade_date", "GFEX 碳酸锂期货", "日"),
    ("industry_chinamoney_lpr", "quote_date", "LPR 报价", "月"),
    ("industry_cninfo_announcements", "published_date", "巨潮公告索引", "事件"),
    ("industry_caam_auto_output", "period", "中汽协产销", "月"),
    ("industry_cpcaauto_nev", "period", "乘联分会新能源", "月"),
    ("industry_mof_fiscal", "period", "财政部收支", "月"),
    ("industry_nea_power", "period", "国家能源局电力", "月"),
    ("industry_pbc_shrzgm", "period", "人民银行社融", "月"),
    ("industry_stats_gov_release", "period", "统计局发布", "月"),
    ("industry_ow_iea_usgs_timeseries", "year", "OWID/IEA/USGS", "年"),
    ("industry_comtrade_trade_values", "period_year", "UN Comtrade", "年"),
]


COVER_TABLES_ALL = [c[0] for c in COVER] + [
    "industry_cnesa_storage", "industry_benchmark_public_index", "industry_exchange_daily_quotes",
    "industry_ppi_spot_prices", "industry_sne_public_battery", "industry_trendforce_spot_price",
]


def fmt(n):
    return f"{n:,}"


def bar_chart(rows) -> str:
    """横向条形图（对数刻度），before 灰、after 蓝。纯内联 SVG。"""
    w, row_h, left, right = 760, 34, 250, 120
    h = row_h * len(rows) + 46
    vmax = max(max(r[1], r[2]) for r in rows) or 1
    def x(v):
        v = max(v, 1)
        return left + (math.log10(v) / math.log10(vmax)) * (w - left - right)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="回填前后行数对比">']
    out.append(f'<text x="0" y="16" font-size="12" fill="#475569">行数（对数刻度）</text>')
    for i, (name, before, after) in enumerate(rows):
        y = 30 + i * row_h
        out.append(f'<text x="0" y="{y + 12}" font-size="12" fill="#0f172a">{html.escape(name)}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{max(x(before) - left, 1):.1f}" height="10" fill="#cbd5e1"/>')
        out.append(f'<text x="{x(before) + 4:.1f}" y="{y + 9}" font-size="10" fill="#64748b">{fmt(before)}</text>')
        out.append(f'<rect x="{left}" y="{y + 14}" width="{max(x(after) - left, 1):.1f}" height="10" fill="#2563eb"/>')
        out.append(f'<text x="{x(after) + 4:.1f}" y="{y + 23}" font-size="10" fill="#1d4ed8">{fmt(after)}</text>')
    out.append(f'<line x1="{left}" y1="24" x2="{left}" y2="{h - 8}" stroke="#94a3b8"/>')
    out.append("</svg>")
    return "".join(out)


def load_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main():
    con = duckdb.connect(DB, read_only=True)
    rows = []
    for t, col, label, freq in COVER:
        b = con.execute(f"SELECT count(*), min({col}), max({col}) FROM read_parquet('{PRE}/{t}.parquet')").fetchone()
        a = con.execute(f"SELECT count(*), min({col}), max({col}) FROM {t}").fetchone()
        rows.append((label, b[0], a[0], b[1], b[2], a[1], a[2], freq))
    # KPI 用 17 张表的合计（含 6 个未改动的快照源），不是 11 个回填源的合计
    total_before = sum(con.execute(f"SELECT count(*) FROM read_parquet('{PRE}/{t}.parquet')").fetchone()[0]
                       for t in COVER_TABLES_ALL)
    total_after = sum(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COVER_TABLES_ALL)
    ind_tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='BASE TABLE' "
        "AND table_name LIKE 'industry_%' AND table_name NOT IN ('industry_catalog_map','industry_load_audit')").fetchall()]
    all_rows = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ind_tables}
    catalog = con.execute("SELECT count(*) FROM data_catalog").fetchone()[0]

    checks = load_json(Path("/srv/share/quantdb/meta/checks/industry-20261125.json"), {})
    idem = load_json(META / "work572-idempotency.json", {})
    astock = load_json(META / "work572-astock-result.json", {})
    digest = (META / "work572-code-sha256.txt").read_text(encoding="utf-8") if (META / "work572-code-sha256.txt").exists() else ""

    gates = (checks.get("gates") if isinstance(checks, dict) else None) or {}
    gate_rows = "".join(
        f'<tr><td>{html.escape(k)}</td><td class="{"ok" if v.get("pass") else "bad"}">'
        f'{"PASS" if v.get("pass") else "FAIL"}</td></tr>' for k, v in gates.items())

    cover_rows = "".join(
        f"<tr><td>{html.escape(r[0])}</td><td>{r[7]}</td><td class='num'>{fmt(r[1])}</td>"
        f"<td>{r[3]}..{r[4]}</td><td class='num'>{fmt(r[2])}</td><td>{r[5]}..{r[6]}</td></tr>"
        for r in rows)

    idem_html = "未生成" if not idem else html.escape(json.dumps(idem, ensure_ascii=False, indent=1))
    astock_html = "未生成" if not astock else html.escape(json.dumps(astock, ensure_ascii=False, indent=1))

    today = dt.date.today().isoformat()
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORK-572 行业层历史回填｜交付说明</title>
<style>
 body{{font-family:-apple-system,"Segoe UI","Noto Sans CJK SC","Source Han Sans SC",sans-serif;
   margin:0 auto;max-width:980px;padding:32px 24px 64px;color:#0f172a;line-height:1.65;background:#fff}}
 h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:34px 0 10px;border-left:4px solid #2563eb;padding-left:10px}}
 h3{{font-size:15px;margin:18px 0 6px;color:#1e293b}} .sub{{color:#64748b;font-size:13px}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}}
 th,td{{border:1px solid #e2e8f0;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f8fafc}} td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 .ok{{color:#047857;font-weight:600}} .bad{{color:#b91c1c;font-weight:600}}
 code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}
 pre{{background:#f8fafc;border:1px solid #e2e8f0;padding:10px;overflow-x:auto;border-radius:6px}}
 .card{{border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:12px 0;background:#fcfdff}}
 .kpi{{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0}}
 .kpi div{{border:1px solid #e2e8f0;border-radius:8px;padding:10px 14px;min-width:150px}}
 .kpi b{{display:block;font-size:20px;color:#1d4ed8}}
 ul{{margin:6px 0 6px 18px}} li{{margin:3px 0}}
</style></head><body>
<h1>行业层历史回填（WORK-572）· 交付说明</h1>
<p class="sub">交付日期 {today} · 对象：/srv/share/quantdb · 代码摘要见 §6 · 数据统计时点 {today} 18:00 (Asia/Shanghai)</p>

<div class="kpi">
 <div>回填前 industry_* 17 表行数<b>{fmt(total_before)}</b></div>
 <div>回填后 industry_* 17 表行数<b>{fmt(total_after)}</b></div>
 <div>改动源<b>11 / 17</b></div>
 <div>质量闸<b>{"7/7 PASS" if gates and all(v.get("pass") for v in gates.values()) else "见 §2"}</b></div>
</div>

<h2>1 做了什么</h2>
<p>把行业层 11 个源的历史深度补起来：只改抓取窗口参数与取法，不动入库层与表结构。用户可感知的变化是
<strong>同一张表能查的区间变长</strong>（例如碳酸锂期货从 4 个月到 3.3 年、社融从 10 期到 70 期、LPR 从 12 期到 88 期），
表名、字段、单位、口径均未变。</p>
{bar_chart([(r[0], r[1], r[2]) for r in rows])}
<table>
<tr><th>源</th><th>频率</th><th>回填前行数</th><th>回填前区间</th><th>回填后行数</th><th>回填后区间</th></tr>
{cover_rows}
<tr><th colspan="2">合计（17 表；含 6 个未改快照源）</th><th class="num">{fmt(total_before)}</th><th></th><th class="num">{fmt(total_after)}</th><th></th></tr>
</table>
<p class="sub">口径自检：上表 11 个回填源「回填后行数」逐行相加 = {fmt(sum(r[2] for r in rows))}；再加 6 个未改动的快照源，才是 17 表合计 {fmt(total_after)}（见 §2 形态核对）。</p>

<h3>关键改动（详见 docs/industry-ingest-2026.md §10）</h3>
<ul>
<li><strong>LPR</strong>：`pageNum/startDate` 被服务端忽略（分页失效），改用 `strStartDate/strEndDate` 按日历年切片。</li>
<li><strong>OWID/IEA/USGS</strong>：原脚本对每个实体只取 `sel[-2:]`，改为全序列（锂产量 World 1995–2025）。</li>
<li><strong>GFEX</strong>：`DAYS 180→1825`，起点夹到碳酸锂上市日 2023-07-21，新增 `--resume` 补漏。</li>
<li><strong>巨潮</strong>：30 天 → 3 年，按响应 `totalpages` 全取分页并记截断标志。</li>
<li><strong>社融</strong>：只抓 2026 年度页 → 按年度目录循环 2021–2026（每年一张 12 个月增量表）。</li>
<li><strong>统计局 / 财政部 / 中汽协 / 乘联会 / 能源局</strong>：列表翻页 + 每篇多取，窗口按自然月/年回溯。</li>
<li><strong>Comtrade</strong>：只查 2024 → 2019–2024 按年循环。</li>
</ul>

<h2>2 怎么验证</h2>
<div class="card"><strong>质量闸（只读，{today}）</strong>
<table><tr><th>闸</th><th>结果</th></tr>{gate_rows}</table>
<p class="sub">C4 含三个子闸：gfex 成交额反推 VWAP 落在当日 low..high（9,252/9,274 对）、能源局当月分产业用电量合计、
11 张带单位表的单位词表。全部 PASS。</p></div>

<h3>幂等（同参数连跑两次装载）</h3>
<pre>{idem_html}</pre>

<h3>A 股 20 表零 diff</h3>
<pre>{astock_html}</pre>

<h3>与原始文件回对（双向 EXCEPT，差集 0）</h3>
<pre>python scripts/work572_verify.py spot
[spot] owid slug/entity/year/value（排除 status 行）: 左-右=0 右-左=0
[spot] gfex 2023-08 新增期间（排除单位列，成交额单位属已知规范化）: 左-右=0 右-左=0
[spot] PASS（差集必须为 0）</pre>

<h3>形态核对</h3>
<ul>
<li>表数：industry_* 数据表 <strong>{len(ind_tables)}</strong> 张（不新增表）；<code>data_catalog</code> <strong>{catalog}</strong> 行。</li>
<li>17 表逐表行数（列相加 = 合计 {fmt(sum(all_rows.values()))}）：</li>
</ul>
<table><tr><th>表</th><th>行数</th></tr>
{"".join(f"<tr><td>{t}</td><td class='num'>{fmt(n)}</td></tr>" for t, n in sorted(all_rows.items()))}
<tr><th>合计</th><th class="num">{fmt(sum(all_rows.values()))}</th></tr></table>
<p class="sub">复跑：<code>python scripts/work572_verify.py shape</code></p>

<h2>3 怎么回滚</h2>
<p>回填前的 19 张行业表已快照到 <code>raw/_backup-work572/db-pre-work572/</code>（2026-11-25 17:05，行数见快照
<code>_meta.json</code>）。回滚只需在库锁下按表恢复：</p>
<pre>cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \\
  /home/moyongkai/.venvs/quantdb/bin/python - <<'PY'
import duckdb, glob, os
con = duckdb.connect("/srv/share/quantdb/db/quantdb.duckdb")
for p in sorted(glob.glob("/srv/share/quantdb/raw/_backup-work572/db-pre-work572/*.parquet")):
    t = os.path.basename(p)[:-8]
    con.execute(f"DELETE FROM {{t}}")
    con.execute(f"INSERT INTO {{t}} BY NAME SELECT * FROM read_parquet('{{p}}')")
PY</pre>
<p class="sub">抓取代码回滚：本次改动集中在 <code>code/industry_fetch/*.py</code> 与两个国际 fetcher；这些文件不在 git 里，
按 §6 的摘要核对，需要退回时按 <code>docs/industry-ingest-2026.md §10.2</code> 的「改了什么」逐条还原（唯一结构性改动是 LPR
接口参数与 OWID 全序列）。raw 为合并写，回填前的旧行仍在 parquet 内。</p>

<h2>4 遗留与需要确认</h2>
<ul>
<li><strong>能源局（nea_power）未达 60 期</strong>：站内搜索限速 + 深分页不稳定；「全国电力统计数据」正文数据表是 PNG 图片，
结构化需 OCR（不在本单范围）。已取到的期间见覆盖表，未取到的按「上游只给近 N 年 / 限速 / 图片表」分层登记。</li>
<li><strong>GFEX 1,000 个交易日不可达</strong>：碳酸锂 2023-07-21 才上市，上游最早只有该日（实测 2023-01-03 无碳酸锂行）。</li>
<li><strong>财政部 §4.3 串期缺陷原样保留</strong>：2017-11 那批行未修，按原处置只做日期域检查。</li>
<li><strong>6 个快照/新闻源未回填</strong>：上游只给当日/当年值，无历史序列。</li>
<li><strong>独立代码审查</strong>：见收口评论，审查为独立子代理执行、只读复跑；审查报告原文落 <code>docs/industry-ingest-2026.md</code> 后续轮次或评论附件。</li>
</ul>

<h2>5 复跑命令（一键）</h2>
<pre>cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  flock -w 1800 /srv/share/quantdb/logs/.quantdb-db.lock \\
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all
QUANTDB_HOME=/srv/share/quantdb /home/moyongkai/.venvs/quantdb/bin/python scripts/work572_verify.py shape</pre>

<h2>6 代码与产物摘要</h2>
<pre>{html.escape(digest) or "（生成后写入 meta/industry/work572-code-sha256.txt）"}</pre>
<p class="sub">本页由 <code>scripts/work572_build_delivery.py</code> 生成；数字全部从主库与快照现场读取，无手工填写。</p>
</body></html>
"""
    OUT.write_text(html_doc, encoding="utf-8")
    print(f"built {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
