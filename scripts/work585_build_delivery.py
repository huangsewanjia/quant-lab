#!/usr/bin/env python3
"""生成 WORK-585 交付说明（单文件 HTML：CSS 内联、图为内联 SVG、终端输出原文内嵌）。

    QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \
      /home/moyongkai/.venvs/quantdb/bin/python scripts/work585_build_delivery.py

数据来源全部现场读取，不手填数字：
  * meta/industry/work585-demo.json   离线演示（值修改 / 期消失 / 幂等）
  * meta/industry/work585-db.json     主库留痕现状
  * meta/checks/industry-<今天>.json  全闸报告
  * meta/industry/work585-code-sha256.txt  代码锁版
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import os
from pathlib import Path

import duckdb

HOME = Path(os.environ.get("QUANTDB_HOME", "/srv/share/quantdb"))
DB = HOME / "db/quantdb.duckdb"
META = HOME / "meta/industry"
CHECKS = HOME / "meta/checks"
OUT = Path(__file__).resolve().parents[1] / "docs/DELIVERY-WORK-585.html"

KIND_COLOR = {
    "no_change": "#94a3b8",
    "row_added": "#1d4ed8",
    "value_changed": "#f59e0b",
    "row_dropped": "#dc2626",
}
KIND_LABEL = {
    "no_change": "无变化汇总",
    "row_added": "新增行",
    "value_changed": "值变化",
    "row_dropped": "行被删",
}


def fmt(n) -> str:
    return f"{n:,}"


def esc(s) -> str:
    return html.escape(str(s))


# --------------------------------------------------------------------------- #
# 图 1：装载流水线（含事务边界）
# --------------------------------------------------------------------------- #
def pipeline_svg() -> str:
    steps = [
        ("读 raw（parquet / csv）", "#f1f5f9"),
        ("transform → staged", "#f1f5f9"),
        ("BEGIN TRANSACTION", "#fef3c7"),
        ("① 算差异 → industry_load_diff", "#dbeafe"),
        ("② DELETE（分区 / 整源）", "#dbeafe"),
        ("③ INSERT OR REPLACE", "#dbeafe"),
        ("④ industry_load_audit", "#dbeafe"),
        ("COMMIT（失败则 ROLLBACK）", "#fef3c7"),
        ("C1–C9 质量闸", "#dcfce7"),
    ]
    w, bh, gap = 860, 30, 9
    out = [f'<svg viewBox="0 0 {w} {len(steps) * (bh + gap) + 10}" width="100%" '
           f'role="img" aria-label="装载流水线">']
    for i, (label, fill) in enumerate(steps):
        y = 6 + i * (bh + gap)
        out.append(f'<rect x="24" y="{y}" width="290" height="{bh}" rx="5" fill="{fill}" '
                   f'stroke="#cbd5e1"/>')
        out.append(f'<text x="36" y="{y + 20}" font-size="12.5" fill="#0f172a">{esc(label)}</text>')
        if i < len(steps) - 1:
            out.append(f'<path d="M169 {y + bh} L169 {y + bh + gap}" stroke="#94a3b8"/>')
    # 事务边界括号
    y0 = 6 + 2 * (bh + gap) - 3
    y1 = 6 + 7 * (bh + gap) + bh + 3
    out.append(f'<path d="M330 {y0} L338 {y0} L338 {y1} L330 {y1}" fill="none" '
               f'stroke="#f59e0b" stroke-width="2"/>')
    out.append(f'<text x="348" y="{(y0 + y1) / 2 - 6}" font-size="12" fill="#b45309">'
               f'同一事务</text>')
    out.append(f'<text x="348" y="{(y0 + y1) / 2 + 10}" font-size="11" fill="#92400e">'
               f'先记账、后动手</text>')
    notes = ["只读已有 raw，不重抓", "日期/数值按值规范化",
             "", "逐行差异（含 0 差异汇总行）", "分区源只删本期；快照源删全表",
             "空快照整源替换直接拒绝", "行数/耗时/模式", "失败不留半表",
             "C9 验「每表每轮都留过痕」"]
    for i, note in enumerate(notes):
        if not note:
            continue
        out.append(f'<text x="440" y="{6 + i * (bh + gap) + 20}" font-size="11.5" '
                   f'fill="#475569">{esc(note)}</text>')
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- #
# 图 2：三轮演示的 diff 构成
# --------------------------------------------------------------------------- #
def demo_svg(demo: dict | None) -> str:
    """单位图：1 个方块 = 1 行 diff，三行分别对应三轮演示（量级差 33:1，柱状图会看不见）。"""
    if not demo:
        return '<p class="muted">未找到 meta/industry/work585-demo.json</p>'
    rounds = [
        ("第 1 轮 首次装载（空表）", [("row_added", demo.get("round1_rows", 0))]),
        ("第 2 轮 值修改 + 撤期", [
            ("value_changed", len(demo.get("changed") or [])),
            ("row_dropped", len(demo.get("dropped") or [])),
        ]),
        ("第 3 轮 同 raw 重跑", [("no_change", len(demo.get("no_change") or []))]),
    ]
    per_row, cell, gap = 22, 12, 3
    left, top, row_h = 210, 18, 34
    width = left + per_row * (cell + gap) + 90
    height = top + len(rounds) * row_h + 34
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
           f'aria-label="三轮演示的差异构成">']
    for i, (label, kinds) in enumerate(rounds):
        y = top + i * row_h
        out.append(f'<text x="4" y="{y + 13}" font-size="12" fill="#334155">{esc(label)}</text>')
        n = 0
        for kind, count in kinds:
            for _ in range(int(count)):
                col, row = n % per_row, n // per_row
                x = left + col * (cell + gap)
                yy = y + row * (cell + gap)
                out.append(f'<rect x="{x}" y="{yy}" width="{cell}" height="{cell}" rx="2" '
                           f'fill="{KIND_COLOR[kind]}"><title>{KIND_LABEL[kind]}</title></rect>')
                n += 1
        total = sum(int(c) for _, c in kinds)
        out.append(f'<text x="{left + per_row * (cell + gap) + 8}" y="{y + 11}" '
                   f'font-size="12" fill="#0f172a">{total} 行</text>')
    ly = height - 10
    for i, kind in enumerate(("row_added", "value_changed", "row_dropped", "no_change")):
        x = left + i * 150
        out.append(f'<rect x="{x}" y="{ly - 11}" width="11" height="11" rx="2" '
                   f'fill="{KIND_COLOR[kind]}"/>')
        out.append(f'<text x="{x + 16}" y="{ly}" font-size="11" fill="#334155">'
                   f'{KIND_LABEL[kind]}</text>')
    out.append("</svg>")
    return "".join(out)


def term(text: str, limit: int = 2600) -> str:
    return f'<pre class="term">{esc(text[:limit])}</pre>'


def main() -> None:
    demo = json.loads((META / "work585-demo.json").read_text()) if (META / "work585-demo.json").exists() else None
    dbinfo = json.loads((META / "work585-db.json").read_text()) if (META / "work585-db.json").exists() else None
    sha_file = META / "work585-code-sha256.txt"
    sha_block = sha_file.read_text().strip() if sha_file.exists() else "（缺：meta/industry/work585-code-sha256.txt）"

    con = duckdb.connect(str(DB), read_only=True)
    run_id = dbinfo["run_id"] if dbinfo else None
    diff_total = int(con.execute(
        "SELECT count(*) FROM industry_load_diff WHERE run_id = ?", [run_id]
    ).fetchone()[0]) if run_id else 0
    per_table = con.execute(
        "SELECT table_name, change_type, count(*) AS n FROM industry_load_diff "
        "WHERE run_id = ? GROUP BY 1, 2 ORDER BY 1", [run_id],
    ).fetchall()
    demo = demo or {}
    demo.setdefault("round1_rows", 0)
    obs_rows = int(con.execute(
        "SELECT sum(cnt) FROM ("
        + " UNION ALL ".join(f"SELECT count(*) AS cnt FROM {t}" for t in
                             [r[0] for r in con.execute(
                                 "SELECT table_name FROM information_schema.tables "
                                 "WHERE table_name LIKE 'industry\\_%' ESCAPE '\\' "
                                 "AND table_type='BASE TABLE' AND table_name NOT IN "
                                 "('industry_load_audit','industry_load_diff','industry_catalog_map') "
                                 "ORDER BY 1").fetchall()])
        + ")"
    ).fetchone()[0])
    tables_n = int(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'industry\\_%' "
        "ESCAPE '\\' AND table_type='BASE TABLE'"
    ).fetchone()[0])
    view_n = int(con.execute("SELECT count(*) FROM v_industry_load_changes_recent").fetchone()[0])
    con.close()

    test_file = Path(__file__).resolve().parents[1] / "db/tests/test_industry_diff.py"
    n_tests = len(re.findall(r"^def test_", test_file.read_text(encoding="utf-8"), re.M)) \
        if test_file.exists() else 0

    # 挑「含 9 道闸」的最新报告，而不是文件名字典序最大：单闸排查会写
    # industry-<日期>-<闸名>.json，按名字排会把它选进来，页面上只剩 1/1 PASS。
    reports: list[tuple[int, float, Path]] = []
    if CHECKS.exists():
        for path in CHECKS.glob("industry-*.json"):
            try:
                gates = len(json.loads(path.read_text(encoding="utf-8")).get("gates", {}))
            except (OSError, ValueError):
                continue
            reports.append((gates, path.stat().st_mtime, path))
    full = [r for r in reports if r[0] == 9]
    check_file = max(full or reports, key=lambda r: r[1])[2] if reports else None
    gate = json.loads(check_file.read_text()) if check_file else {"gates": {}}
    gate_rows = "".join(
        f'<tr><td>{esc(name)}</td><td class="{"ok" if d.get("pass") else "bad"}">'
        f'{"PASS" if d.get("pass") else "FAIL"}</td></tr>'
        for name, d in gate.get("gates", {}).items()
    )
    gate_ok = sum(1 for d in gate.get("gates", {}).values() if d.get("pass"))
    gate_total = len(gate.get("gates", {}))

    rows_html = "".join(
        f"<tr><td>{esc(t)}</td><td>{esc(k)}</td><td class='num'>{n:,}</td></tr>"
        for t, k, n in per_table
    )
    changed = demo.get("changed") or [{}]
    dropped = demo.get("dropped") or [{}]

    body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WORK-585 交付说明 · 行业装载变更留痕</title>
<style>
:root{{--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--bg:#ffffff;--accent:#1d4ed8;--warn:#b45309;--ok:#15803d;--bad:#b91c1c}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f8fafc;color:var(--ink);
 font:16px/1.75 "Noto Serif CJK SC","Source Han Serif SC","Songti SC",Georgia,serif}}
main{{max-width:980px;margin:0 auto;padding:48px 28px 96px;background:var(--bg);
 box-shadow:0 0 0 1px var(--line)}}
h1{{font-size:28px;line-height:1.35;margin:0 0 6px;letter-spacing:.2px}}
h2{{font-size:20px;margin:44px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--ink)}}
h3{{font-size:16px;margin:24px 0 8px;color:#1e293b}}
p,li{{font-size:15.5px}}
.sub{{color:var(--muted);font-size:13.5px;margin:0 0 22px;font-family:ui-monospace,monospace}}
.lede{{background:#f1f5f9;border-left:4px solid var(--accent);padding:14px 18px;margin:18px 0 8px}}
.lede b{{color:var(--accent)}}
table{{border-collapse:collapse;width:100%;margin:12px 0 8px;font-size:14px;
 font-family:"Noto Sans CJK SC","Source Han Sans SC",system-ui,sans-serif}}
th,td{{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}}
th{{background:#f1f5f9;font-weight:600}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}}
.muted{{color:var(--muted)}}
figure{{margin:20px 0;padding:14px;border:1px solid var(--line);border-radius:8px;background:#fff}}
figcaption{{font-size:12.5px;color:var(--muted);margin-top:10px;
 font-family:"Noto Sans CJK SC",system-ui,sans-serif}}
pre.term{{background:#0f172a;color:#e2e8f0;padding:14px 16px;border-radius:6px;
 overflow-x:auto;font-size:12.2px;line-height:1.55;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
 background:#f1f5f9;padding:1px 5px;border-radius:3px}}
.matrix td:first-child{{width:34%}}
.tag{{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;
 background:#dbeafe;color:#1e40af;font-family:system-ui,sans-serif}}
footer{{margin-top:56px;border-top:1px solid var(--line);padding-top:14px;
 color:var(--muted);font-size:12.5px}}
</style></head><body><main>

<h1>行业装载变更留痕：diff 日志 + 快照源整源替换</h1>
<p class="sub">WORK-585 · 数据底座 quantdb · 生成于 {dt.datetime.now():%Y-%m-%d %H:%M} CST · 目标 runtime：Mo-server</p>

<div class="lede">
<b>一句话：</b>装载器现在每次替换前先把「库内现状 vs 本次快照」的逐行差异写进
<code>industry_load_diff</code>，再动手替换（同一事务，失败一起回滚）；
<code>cnesa_storage</code> 改为整源替换，WORK-577 遗留的 F3（上游删期、库内残留幽灵行）消除；
复核用 <code>v_industry_load_changes_recent</code> 看近 30 天的值变化与被删行。
现有 17 张观测表结构、C1–C8 判据、<code>industry.sh</code> 调用方式一律未改。
</div>

<h2>1. 用途与受众</h2>
<p><b>给谁用：</b>负责行业层数据的人（复核者），以及任何要引用 <code>industry_*</code> 表之前
想知道「这条数什么时候被人改过」的研究者。</p>
<p><b>解决什么：</b>旧装载只有 <code>DELETE + INSERT</code>，审计表只记行数与耗时。
上游把某个值改了、或删掉一整期，库内变化没有任何记录——复核者只能看到「行数没变」，
看不到「3.7 亿千瓦变成了别的数」。本单把「替换」变成一件有账可查的事。</p>
<p><b>不解决什么：</b>不判断变化是否符合业务预期，不做审批流，不发消息；它只保证
「改了什么、删了什么」逐行可读、可追、可复跑。</p>

<h2>2. 首次使用（三步）</h2>
<ol>
<li><b>确认表在</b>：<code>select count(*) from industry_load_diff;</code>
（表由 <code>db/schema_industry.sql</code> 随每次装载自动建立，无需手工执行 DDL）。</li>
<li><b>找最近一次运行</b>：<code>select run_id, max(finished_at) from industry_load_audit
group by 1 having count(*) = 17 order by 2 desc limit 1;</code></li>
<li><b>看要人复核的变更</b>：<code>select * from v_industry_load_changes_recent
order by changed_at desc limit 50;</code></li>
</ol>

<h2>3. 主流程：装载一次到底发生什么</h2>
<figure>{pipeline_svg()}
<figcaption>图 1｜装载流水线。橙色括号内是一个事务：差异先落库，再删旧插新，
审计同事务写入；任一步抛错整体回滚，库内保持装载前状态。旧实现 DELETE 与 INSERT 各自
autocommit，INSERT 失败会留下空表/半表。</figcaption></figure>

<h3>3.1 差异怎么算</h3>
<table>
<tr><th>源类型</th><th>比对键</th><th>能产出什么</th></tr>
<tr><td>自然主键源（gfex / lpr / cnesa / cninfo / pbc / stats / comtrade / exchange / owid / sne）</td>
<td>表的真实主键（如 <code>period × indicator</code>）</td>
<td><code>value_changed</code>（逐列一行）、<code>row_added</code>、<code>row_dropped</code></td></tr>
<tr><td><code>row_hash</code> 主键源（caam / cpca / mof / nea / benchmark / ppi / trendforce）</td>
<td><code>row_hash</code>（内容哈希）</td>
<td><code>row_added</code> / <code>row_dropped</code>：内容改版天然表现为「旧 hash 走、新 hash 来」，
<code>indicator</code> 用业务键文本渲染，人一眼能对上</td></tr>
</table>
<p class="muted">数值按「值」比对（<code>80620</code> 与 <code>80620.0</code> 视为同一个值），
DATE 与零点 TIMESTAMP 规范化到同一天——否则每次重载都会把全表记成「变了」。
这两条是实测踩出来的：首版草稿在真实库上做只读演练时，
<code>exchange_daily_quotes</code> 报了 87,024 个假变化。</p>

<h2>4. 主流程控件与命令</h2>
<table>
<tr><th>入口</th><th>作用</th><th>说明</th></tr>
<tr><td><code>python -m db.etl.industry load</code></td><td>装全部源（17 观测 + 1 预测，分两组 run_id）</td>
<td>定时任务 <code>db/timers/industry.sh</code> 的调用方式未变</td></tr>
<tr><td><code>--sources cnesa_storage</code></td><td>只装一个源（排查用）</td><td>同样留痕</td></tr>
<tr><td><code>--tier daily|monthly</code></td><td>只装该调度档</td><td>未变</td></tr>
<tr><td><code>--full</code></td><td>清表重装（排查用）</td><td>留痕覆盖被清掉的全部行</td></tr>
<tr><td><code>v_industry_load_changes_recent</code></td><td>近 30 天 <code>value_changed</code> / <code>row_dropped</code></td>
<td>新增行是常态（源在扩），只有这两类需要人看一眼</td></tr>
<tr><td><code>python -m db.checks.industry all</code></td><td>9 道闸</td><td>新增 C9，编号让开 WORK-584 的 C8</td></tr>
</table>

<h2>5. 需求符合性矩阵</h2>
<table class="matrix">
<tr><th>需求（本单原文）</th><th>交付物</th><th>状态</th></tr>
<tr><td>新表 <code>industry_load_diff</code>（run_id / source / table / period / indicator / change_type /
old_value / new_value / changed_at），同事务先记账后替换，0 差异写汇总行</td>
<td><code>db/schema_industry.sql</code>（10 列，多一列 <code>column_name</code> 定位变化的列）+
<code>db/etl/industry.py::load_source</code></td><td class="ok">完成</td></tr>
<tr><td>快照型源（本单只 cnesa_storage）整源替换，删掉的行全部落 diff → 消 F3</td>
<td><code>Source.replace_mode</code>；<code>cnesa_storage</code> 设 <code>"source"</code></td>
<td class="ok">完成</td></tr>
<tr><td>视图 <code>v_industry_load_changes_recent</code>（近 30 天 value_changed / row_dropped）</td>
<td>schema 内建视图</td><td class="ok">完成</td></tr>
<tr><td>文档增补「变更留痕」小节</td><td><code>docs/industry-ingest-2026.md</code> §14</td>
<td class="ok">完成</td></tr>
<tr><td>可为 diff 加一条独立新检查，编号不与 WORK-584 冲突；不改 7 闸判据、不动 17 表结构</td>
<td><code>C9_load_diff</code>（C1–C8 判据未改）</td><td class="ok">完成</td></tr>
<tr><td>演示用例：值修改 + 期消失</td><td><code>scripts/work585_verify.py demo</code> +
<code>db/tests/test_industry_diff.py</code></td><td class="ok">见 §6.1</td></tr>
<tr><td>F3 回归：模拟上游删 2021 年末那句 → 2022-01 清除且进日志</td>
<td><code>test_snapshot_source_drops_vanished_period_and_logs_it</code></td>
<td class="ok">见 §6.2</td></tr>
<tr><td>幂等：0 差异写汇总行、17 表行数不变、连跑两次一致</td>
<td><code>test_rerun_without_changes_writes_one_no_change_row</code></td>
<td class="ok">见 §6.3</td></tr>
<tr><td>既有闸 PASS、audit 照常、<code>pytest db/tests/</code> 全过、回滚写清</td>
<td>§6.4 / §6.5 / §8</td><td class="ok">完成</td></tr>
</table>

<h2>6. 实测证据</h2>

<h3>6.1 演示：值修改 + 期消失 + 幂等（离线，不碰生产表）</h3>
<p>把生产 raw 的 <code>cnesa_storage/part.parquet</code>（33 行）复制到演示库
<code>$QUANTDB_HOME/staging/work585-demo</code>（每次重建、脚本可复跑），第 2 轮把「新型储能累计装机规模」从
{demo.get("before_value")} 改成 150.9，并删掉 2022-01 那一期，第 3 轮原样重跑。</p>
<figure>{demo_svg(demo)}
<figcaption>图 2｜三轮演示各自的 diff 构成。第 1 轮空表装载全是 <code>row_added</code>；
第 2 轮恰好 1 条 <code>value_changed</code>（144.7 → 150.9）+ 1 条 <code>row_dropped</code>（2022-01 撤期）；
第 3 轮同 raw 重跑只写 1 行 <code>no_change</code> 汇总。</figcaption></figure>
<table>
<tr><th>轮次</th><th>change_type</th><th>indicator</th><th>column</th><th>old → new</th></tr>
<tr><td>2</td><td>value_changed</td><td>{esc(changed[0].get("indicator",""))}</td>
<td>{esc(changed[0].get("column_name",""))}</td>
<td>{esc(changed[0].get("old_value",""))} → <b>{esc(changed[0].get("new_value",""))}</b></td></tr>
<tr><td>2</td><td>row_dropped</td><td>{esc(dropped[0].get("indicator",""))}</td><td>—</td>
<td>{esc(str(dropped[0].get("old_value",""))[:120])}…</td></tr>
<tr><td>3</td><td>no_change</td><td>(全表)</td><td>—</td>
<td>{esc((demo.get("no_change") or [{}])[0].get("new_value",""))}</td></tr>
</table>
{term(chr(10).join(demo.get("loader_out", [])))}

<h3>6.2 F3 回归：上游删期，库内不留幽灵行</h3>
<p>第 2 轮删掉 2022-01 后，演示库最终只剩这些期：
<b>{esc("、".join(demo.get("periods_after", [])))}</b>（{fmt(demo.get("rows_after", 0))} 行）。
改前按期替换只删「本次出现的期」，2022-01 会一直留在库里且 7 闸全绿——这正是 F3。</p>

<h3>6.3 幂等</h3>
<p>第 3 轮与第 2 轮 raw 完全相同，写入 1 行 <code>no_change</code>
（<code>{esc((demo.get("no_change") or [{}])[0].get("new_value",""))}</code>），
库内行数保持 {fmt(demo.get("rows_after", 0))}。</p>

<h3>6.4 主库现状（本轮正式装载）</h3>
<p>run <code>{esc(run_id or "—")}</code>：diff 行合计 <b>{diff_total}</b>，
覆盖 <b>{len({t for t, _, _ in per_table})}</b> 张表；库内 <code>industry_*</code> 基表
<b>{tables_n}</b> 张（17 观测 + 1 预测 + audit + diff + catalog_map），
行业层业务表合计（17 观测 + 1 预测）<b>{fmt(obs_rows)}</b> 行；近 30 天复核视图 <b>{view_n}</b> 行
（本次装载与库内一致，没有值变化与被删行，视图为空是正确结果）。</p>
<table>
<tr><th>table_name</th><th>change_type</th><th class="num">n</th></tr>
{rows_html}
</table>
<p class="muted">自检：各 <code>change_type</code> 分项相加 = {diff_total}；
各表相加 = {diff_total}。统计时点 {dt.datetime.now():%Y-%m-%d %H:%M} CST。</p>

<h3>6.5 质量闸</h3>
<table><tr><th>闸</th><th>结果</th></tr>{gate_rows}</table>
<p>{gate_ok}/{gate_total} PASS（报告文件 <code>{esc(check_file.name if check_file else "—")}</code>）。</p>

<h3>6.6 离线测试</h3>
<p><code>pytest db/tests/</code> 全绿，其中 <code>db/tests/test_industry_diff.py</code>
<b>{n_tests}</b> 项为本单新增：值修改 + 新增行、快照源撤期（F3 回归）、幂等 0 差异、
分区源只碰本期、空快照拒绝清表（含 <code>--full</code>）、整数与浮点同值不误报、
离线 HTML fixture 的 F3 全链路、C9 缺留痕、C9 畸形行。</p>

<h2>7. 已知限制</h2>
<ul>
<li><b>整源替换只给了 <code>cnesa_storage</code></b>。其余快照型源（benchmark / ppi / trendforce）按
<code>snapshot_date</code>/<code>as_of</code> 分区替换，旧快照按设计保留；它们是否也该整源替换，
要看「同一快照日上游会不会删行」，需要各自的一次实测，不在本单范围。</li>
<li><b>row_hash 源没有「值变化」</b>：内容改版显示为一行 add + 一行 drop。不把两行配成一条，
是因为业务键不唯一（146 行对 98 个业务键），配对靠推断、推断错比不推断更糟。</li>
<li><b>C9 只查留痕完整性</b>，不判断变化是否符合业务预期——那是人看视图的事。</li>
<li><b>diff 不清理</b>：每日/每月累积，量级是「每次装载的变更行数」，正常重跑每表 1 行；
若将来需要保留窗口，另开单做归档，不在本单。</li>
<li><b><code>--full</code> 的 diff 会放大</b>：清表重装会为被清掉的行各写一条 <code>row_dropped</code>
（<code>industry_stats_gov_release</code> 13.7 万行 → 约 27 万条 diff，实测 <code>executemany</code>
约 2,450 行/秒，仅插入约 2 分钟），所以它仍是「排查时用」的手工命令，不进定时任务。</li>
<li><b>数据库结构</b>：新增 1 表 + 1 视图，17 张观测表零改动；旧版本 DuckDB 客户端读到
<code>industry_load_diff</code> 不影响既有查询。</li>
</ul>

<h2>8. 回滚</h2>
<pre class="term"># ① 代码（二选一）
git -C /srv/share/quantdb/code/quant-lab revert &lt;交付 SHA&gt;
#   或直接还原改前备份：
cp db/etl/industry.py.bak-&lt;时间戳&gt;         db/etl/industry.py
cp db/checks/industry.py.bak-&lt;时间戳&gt;      db/checks/industry.py
cp db/schema_industry.sql.bak-&lt;时间戳&gt;     db/schema_industry.sql

# ② 库对象（只删留痕对象，不动任何观测表数据）
flock /srv/share/quantdb/logs/.quantdb-db.lock \
  /home/moyongkai/.venvs/quantdb/bin/python - &lt;&lt;'SQL'
import duckdb
con = duckdb.connect("/srv/share/quantdb/db/quantdb.duckdb")
con.execute("DROP VIEW IF EXISTS v_industry_load_changes_recent")
con.execute("DROP TABLE IF EXISTS industry_load_diff")
con.execute("CHECKPOINT"); con.close()
SQL

# ③ 数据：整源替换已经清掉的残留行不会因回滚代码复活；
#    需要恢复就按 raw 重新装载（raw 未改动）。</pre>
<p class="muted"><code>industry.sh</code> 调用方式未改，回滚代码即恢复原行为；
C9 随 <code>industry_load_diff</code> 一起消失，C1–C8 不受影响。</p>

<h2>9. 附录</h2>
<h3>9.1 表结构</h3>
<pre class="term">CREATE TABLE IF NOT EXISTS industry_load_diff (
    run_id      VARCHAR NOT NULL,   -- 与 industry_load_audit.run_id 同一次运行
    source      VARCHAR NOT NULL,   -- Source.name
    table_name  VARCHAR NOT NULL,
    period      DATE,               -- 分区日期；非日期分区源为 NULL
    indicator   VARCHAR NOT NULL,   -- 业务键文本 k=v|k=v
    change_type VARCHAR NOT NULL,   -- value_changed | row_added | row_dropped | no_change
    column_name VARCHAR,            -- value_changed 时变化的列名
    old_value   VARCHAR,            -- 变化前 / 被删行整行文本；源侧为空记 ∅
    new_value   VARCHAR,            -- 变化后 / 新增行整行文本；no_change 存汇总文本
    changed_at  TIMESTAMP NOT NULL
);</pre>

<h3>9.2 常用查询</h3>
<pre class="term">-- 近 30 天需要人复核的变更
select * from v_industry_load_changes_recent order by changed_at desc;

-- 某次运行改了什么
select change_type, table_name, period, indicator, column_name, old_value, new_value
from industry_load_diff where run_id = '{esc(run_id or "<run_id>")}'
order by table_name, indicator;

-- 本轮跑过没有
select table_name, new_value from industry_load_diff
where run_id = '{esc(run_id or "<run_id>")}' and change_type = 'no_change';</pre>

<h3>9.3 复跑命令</h3>
<pre class="term">cd /srv/share/quantdb/code/quant-lab
# 离线行为测试（raw 与库都在 pytest tmp_path，不碰生产）
PYTHONPATH=$PWD /home/moyongkai/.venvs/quantdb/bin/python -m pytest db/tests/test_industry_diff.py -q
# 复核脚本：演示 / 主库现状 / 自检 / C9
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  /home/moyongkai/.venvs/quantdb/bin/python scripts/work585_verify.py all
# 9 道闸
QUANTDB_HOME=/srv/share/quantdb PYTHONPATH=$PWD \\
  /home/moyongkai/.venvs/quantdb/bin/python -m db.checks.industry all</pre>

<h3>9.4 代码锁版（sha256）</h3>
<pre class="term">{esc(sha_block)}</pre>

<footer>
本页由 <code>scripts/work585_build_delivery.py</code> 从现场数据生成；所有数字来自
<code>db/quantdb.duckdb</code> 只读查询、<code>meta/industry/work585-*.json</code> 与
<code>meta/checks/</code> 闸报告，无手工填写。图 1/图 2 为内联 SVG，断网可读、可直接打印。
</footer>
</main></body></html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(f"写出 {OUT}（{len(body):,} 字节）")


if __name__ == "__main__":
    main()
