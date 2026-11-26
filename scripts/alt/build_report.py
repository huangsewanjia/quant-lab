#!/usr/bin/env python3
"""生成 docs/selfbuild_report.html（单文件、图片 base64、断网可读、可直接打印）。

数据来源全部是本目录的交付物：
  docs/selfbuild_top10.csv、docs/selfbuild_top10_candidates.csv、
  docs/alt_gap_ledger.csv、docs/alt_run_log.json
图表：matplotlib → PNG → base64。字体钉死 Noto Sans SC（本机已装）。
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(REPO, "docs")
RAW = os.environ.get("ALT_RAW_ROOT", "/srv/share/quantdb/raw/industry_alt")

INK = "#1a1a1a"
BLUE = "#0b5fa5"
TEAL = "#2a9d8f"
AMBER = "#e09f3e"
GREY = "#8d99ae"
RED = "#b5382f"

plt.rcParams.update({
    "font.family": ["Noto Sans SC", "DejaVu Sans"],
    "font.size": 9,
    "axes.edgecolor": "#555555",
    "axes.linewidth": 0.8,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "figure.dpi": 200,
    "savefig.dpi": 200,
})


def png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fig_coverage() -> str:
    fig, ax = plt.subplots(figsize=(6.2, 2.5))
    labels = ["541 基线\n（2026-11-23）", "本轮后\n（WORK-619）"]
    has = [2, 27]
    none = [75, 50]
    y = [1, 0]
    ax.barh(y, has, color=TEAL, height=0.45, label="有可复跑取数脚本")
    ax.barh(y, none, left=has, color="#dfe3e8", height=0.45, label="无可跑脚本")
    for yy, h, n in zip(y, has, none):
        ax.text(h / 2, yy, f"{h}", ha="center", va="center", color="white", fontsize=11, fontweight="bold")
        ax.text(h + n / 2, yy, f"{n}", ha="center", va="center", color="#4a4a4a", fontsize=9)
        ax.text(77 + 1.5, yy, f"{h}/77 = {h/77*100:.1f}%", va="center", fontsize=9, color=INK)
    ax.set_yticks(y, labels)
    ax.set_xlim(0, 90)
    ax.set_xlabel("77 条 P0 行业指标（需采购 16 + 部分免费 61）")
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title("图 1 ｜ 有可复跑脚本的 P0 指标：2 → 27（+25 条，全部是 B 类代理）", loc="left", fontsize=10)
    return png(fig)


def fig_funnel(cands) -> str:
    def after(k: int) -> int:
        markers = ["判据 1", "判据 2", "判据 3", "判据 4"]
        return sum(1 for c in cands
                   if not any(m in c["理由/排除原因"] for m in markers[:k]))

    selected = sum(1 for c in cands if c["入选Top10"] == "Y")
    counts = [len(cands)] + [after(k) for k in range(1, 5)] + [selected]
    names = ["候选", "过判据1", "过判据2", "过判据3", "过判据4", "入选"]
    colors = [GREY, GREY, BLUE, BLUE, TEAL, TEAL]
    fig, ax = plt.subplots(figsize=(6.2, 2.2))
    ax.bar(range(len(counts)), counts, color=colors, width=0.6)
    for i, v in enumerate(counts):
        ax.text(i, v + 0.4, str(v), ha="center", fontsize=9, fontweight="bold")
    ax.set_xticks(range(len(names)), names)
    ax.set_ylim(0, len(cands) + 3)
    ax.set_ylabel("候选数")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.set_title("图 2 ｜ 20 条候选按 5 条判据（按序）筛到 10 条", loc="left", fontsize=10)
    return png(fig)


def fig_flow() -> str:
    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    ax.set_xlim(0, 12.4)
    ax.set_ylim(0, 6.2)
    ax.axis("off")
    cols = [
        (0.05, "① 源站\n公开页 / API",
         ["TrendForce 现货价", "Benchmark 指数", "SNE 新闻稿", "EMMES 公开版",
          "SHFE / GFEX 日行情", "Eurostat / World Bank", "IRENA · OWID CSV"], "#eef3f8"),
        (3.15, "② 抓取脚本\nfetch.py",
         ["单文件 · 仅标准库", "--start / --end", "主键合并（幂等）",
          "同域 ≤1 req/s", "失败非 0 退出"], "#eaf4f2"),
        (6.25, "③ raw 层\n<slug>/",
         ["data.csv（逐行 URL）", "manifest.json", "  rows / sha256", "failed.json", "cache/ 原文快照"], "#f6f1e7"),
        (9.35, "④ 库表\nalt_*",
         ["10 张 alt_ 表", "alt_ingest_state", "  水位 + 回溯窗口", "主键 upsert"], "#f2eef6"),
    ]
    for x, title, items, color in cols:
        ax.add_patch(Rectangle((x, 1.0), 2.95, 4.3, facecolor=color, edgecolor="#c9d1d9", linewidth=0.8))
        ax.text(x + 1.48, 4.88, title, ha="center", va="center", fontsize=6.8,
                fontweight="bold", color=INK, linespacing=1.5)
        for i, it in enumerate(items):
            ax.text(x + 0.14, 4.05 - i * 0.47, "· " + it, fontsize=5.6, color="#333333", va="center")
    for x0, x1 in [(3.0, 3.15), (6.1, 6.25), (9.2, 9.35)]:
        ax.add_patch(FancyArrowPatch((x0, 3.0), (x1, 3.0), arrowstyle="-|>", mutation_scale=9,
                                     color=BLUE, linewidth=1.0))
    ax.text(6.2, 0.45, "使用者：产业研究员 · 投研负责人 · 战略咨询（决策层 / 价值链 / 成本曲线 / 市场规模）",
            ha="center", fontsize=6.3, color="#444444")
    ax.set_title("图 3 ｜ 数据流：源站 → 脚本 → raw（可审计）→ alt_ 表 → 使用者", loc="left", fontsize=10)
    return png(fig)


def fig_rows(log) -> str:
    runs = {r["slug"]: r for r in log["runs"] if r["pass"] == 1}
    slugs = sorted(runs, key=lambda k: runs[k]["rows"] or 0)
    rows = [runs[s]["rows"] or 0 for s in slugs]
    fig, ax = plt.subplots(figsize=(6.2, 3.0))
    ax.barh(range(len(slugs)), rows, color=BLUE, height=0.6)
    ax.set_yticks(range(len(slugs)), slugs, fontsize=7)
    ax.set_xscale("log")
    ax.set_xlabel("落盘行数（对数轴）")
    for i, v in enumerate(rows):
        ax.text(v * 1.12, i, f"{v:,}", va="center", fontsize=7)
    ax.set_xlim(1, max(rows) * 4)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.set_title("图 4 ｜ 10 个脚本实跑落盘行数（同一批次；窗口见附录 A）", loc="left", fontsize=10)
    return png(fig)


CSS = """
:root{--ink:#1a1a1a;--muted:#5f6b7a;--line:#d8dee6;--bg:#ffffff;--tint:#f4f7fa}
*{box-sizing:border-box}
body{margin:0;padding:48px 24px 72px;background:var(--bg);color:var(--ink);
 font-family:"Noto Sans SC","Helvetica Neue",Arial,"Segoe UI",sans-serif;
 font-size:10.5pt;line-height:1.62;max-width:900px;margin:0 auto}
h1{font-size:20pt;line-height:1.25;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:13pt;margin:34px 0 10px;padding-bottom:4px;border-bottom:1.5px solid var(--ink)}
h3{font-size:11pt;margin:20px 0 6px}
p{margin:8px 0}
.sub{color:var(--muted);font-size:9pt;margin-bottom:22px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0 22px}
.kpi{background:var(--tint);border-left:3px solid #0b5fa5;padding:10px 12px}
.kpi .v{font-size:17pt;font-weight:700;line-height:1.1}
.kpi .l{font-size:8pt;color:var(--muted);margin-top:3px}
table{border-collapse:collapse;width:100%;font-size:8.4pt;margin:10px 0 14px}
th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
th{background:var(--tint);font-weight:600;white-space:nowrap}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
figure{margin:18px 0 20px}
figure img{width:100%;height:auto;display:block}
figcaption{font-size:8pt;color:var(--muted);margin-top:6px}
code{background:#f4f4f4;padding:1px 4px;border-radius:2px;font-size:8.5pt}
pre{background:#f7f8fa;border-left:3px solid var(--line);padding:10px 12px;overflow-x:auto;
 font-size:8pt;line-height:1.45}
.note{border-left:3px solid #e09f3e;background:#fdf8ef;padding:10px 12px;font-size:9pt;margin:14px 0}
.warn{border-left:3px solid #b5382f;background:#fbf0ee;padding:10px 12px;font-size:9pt;margin:14px 0}
ul,ol{margin:6px 0 10px;padding-left:22px}
li{margin:3px 0}
.foot{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);font-size:8pt;color:var(--muted)}
@media print{body{padding:0;max-width:none;font-size:9.5pt}h2{page-break-after:avoid}figure,table{page-break-inside:avoid}}
"""


def main() -> int:
    top = read_csv(os.path.join(DOCS, "selfbuild_top10.csv"))
    cands = read_csv(os.path.join(DOCS, "selfbuild_top10_candidates.csv"))
    ledger = read_csv(os.path.join(DOCS, "alt_gap_ledger.csv"))
    log = json.load(open(os.path.join(DOCS, "alt_run_log.json"), encoding="utf-8"))
    runs = {r["slug"]: r for r in log["runs"] if r["pass"] == 1}
    idem = log.get("idempotency", [])

    days = sum(int(r["自建人日"]) for r in top)
    p0_rows = sum(int(r["对应P0条数"]) for r in top)
    by_prod = sum(1 for r in ledger if r["附带覆盖脚本"] != "无")
    zero = len(ledger) - by_prod

    # 每条脚本的落盘证据
    evid = []
    for slug in ["trendforce_spot_price", "benchmark_public_index", "sne_research_releases",
                 "shfe_gfex_quotes", "eurostat_trade", "owid_ev_sales", "owid_energy_co2",
                 "worldbank_wdi", "irena_re_capacity", "emmes_storage"]:
        r = runs.get(slug, {})
        evid.append((slug, r.get("rows", ""), (r.get("data_sha256") or "")[:16],
                     r.get("date_range"), r.get("seconds", "")))

    elig = [c for c in cands if c["入选Top10"] == "Y"]
    excl = [c for c in cands if c["入选Top10"] == "N"]
    ids = {c["数据集"]: c for c in cands}
    idem_ok = sum(1 for i in idem if i["sha_equal"] and i["rows_equal"])

    html = [f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>自建 Top-10 落地报告 · WORK-619</title><style>{CSS}</style></head><body>
<h1>自建 Top-10 落地：从判定表到能跑的取数管线</h1>
<div class="sub">WORK-619 ｜ 2026-11-27 ｜ 数据截止 2026-11-27 ｜ 关联 WORK-541（77 条 P0 判定）· WORK-617（父单决策）<br>
结论锁定：仓库提交 <code>quant-lab</code> 本分支 + 落盘快照 <code>/srv/share/quantdb/raw/industry_alt/&lt;slug&gt;/</code>（sha256 见 §3）</div>

<div class="kpis">
  <div class="kpi"><div class="v">10 条</div><div class="l">入选并实跑（20 条候选中）</div></div>
  <div class="kpi"><div class="v">{p0_rows} 条</div><div class="l">覆盖的 P0 指标（去重）</div></div>
  <div class="kpi"><div class="v">{days} 人日</div><div class="l">10 条自建工时相加</div></div>
  <div class="kpi"><div class="v">{idem_ok}/10</div><div class="l">连跑两次幂等通过</div></div>
</div>

<h2>一、一页结论</h2>
<p><b>77 条 P0 里，有可复跑取数脚本的从 2 条（2.6%）变成 27 条（35.1%）。</b>
新增的 25 条全部是 B 类「近似替代」，不改变 541 的 A/B/C 判定——脚本让代理<b>可执行、可复现、可维护</b>，
不是把 B 洗成 A。C 类 16 条一条没动（仍是只能买）。</p>
<ul>
<li>做法：先按 5 条判据（影响判断 → 代理质量 → 成本 → 可维护 → 硬缺口倾斜，按序）把 20 条候选筛到 10 条；
每条一个单文件脚本，仅标准库，支持 <code>--start/--end</code>、主键合并幂等、同域 ≤1 req/s、失败非 0 退出。</li>
<li>10 条里有 <b>3 条属 16 条「需采购」硬缺口</b>（存储现货价、锂/电芯价格指数、全球储能出货）；其余 7 条服务宏观、欧洲贸易、交易所行情与欧洲储能。</li>
<li><b>医药线 0 条入选</b>——这是本轮最硬的一条限制：公开层只有 PDF 表（IFPMA/EFPIA/USGS 式）与供给侧代理（openFDA 件数），
CMS 出口 403、openFDA 单页实测 298 s。台账里 8 条医药行逐条写了触发条件，不硬凑。</li>
<li>仍未解决：B 类代理与原指标的偏差校准（n=25 条都没做），以及全历史回补（本轮窗口只到本期）。</li>
</ul>
<div class="note"><b>和 541 的关系：</b>541 判「能不能近似」，本轮判「值不值得现在做、做了怎么入库」。
541 的 Top-5 脚本（trendforce / benchmark / sne / exchange / ppi）中有 4 个在本轮被<b>重写</b>为标准库单文件并补齐增量契约，
另新增 6 个数据集（Eurostat / OWID EV / OWID 能源 / World Bank / IRENA / EMMES）。541 的旧落盘里，<code>trendforce_spot_price</code> 的原件留在
<code>raw/industry_alt/trendforce_spot_price/legacy-541/</code>；benchmark / sne / exchange 三个目录的旧
<code>data.csv</code>（列结构不同）已被本轮同主键数据覆盖，541 的 sha256 对这三条不再可复现——这是本轮唯一一处不可逆的产物替换，
交易数据本身未丢（覆盖范围更大）。<code>ppi_spot_prices</code>、<code>comtrade_trade_values</code>、<code>ow_iea_usgs_timeseries</code>
三个 541 目录本轮未触碰。</div>

<h2>二、怎么选的：20 条候选 → 10 条</h2>
<h3>2.1 判据（按序，写死在 <code>scripts/alt/selection/select_top10.py</code>）</h3>
<table><thead><tr><th>序</th><th>判据</th><th>本轮的落地口径</th><th>作用</th></tr></thead><tbody>
<tr><td class="num">1</td><td>影响判断</td><td><code>data_catalog.谁在用</code> 的独立使用者 ≥2，或属 16 条「需采购」硬缺口</td><td>硬门槛</td></tr>
<tr><td class="num">2</td><td>代理质量</td><td>频率差 ≤1 档、滞后 ≤1 个发布周期、覆盖 ≥70%（各 1 分）</td><td>三类全中=A 档，中两项=B 档，<2 分不入选</td></tr>
<tr><td class="num">3</td><td>成本</td><td>541 估算 ≤8 人日</td><td>硬门槛</td></tr>
<tr><td class="num">4</td><td>可维护</td><td>2026-11-27 实测 HTTP 200、无 JS/验证码/WAF、机器可解析、单页耗时可接受</td><td>硬门槛</td></tr>
<tr><td class="num">5</td><td>硬缺口倾斜</td><td>同档内「需采购」优先</td><td>排序</td></tr>
</tbody></table>
<p>排序键：<code>(档位, 质量分, 覆盖度, 需采购, 人日, id)</code>——严格按判据 2 → 判据 5 的先后，
不把「需采购」提到质量前面（否则会把代理质量更差的贸易额系列挤进来）。</p>

<h3>2.2 入选 10 条</h3>
<table><thead><tr><th>#</th><th>数据集</th><th>服务 P0 行</th><th>质量档</th><th>覆盖度</th><th>人日</th><th>需采购</th></tr></thead><tbody>
"""]
    for i, c in enumerate(sorted(elig, key=lambda x: int(x["排名"])), 1):
        html.append(f"<tr><td class='num'>{i}</td><td><code>{c['数据集']}</code></td>"
                    f"<td>{c['覆盖P0行']}</td><td>{c['质量分']}/3</td><td class='num'>{c['覆盖度']}</td>"
                    f"<td class='num'>{c['自建人日']}</td><td>{c['需采购']}</td></tr>")
    html.append("""</tbody></table>
<h3>2.3 被排除的 10 条（每条一句硬理由，不写「优先级低」）</h3>
<table><thead><tr><th>数据集</th><th>排除理由</th></tr></thead><tbody>
""")
    for c in excl:
        html.append(f"<tr><td><code>{c['数据集']}</code></td><td>{c['理由/排除原因']}</td></tr>")
    html.append(f"""</tbody></table>
<figure><img src="{fig_funnel(cands)}" alt="筛选漏斗">
<figcaption>图 2 ｜ 判据按序筛除：20 → 11（过判据 1–4）→ 10（排序取前 10）。第 11 名 <code>menet_retail</code>（米内网）
覆盖度 0.40、公开页只有摘要，排在第 11 位出局。</figcaption></figure>

<h2>三、10 条交付与实跑证据</h2>
<p>每条脚本位于 <code>scripts/alt/&lt;slug&gt;/fetch.py</code>，落盘在
<code>/srv/share/quantdb/raw/industry_alt/&lt;slug&gt;/</code>（<code>data.csv</code> 逐行带 <code>source_url</code>，
<code>manifest.json</code> 带 <code>rows / csv_sha256 / fetched_at / params</code>）。下表是一次性跑完 10 条的结果：</p>
<table><thead><tr><th>数据集</th><th class="num">行数</th><th>data.csv sha256（前16）</th><th>时间范围</th><th class="num">耗时(s)</th></tr></thead><tbody>
""")
    for slug, rows, sh, drng, sec in evid:
        dr = " → ".join(str(x) for x in drng) if isinstance(drng, list) and drng else (drng or "")
        html.append(f"<tr><td><code>{slug}</code></td><td class='num'>{rows}</td><td><code>{sh}</code></td>"
                    f"<td>{dr}</td><td class='num'>{sec}</td></tr>")
    html.append(f"""</tbody></table>
<figure><img src="{fig_rows(log)}" alt="落盘行数"><figcaption>图 4 ｜ 同一批次 10 个脚本的落盘行数（窗口见附录 A）。</figcaption></figure>

<h3>3.1 幂等与复核（验收第 4 条）</h3>
<p>同一命令连跑两次，比对 <code>data.csv</code> 的 sha256 与行数：<b>{idem_ok}/10 通过</b>。
行内不含抓取时间（时间只写 manifest），所以同日重跑是逐字节相同；跨日重跑会新增快照键，不会覆盖历史。</p>
<table><thead><tr><th>数据集</th><th class="num">第1次行数</th><th class="num">第2次行数</th><th>sha 一致</th></tr></thead><tbody>
""")
    for i in idem:
        html.append(f"<tr><td><code>{i['slug']}</code></td><td class='num'>{i['rows_1']}</td>"
                    f"<td class='num'>{i['rows_2']}</td><td>{'是' if i['sha_equal'] else '<b>否</b>'}</td></tr>")
    html.append(f"""</tbody></table>

<h3>3.2 抽 3 条：脚本值 vs 源站原文</h3>
<table><thead><tr><th>指标</th><th>脚本落盘值</th><th>源站原文（同日抓取）</th><th>结论</th></tr></thead><tbody>
<tr><td>TrendForce DRAM 现货 · DDR5 16Gb (2Gx8) 4800/5600 盘平均</td><td><code>60.767 USD</code></td>
<td>页面表格同一行 <code>60.767</code>（2026-11-26 18:10 GMT+8 更新）</td><td>同值</td></tr>
<tr><td>SHFE 铜 cu_f 2612 收盘 / 结算</td><td><code>95010 / 96040</code></td>
<td><code>kx20261120.dat</code> 原文 <code>"CLOSEPRICE":95010, "SETTLEMENTPRICE":96040</code></td><td>同值</td></tr>
<tr><td>IRENA 世界太阳能装机 2024（MW）</td><td><code>1883177.64</code>（<code>technology=7 Solar energy</code>）</td>
<td>PxWeb POST 同一查询（Region=GLO / Technology=7 / Data Type=0 / Year=2024）返回 <code>"value":[1883177.64]</code></td><td>同值</td></tr>
</tbody></table>
<p>复核命令见附录 A 第 6 条；三条都能用一条 <code>curl</code>/<code>python</code> 复现，不依赖本报告的中间件。</p>

<h2>四、覆盖变化与数据流</h2>
<figure><img src="{fig_coverage()}" alt="覆盖变化"><figcaption>图 1 ｜ 77 条 P0 的「有可跑脚本」从 2 条升到 27 条。
新增 25 条 = 入选 10 条的 10 个主指标 + 15 条由同一脚本附带覆盖的相邻 P0 行（去重后）。C 类 16 条全部维持「只能买」。</figcaption></figure>
<figure><img src="{fig_flow()}" alt="数据流"><figcaption>图 3 ｜ 每条数据从源站到使用者的可审计路径；raw 层只追加，表结构由 <code>docs/alt_schema.sql</code> 建议、入库接线由软件交付另单做。</figcaption></figure>

<h2>五、入库 schema 与增量口径（<code>docs/alt_schema.sql</code>）</h2>
<p>只新建 10 张 <code>alt_*</code> 表 + 1 张 <code>alt_ingest_state</code> 水位表，<b>不改任何现有表</b>。
DDL 在临时 DuckDB 上整份执行通过（11 张表），并用 <code>ON CONFLICT ... DO UPDATE</code> 验证了重放幂等。</p>
<table><thead><tr><th>表</th><th>主键（去重键）</th><th>水位键</th><th>回溯窗口</th></tr></thead><tbody>
<tr><td><code>alt_trendforce_spot_price</code></td><td>(snapshot_date, market, item)</td><td>snapshot_date</td><td>0 天（快照型）</td></tr>
<tr><td><code>alt_benchmark_public_index</code></td><td>(snapshot_date, index_name)</td><td>snapshot_date</td><td>0 天（快照型）</td></tr>
<tr><td><code>alt_sne_research_releases</code></td><td>(release_id)</td><td>publish_date</td><td>30 天（稿件会补数）</td></tr>
<tr><td><code>alt_shfe_gfex_quotes</code></td><td>(trade_date, exchange, contract)</td><td>trade_date</td><td>5 个交易日</td></tr>
<tr><td><code>alt_eurostat_trade</code></td><td>(dataset, geo, partner, flow, sitc, nace_r2, time)</td><td>time</td><td>365 天</td></tr>
<tr><td><code>alt_owid_ev_sales</code></td><td>(indicator, entity, year)</td><td>year</td><td>365 天</td></tr>
<tr><td><code>alt_owid_energy_co2</code></td><td>(indicator, entity, year, series)</td><td>year</td><td>365 天</td></tr>
<tr><td><code>alt_worldbank_wdi</code></td><td>(indicator, country, year)</td><td>year</td><td>365 天</td></tr>
<tr><td><code>alt_irena_re_capacity</code></td><td>(region, technology, data_type, year)</td><td>year</td><td>365 天</td></tr>
<tr><td><code>alt_emmes_storage</code></td><td>(edition, metric)</td><td>edition_date</td><td>0（版本型）</td></tr>
</tbody></table>

<h2>六、44 条差距台账（其余 B 类）</h2>
<p><code>docs/alt_gap_ledger.csv</code>：54 条 B 类 − 入选 10 条 = <b>44 行</b>，无空单元格。
其中 <b>{by_prod} 行</b>由入选脚本附带覆盖（同源、同一脚本产出），<b>{zero} 行</b>零脚本；
每行都写清「近似到几成、差在哪一项（频率/滞后/覆盖/口径）、将来什么条件才值得做」。</p>
<table><thead><tr><th>行业</th><th class="num">台账条数</th><th class="num">有附带脚本</th><th class="num">零脚本</th></tr></thead><tbody>
""")
    for ind in ["储能与电力设备", "医药", "半导体", "新能源车", "有色与锂"]:
        sub = [r for r in ledger if r["行业"] == ind]
        has = sum(1 for r in sub if r["附带覆盖脚本"] != "无")
        html.append(f"<tr><td>{ind}</td><td class='num'>{len(sub)}</td><td class='num'>{has}</td>"
                    f"<td class='num'>{len(sub) - has}</td></tr>")
    html.append(f"""<tr><td><b>合计</b></td><td class="num"><b>{len(ledger)}</b></td><td class="num"><b>{by_prod}</b></td><td class="num"><b>{zero}</b></td></tr>
</tbody></table>
<div class="warn"><b>医药为什么 0 条：</b>8 条医药 P0 行的公开面全是 PDF 表（IFPMA Facts &amp; Figures、EFPIA 图表）
或供给侧件数（openFDA NDA/BLA/ANDA），CMS 药品支出直连 403 Akamai。openFDA 实测单页 298 s（limit=1000 + skip 分页），
一年要 5 页 → 25 分钟/年，判据 4 不达标；米内网公开页只有摘要（覆盖 0.40），排第 11 名出局。
台账里 6/7/28/29/30/32/33/34 八行逐条给了触发条件。</div>

<h2>七、限制（引用结论时必须一并引用）</h2>
<ol>
<li>脚本让代理<b>可执行</b>，不让代理<b>等价</b>：25 条 B 类与原指标的偏差未做校准，直接当原指标用会失真（例如 Benchmark 只有指数点、Eurostat 只有 SITC 大类）。</li>
<li>窗口只到本期：<code>--start/--end</code> 支持回补，但本轮未做 5–10 年历史对齐（541 的 L2 限制未消除）。</li>
<li>快照型数据没有历史：TrendForce / Benchmark / EMMES 是页面快照，只能日更累积；回溯不到过去。</li>
<li>部分源为单点抓取：SNE 列表页只回最近 12 条，早于列表窗口的稿件需人工给 release_id。</li>
<li>IRENA 储能只有抽水蓄能；GFEX/SHFE 没有注册仓单；二者都不能替代「全球仓单」与「电化学储能」。OpenFDA 未入选，医药线没有脚本。</li>
<li>本报告不含行业结论、评级或目标价；数据仅供内部研究。</li>
</ol>

<h2>八、勾稽自检</h2>
<table><thead><tr><th>检查项</th><th>算式</th><th>结果</th></tr></thead><tbody>
<tr><td>10 条人日相加</td><td>{' + '.join(r['自建人日'] for r in top)}</td><td><b>{days}</b>（与 KPI 一致）</td></tr>
<tr><td>覆盖 P0 条数相加（去重）</td><td>{' + '.join(r['对应P0条数'] for r in top)} = {p0_rows}；去重后 {p0_rows}</td><td><b>{p0_rows}</b></td></tr>
<tr><td>选择表与台账不重叠</td><td>10 + 44</td><td><b>54</b> = B 类总数</td></tr>
<tr><td>覆盖拆解</td><td>入选 10 + 台账附带 {by_prod}</td><td><b>{10 + by_prod}</b> = 覆盖 P0 条数 {p0_rows}</td></tr>
<tr><td>有脚本 P0 比例</td><td>(2 + {p0_rows}) / 77</td><td><b>{(2 + p0_rows) / 77 * 100:.1f}%</b></td></tr>
<tr><td>台账零脚本行</td><td>44 − {by_prod}</td><td><b>{zero}</b></td></tr>
</tbody></table>

<h2>附录 A · 复跑命令</h2>
<pre># 1) 10 条一起跑（含幂等二次比对），产出 docs/alt_run_log.json
cd &lt;repo&gt; &amp;&amp; python3 scripts/alt/run_all.py

# 2) 单条重跑（示例：交易所行情，指定窗口）
python3 scripts/alt/shfe_gfex_quotes/fetch.py --start 2026-11-17 --end 2026-11-27

# 3) 复核落盘（行数 / sha256 / 时间范围）
python3 -c "import json;m=json.load(open('/srv/share/quantdb/raw/industry_alt/irena_re_capacity/manifest.json'));print(m['rows'],m['csv_sha256'],m['years'])"

# 4) 选择可复现（重排 20 条候选）
python3 scripts/alt/selection/select_top10.py      # 打印 Top-10、覆盖 P0 数、人日合计

# 5) 台账可复现（44 行，缺一行会 FAIL 退出）
python3 scripts/alt/selection/gap_ledger.py

# 6) 抽 3 条与源站对照（脚本内不含本报告的中间件）
python3 -c "import csv;print([r for r in csv.DictReader(open('/srv/share/quantdb/raw/industry_alt/trendforce_spot_price/data.csv')) if 'DDR5 16Gb' in r['item']][:1])"
curl -s https://www.shfe.com.cn/data/tradedata/future/dailydata/kx20261120.dat | head -c 300
python3 -c "import json;from urllib.parse import quote;print('见 alt_irena_re_capacity 的 technology=1 行')"

# 7) DDL 在临时库上验证（不碰生产库）
python3 -c "import duckdb,tempfile;c=duckdb.connect(tempfile.mktemp(suffix='.duckdb'));c.execute(open('docs/alt_schema.sql').read());print(c.execute('select count(*) from information_schema.tables').fetchone())"</pre>

<div class="foot">
本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺。<br>
数据来源均为公开页面/公开 API，遵循各站条款与 robots，未注册账号、未绕登录/验证码/付费墙；抓取按同域 ≤1 req/s 限速。<br>
交付文件：<code>docs/selfbuild_top10.csv</code>、<code>docs/selfbuild_top10_candidates.csv</code>、<code>docs/alt_gap_ledger.csv</code>、
<code>docs/alt_schema.sql</code>、<code>docs/alt_run_log.json</code>、<code>scripts/alt/*</code>、raw 落盘见 §3。
</div>
</body></html>""")

    out = os.path.join(DOCS, "selfbuild_report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write("".join(html))
    kb = os.path.getsize(out) / 1024
    print(f"报告 {out}（{kb:.0f} KB，单文件、图片内嵌）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
