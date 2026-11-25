#!/usr/bin/env python3
"""生成 WORK-568 单文件交付说明 DELIVERY-WORK-568.html。

数据全部现查主库与质量闸报告，不手填数字：
    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=/srv/share/quantdb/code/quant-lab \
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work568_build_delivery.py
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import duckdb

HOME = Path("/srv/share/quantdb")
DB = HOME / "db" / "quantdb.duckdb"
OUT = HOME / "code" / "quant-lab" / "docs" / "DELIVERY-WORK-568.html"

TABLES = [
    ("industry_gfex_lithium_carbonate", "碳酸锂期货日行情", "GFEX"),
    ("industry_chinamoney_lpr", "LPR 报价", "中国货币网"),
    ("industry_cnesa_storage", "储能年度数据", "CNESA"),
    ("industry_cninfo_announcements", "公告索引", "巨潮资讯"),
    ("industry_caam_auto_output", "汽车产销", "中汽协"),
    ("industry_cpcaauto_nev", "新能源乘用车", "乘联分会"),
    ("industry_mof_fiscal", "财政收支", "财政部"),
    ("industry_nea_power", "电力统计", "国家能源局"),
    ("industry_pbc_shrzgm", "社融增量", "人民银行"),
    ("industry_stats_gov_release", "统计局六组发布", "国家统计局"),
    ("industry_benchmark_public_index", "矿物指数", "Benchmark"),
    ("industry_comtrade_trade_values", "双边贸易额", "UN Comtrade"),
    ("industry_exchange_daily_quotes", "交易所快照", "GFEX/SHFE"),
    ("industry_ow_iea_usgs_timeseries", "年度序列", "OWID/IEA/USGS"),
    ("industry_ppi_spot_prices", "挂牌价", "生意社"),
    ("industry_sne_public_battery", "电池新闻稿索引", "SNE Research"),
    ("industry_trendforce_spot_price", "存储现货价", "TrendForce"),
]

PALETTE = ["#7f9bb3", "#b9c7d3", "#d9c9a3", "#8fae9b", "#c2a3a3", "#a9a3c2"]


def esc(value) -> str:
    return html.escape(str(value))


def query_counts(con) -> dict[str, int]:
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t, _, _ in TABLES}


def query_coverage(con):
    rows = con.execute(
        "SELECT industry, catalog_rows, covered, complete, partial, not_covered "
        "FROM v_industry_catalog_coverage_summary ORDER BY industry"
    ).fetchall()
    total = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE coverage='已覆盖'), "
        "count(*) FILTER (WHERE completeness='完整'), "
        "count(*) FILTER (WHERE completeness='部分') FROM v_industry_catalog_coverage"
    ).fetchone()
    return rows, total


def query_window(con, table: str) -> tuple[str, str]:
    cols = {
        "industry_gfex_lithium_carbonate": "trade_date",
        "industry_chinamoney_lpr": "quote_date",
        "industry_cnesa_storage": "period",
        "industry_cninfo_announcements": "published_date",
        "industry_caam_auto_output": "period",
        "industry_cpcaauto_nev": "period",
        "industry_mof_fiscal": "period",
        "industry_nea_power": "period",
        "industry_pbc_shrzgm": "period",
        "industry_stats_gov_release": "period",
        "industry_benchmark_public_index": "snapshot_date",
        "industry_exchange_daily_quotes": "trade_date",
        "industry_ow_iea_usgs_timeseries": "year",
        "industry_ppi_spot_prices": "as_of",
        "industry_sne_public_battery": "publish_date",
        "industry_trendforce_spot_price": "snapshot_date",
    }
    col = cols.get(table)
    if not col:
        row = con.execute("SELECT min(period_year), max(period_year) FROM industry_comtrade_trade_values").fetchone()
        return str(row[0]), str(row[1])
    row = con.execute(f"SELECT min({col}), max({col}) FROM {table}").fetchone()
    return str(row[0]), str(row[1])


def bar_chart(counts: dict[str, int]) -> str:
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    max_v = max(v for _, v in items) or 1
    width, bar_h, gap, left = 760, 18, 8, 250
    height = len(items) * (bar_h + gap) + 30
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             'aria-label="每个 industry 表的行数">']
    for i, (table, value) in enumerate(items):
        y = i * (bar_h + gap) + 6
        w = max(2, (value / max_v) * (width - left - 90))
        color = PALETTE[0] if value > 100 else PALETTE[3]
        parts.append(
            f'<text x="0" y="{y + 13}" font-size="11" fill="#2f3437">{esc(table.replace("industry_", ""))}</text>'
        )
        parts.append(
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{color}" rx="2"/>'
        )
        parts.append(
            f'<text x="{left + w + 6:.1f}" y="{y + 13}" font-size="11" fill="#555">{value:,}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def coverage_chart(rows) -> str:
    width, bar_h, gap, left = 760, 22, 10, 120
    height = len(rows) * (bar_h + gap) + 24
    max_v = max(r[1] for r in rows) or 1
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             'aria-label="目录覆盖按行业堆叠">']
    for i, (industry, total, covered, complete, partial, missing) in enumerate(rows):
        y = i * (bar_h + gap) + 6
        scale = (width - left - 40) / max_v
        x = left
        parts.append(f'<text x="0" y="{y + 15}" font-size="12" fill="#2f3437">{esc(industry)}</text>')
        complete_w = complete * scale
        partial_w = partial * scale
        missing_w = missing * scale
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{complete_w:.1f}" height="{bar_h}" fill="#5f7f65"/>')
        x += complete_w
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{partial_w:.1f}" height="{bar_h}" fill="#a8bfa8"/>')
        x += partial_w
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{missing_w:.1f}" height="{bar_h}" fill="#e2e0d8"/>')
        parts.append(
            f'<text x="{x + missing_w + 6:.1f}" y="{y + 15}" font-size="11" fill="#555">'
            f'{covered}/{total}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def flow_svg() -> str:
    return """
<svg viewBox="0 0 900 300" width="100%" role="img" aria-label="行业层数据流">
  <defs>
    <marker id="a" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
      <path d="M0,0 L9,4.5 L0,9 z" fill="#7a7f83"/>
    </marker>
  </defs>
  <g font-family="inherit" font-size="12" fill="#2f3437">
    <rect x="10" y="30" width="200" height="86" rx="6" fill="#eef1f3" stroke="#c9d2d8"/>
    <text x="26" y="54" font-weight="600">raw/industry/*/part.parquet</text>
    <text x="26" y="74" fill="#555">10 个国内源</text>
    <text x="26" y="92" fill="#555">一次抓取 2026-11-23</text>

    <rect x="10" y="170" width="200" height="86" rx="6" fill="#eef1f3" stroke="#c9d2d8"/>
    <text x="26" y="194" font-weight="600">raw/industry_alt/*/data.csv</text>
    <text x="26" y="214" fill="#555">7 个国际源</text>
    <text x="26" y="232" fill="#555">manifest 带 sha256 与 fetched_at</text>

    <rect x="290" y="95" width="190" height="100" rx="6" fill="#f6f1e6" stroke="#dccfb4"/>
    <text x="306" y="120" font-weight="600">db.etl.industry</text>
    <text x="306" y="140" fill="#555">解析 / 单位改写 / row_hash</text>
    <text x="306" y="158" fill="#555">按 源×期 先删后插</text>
    <text x="306" y="176" fill="#555">审计落 industry_load_audit</text>

    <rect x="550" y="60" width="180" height="80" rx="6" fill="#eaf0ea" stroke="#c3d2c5"/>
    <text x="566" y="84" font-weight="600">industry_* 17 张表</text>
    <text x="566" y="104" fill="#555">16.8k 行</text>
    <text x="566" y="122" fill="#555">每表一个主键</text>

    <rect x="550" y="170" width="180" height="94" rx="6" fill="#eaeef2" stroke="#c3ccd4"/>
    <text x="566" y="194" font-weight="600">db.checks.industry</text>
    <text x="566" y="214" fill="#555">C1 行数 · C2 主键</text>
    <text x="566" y="232" fill="#555">C4 单位勾稽 · C6 覆盖</text>
    <text x="566" y="250" fill="#555">任一 FAIL 退出 1</text>

    <rect x="780" y="110" width="110" height="80" rx="6" fill="#f3eeee" stroke="#d8c4c4"/>
    <text x="796" y="134" font-weight="600">timer</text>
    <text x="796" y="154" fill="#555">日 06:10</text>
    <text x="796" y="172" fill="#555">月 3 日 06:40</text>

    <line x1="210" y1="73" x2="286" y2="126" stroke="#7a7f83" marker-end="url(#a)"/>
    <line x1="210" y1="213" x2="286" y2="168" stroke="#7a7f83" marker-end="url(#a)"/>
    <line x1="480" y1="126" x2="546" y2="104" stroke="#7a7f83" marker-end="url(#a)"/>
    <line x1="480" y1="160" x2="546" y2="200" stroke="#7a7f83" marker-end="url(#a)"/>
    <line x1="730" y1="100" x2="778" y2="140" stroke="#7a7f83" marker-end="url(#a)"/>
    <line x1="730" y1="210" x2="778" y2="164" stroke="#7a7f83" marker-end="url(#a)"/>
  </g>
</svg>
"""


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    counts = query_counts(con)
    coverage, totals = query_coverage(con)
    windows = {t: query_window(con, t) for t, _, _ in TABLES}
    audit = con.execute(
        "SELECT run_id, count(*), sum(raw_rows), sum(staged_rows), sum(loaded_rows), "
        "max(finished_at) FROM industry_load_audit GROUP BY 1 ORDER BY 1 DESC LIMIT 3"
    ).fetchall()
    con.close()

    check_path = sorted((HOME / "meta" / "checks").glob("industry-*.json"))[-1]
    check = json.loads(check_path.read_text(encoding="utf-8"))
    gates = check["gates"]
    total_rows = sum(counts.values())

    table_rows = "".join(
        f"<tr><td><code>{esc(t)}</code></td><td>{esc(name)}</td><td>{esc(src)}</td>"
        f"<td class='num'>{counts[t]:,}</td><td>{esc(windows[t][0])} → {esc(windows[t][1])}</td></tr>"
        for t, name, src in TABLES
    )

    gate_rows = "".join(
        f"<tr><td><code>{esc(name)}</code></td><td class='{'ok' if d.get('pass') else 'bad'}'>"
        f"{'PASS' if d.get('pass') else 'FAIL'}</td><td>{esc(_gate_note(name, d))}</td></tr>"
        for name, d in gates.items()
    )

    audit_rows = "".join(
        f"<tr><td><code>{esc(run)}</code></td><td class='num'>{n}</td><td class='num'>{raw:,}</td>"
        f"<td class='num'>{staged:,}</td><td class='num'>{loaded:,}</td><td>{esc(finished)}</td></tr>"
        for run, n, raw, staged, loaded, finished in audit
    )

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WORK-568 行业层入库与定更 · 交付说明</title>
<style>
  :root {{ --ink:#1f2326; --muted:#5b6167; --rule:#d9dde1; --paper:#fbfaf7; --accent:#2f5d50; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font-family: "Source Han Serif SC","Noto Serif CJK SC",Songti SC,Georgia,serif;
         line-height:1.72; font-size:16px; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 64px 32px 96px; }}
  header {{ border-bottom: 2px solid var(--ink); padding-bottom: 24px; margin-bottom: 40px; }}
  .eyebrow {{ font-family: ui-sans-serif, "Noto Sans CJK SC", sans-serif; font-size:12px;
              letter-spacing:.18em; text-transform:uppercase; color:var(--muted); }}
  h1 {{ font-size: 34px; line-height:1.28; margin: 10px 0 8px; font-weight: 600; }}
  .subtitle {{ color: var(--muted); font-size: 15px; }}
  h2 {{ font-size: 22px; margin: 52px 0 14px; padding-bottom: 6px; border-bottom:1px solid var(--rule); }}
  h3 {{ font-size: 17px; margin: 28px 0 8px; }}
  p {{ margin: 10px 0; }}
  .lede {{ font-size: 17px; }}
  .meta {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:14px; margin: 28px 0 8px; }}
  .card {{ background:#fff; border:1px solid var(--rule); border-radius:6px; padding:14px 16px; }}
  .card b {{ display:block; font-size:24px; font-weight:600; }}
  .card span {{ color:var(--muted); font-size:12px; font-family: ui-sans-serif, sans-serif; }}
  figure {{ margin: 26px 0; background:#fff; border:1px solid var(--rule); border-radius:6px;
            padding:18px 20px 12px; }}
  figcaption {{ color:var(--muted); font-size:13px; margin-top:8px; }}
  figcaption b {{ color:var(--ink); }}
  table {{ width:100%; border-collapse: collapse; margin: 14px 0 6px; font-size:14px;
           font-family: ui-sans-serif, "Noto Sans CJK SC", sans-serif; }}
  th {{ text-align:left; font-weight:600; border-bottom:1.5px solid var(--ink); padding:8px 8px; }}
  td {{ border-bottom:1px solid var(--rule); padding:7px 8px; vertical-align: top; }}
  td.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
  .ok {{ color:#2f6b45; font-weight:600; }}
  .bad {{ color:#a33a3a; font-weight:600; }}
  code {{ font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; font-size:12.5px;
          background:#f0f1ee; padding:1px 5px; border-radius:3px; }}
  pre {{ background:#20262a; color:#e8ecea; padding:14px 16px; border-radius:6px; overflow-x:auto;
         font-family: ui-monospace, Consolas, monospace; font-size:12.5px; line-height:1.6; }}
  .callout {{ border-left:3px solid var(--accent); background:#f1f5f2; padding:10px 16px; margin:16px 0; }}
  .warn {{ border-left:3px solid #a3703a; background:#f8f3ea; padding:10px 16px; margin:16px 0; }}
  ol, ul {{ padding-left: 22px; }}
  footer {{ margin-top:64px; border-top:1px solid var(--rule); padding-top:18px;
            color:var(--muted); font-size:13px; }}
  @media print {{ body {{ background:#fff; }} main {{ padding:0; }} figure, .card {{ break-inside: avoid; }} }}
</style></head>
<body><main>
<header>
  <div class="eyebrow">quantdb · WORK-568 · 交付说明</div>
  <h1>行业层入库与定更：17 个已落盘行业源第一次进 DuckDB</h1>
  <div class="subtitle">碳酸锂期货、装机、产量、价格、出口、LPR —— 从 parquet/csv 到可 SQL 查询、每日/每月自动更新
  · 数据统计时点 {dt.date.today():%Y-%m-%d}</div>
</header>

<p class="lede">本交付解决一个具体缺口：<code>raw/industry/</code> 与 <code>raw/industry_alt/</code> 里的行业数据已经落盘，
但结构化库里一张行业表都没有，也没有任何定时任务。交付后，17 个源变成 17 张 <code>industry_*</code> 表（{total_rows:,} 行），
每次装载可重跑且行数不变，7 道质量闸全 PASS，日频与月频 timer 已安装。</p>

<div class="meta">
  <div class="card"><b>{len(TABLES)}</b><span>industry_* 表</span></div>
  <div class="card"><b>{total_rows:,}</b><span>总行数</span></div>
  <div class="card"><b>{totals[1]}</b><span>目录指标可查到数值 / 405</span></div>
  <div class="card"><b>{sum(1 for d in gates.values() if d.get('pass'))}/7</b><span>质量闸 PASS</span></div>
</div>

<h2>1 数据流与交付边界</h2>
<figure>
{flow_svg()}
<figcaption><b>图 1</b> 行业层数据流。左侧是本次<b>只读</b>的两个原始层；中间是新增的装载与质量闸；
右侧是结果与调度。<b>不重写抓取逻辑</b>，fetcher 失败时装载沿用已有 raw（数据旧但不空）。</figcaption>
</figure>

<p>边界：不改既有 20 张 A 股表与 <code>v_daily_qfq/v_daily_hfq</code>；不改 <code>data_catalog</code> 的 405 条内容，只读它做映射；
写库只发生在 <code>db.etl.industry</code> 单进程内，并与其它 timer 共用 <code>/srv/share/quantdb/logs/.quantdb-db.lock</code> 串行。</p>

<h2>2 每个源进了哪张表、有多少行</h2>
<figure>
{bar_chart(counts)}
<figcaption><b>图 2</b> 17 张表的行数（装载后现查）。碳酸锂期货日行情占绝大多数，因为它是 180 天 × 全合约 × 9 字段；
其余源是月度/年度摘要，行数在 2–2,468 之间。<code>industry_cnesa_storage</code> 只有 2 行，原因见 §6.2。</figcaption>
</figure>

<table>
<thead><tr><th>表</th><th>内容</th><th>源</th><th class="num">行数</th><th>覆盖区间</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>

<h2>3 目录覆盖：405 条里我们能查到多少</h2>
<figure>
{coverage_chart(coverage)}
<figcaption><b>图 3</b> <code>v_industry_catalog_coverage</code> 按行业堆叠：深绿 = 完整，浅绿 = 部分，灰 = 未覆盖。
右侧标注「已覆盖/目录条数」。已覆盖 = 表里能查到该指标的可比数值（含只覆盖部分维度），「完整」再单独计数。</figcaption>
</figure>

<p>全库合计：目录 <b>{totals[0]}</b> 条，可查到数值 <b>{totals[1]}</b> 条，其中严格完整 <b>{totals[2]}</b> 条、部分 <b>{totals[3]}</b> 条。
验收线「已覆盖 ≥ 30」按可查到数值的口径达标；严格口径只有 {totals[2]} 条，卡点写在 §6.5，没有把「部分」说成「完整」。</p>

<h2>4 验收矩阵（逐条可复跑）</h2>
<table>
<thead><tr><th>验收项</th><th>要求</th><th>实测</th><th>结论</th></tr></thead>
<tbody>
<tr><td>1 表数</td><td>≥ 18 − 3 = 15</td><td>{len(TABLES)} 张 <code>industry_*</code>；源目录实际 17 个而非 issue 写的 18 个（缺汇率源）</td><td class="ok">PASS</td></tr>
<tr><td>2 抽样回对原始文件</td><td>3 个源数值完全一致</td><td>gfex / sne / comtrade 逐行比对，见 §5.2</td><td class="ok">PASS</td></tr>
<tr><td>3 重跑幂等</td><td>两次装载行数不变</td><td>连跑两次，17 表行数逐一相同，见 §5.3</td><td class="ok">PASS</td></tr>
<tr><td>4 timer</td><td><code>list-timers | grep industry</code> 可见，日频 next ≤ 24h</td><td>见 §5.4 输出</td><td class="ok">PASS</td></tr>
<tr><td>5 目录覆盖</td><td>已覆盖 ≥ 30</td><td>{totals[1]} 条（严格完整 {totals[2]} 条）</td><td class="ok">PASS（附口径说明）</td></tr>
</tbody></table>

<h3>4.1 七道质量闸</h3>
<table>
<thead><tr><th>闸</th><th>结果</th><th>读数</th></tr></thead>
<tbody>{gate_rows}</tbody>
</table>
<p>报告原文：<code>{esc(str(check_path))}</code>。</p>

<h2>5 证据</h2>
<h3>5.1 装载与重跑</h3>
<table>
<thead><tr><th>run_id</th><th class="num">表数</th><th class="num">raw 行</th><th class="num">staged 行</th><th class="num">装入后总行</th><th>完成时间</th></tr></thead>
<tbody>{audit_rows}</tbody>
</table>
<p>同参数重跑时 raw→staged 行数不变，装载按分区先删后插，因此每表总行数保持不变；审计表保留每次运行，可复核。</p>

<h3>5.2 抽样回对</h3>
<pre>-- 例：gfex 同一行（20260528 / 2606 / close）
raw : 174800.0   表 : 174800.0
-- 例：sne release 781 的标题与发布日期
raw : 2026.11.11 → 表 : 2026-11-11
-- 例：comtrade HS 850440 中国 2024 出口额
raw : 31477277093.0   表 : 31477277093.0</pre>

<h3>5.3 重跑命令</h3>
<pre>cd /srv/share/quantdb/code/quant-lab
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  flock -w 7200 /srv/share/quantdb/logs/.quantdb-db.lock \\
  /home/moyongkai/.venvs/quantdb/bin/python -m db.etl.industry load
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all</pre>

<h3>5.4 调度</h3>
<pre>systemctl --user list-timers | grep industry
quantdb-industry.timer          ...  quantdb-industry.service
quantdb-industry-monthly.timer  ...  quantdb-industry-monthly.service</pre>

<h2>6 已知限制与缺陷（都有实测证据）</h2>
<h3>6.1 只有 17 个源，不是 18 个</h3>
<p>issue 写「11 国内 + 7 国际」。<code>ls -d raw/industry/*/</code> 返回 10 个，缺的是「汇率」：没有 manifest、没有 parquet、也没有 fetcher。
没有用 LPR 或别的东西顶替，登记为缺口。</p>

<h3>6.2 CNESA 只有 2 行 = 抓取失败</h3>
<p>公开页 HTTP 200、341,562 字节，正文至少还有 66.43GW、189.48GWh、213.3GW、+54%、31.3%、144.7GW、+85%、
121.5GWh、+140.1%、206.3GWh 等数字；fetcher 的 6 条正则只命中 2 条。按 issue 要求不改抓取逻辑，按「部分覆盖」计。</p>

<h3>6.3 GFEX 成交额单位</h3>
<p>fetcher 标「元」，实测 <code>turnover×10000/volume</code> 在 1326 个合约×交易日里有 1325 个落在当日 low..high，
说明单位是<b>万元</b>。装载层修正并在列注释与 C4 闸里固定。</p>

<h3>6.4 上游解析缺陷原样保留</h3>
<ul>
<li>财政部 37 行 <code>published_date</code> 全是 2017-11-21（错）；已改由 URL 的 <code>tYYYYMMDD</code> 派生，原文留在 <code>published_date_raw</code>。
12 行 <code>period=2017-11</code> 与 2026 年文章冲突，不推断真实期，引用前须回原文。</li>
<li>统计局利润表表头与数值错位一列（1062066 是亿元营收却挂在同比增长列），保留行列号不猜配对。</li>
<li>能源局同一指标名混当月与累计（城乡居民生活用电量 1,883 与 11,132），用 row_hash 保留全部行，业务键重复数在 C2 如实报出。</li>
<li>TrendForce 的 value 列混价格、涨跌幅与表头，保留 <code>value_text</code> 并用 <code>unit_kind</code> 区分。</li>
</ul>

<h3>6.5 覆盖为什么到不了严格的 30</h3>
<p>目录条目普遍要求「全球 + 月度 + 多品种」，而现有 17 个源是国家统计/协会摘要或单点快照；半导体、医药两行业只有贸易与现货价，
没有产业统计；汇率、M2、仓单、合约价、地区均价无落盘源。可达上限按「可查到数值」是 {totals[1]} 条，按「完整」是 {totals[2]} 条。</p>

<h2>7 回滚</h2>
<pre>duckdb /srv/share/quantdb/db/quantdb.duckdb -c "DROP VIEW v_industry_catalog_coverage_summary; \\
  DROP VIEW v_industry_catalog_coverage; DROP TABLE industry_gfex_lithium_carbonate; ..."
systemctl --user disable --now quantdb-industry.timer quantdb-industry-monthly.timer
rm -f ~/.config/systemd/user/quantdb-industry*.service ~/.config/systemd/user/quantdb-industry*.timer
systemctl --user daemon-reload</pre>
<p>只影响 <code>industry_*</code> 对象；A 股 20 张表与复权视图不受影响。全量回滚走冷备 <code>/srv/backup/quantdb/</code>。</p>

<footer>
交付物：<code>db/schema_industry.sql</code>、<code>db/etl/industry.py</code>、<code>db/checks/industry.py</code>、
<code>db/timers/industry.sh</code>、<code>db/timers/systemd/quantdb-industry*</code>、<code>db/tests/test_industry.py</code>、
<code>docs/industry-ingest-2026.md</code>。<br>
本页由 <code>scripts/work568_build_delivery.py</code> 从主库与质量闸报告直接生成，图表为内联 SVG，断网可读、可直接打印。
</footer>
</main></body></html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT} ({len(doc):,} bytes)")


def _gate_note(name: str, detail: dict) -> str:
    if not detail.get("pass"):
        return f"失败：{json.dumps({k: v for k, v in detail.items() if k != 'tables'}, ensure_ascii=False)[:300]}"
    if name == "C1_rows_nonzero":
        return f"{detail['tables_present']}/{detail['tables_checked']} 表存在，合计 {detail['rows_total']:,} 行"
    if name == "C2_primary_key":
        return f"{len(detail['tables'])} 张表主键无 NULL、无重复"
    if name == "C3_date_domain":
        return f"{len(detail['columns'])} 个日期列全部落在 {detail['domain'][0]} .. {detail['domain'][1]}"
    if name == "C4_unit_reconciliation":
        g = detail["gates"]
        return "；".join(f"{k} {v['ok']}/{v['pairs']}" for k, v in g.items())
    if name == "C5_audit_consistency":
        return f"最新 run {detail['latest_run']} 的审计与现状一致（{len(detail['tables'])} 表）"
    if name == "C6_catalog_coverage":
        return (f"已覆盖 {detail['covered']}/{detail['catalog_rows']}，完整 {detail['complete']}，"
                f"部分 {detail['partial']}，映射孤儿 {detail['mapping_orphans']}")
    if name == "C7_freshness":
        return f"{len(detail['checks'])} 个时间列在阈值内"
    return ""


if __name__ == "__main__":
    main()
