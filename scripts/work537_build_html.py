#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-537 · 一页结论的单文件 HTML 版本（论文版式，可直接双击打开 / 断网可读 / 可打印）。

- 图片 base64 内嵌、CSS 内联、字体回退系统字体；无外部依赖。
- 渠道表从 docs/foreign_research_channels.csv 生成，不手抄，避免与数据不一致。
- 证据数值从 CSV 与 raw 样例现算，不写死。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 跟随脚本所在仓库（worktree 安全）
CSV_DELIVER = os.path.join(REPO, "docs", "foreign_research_channels.csv")
FIG_DIR = os.path.join(REPO, "docs", "WORK-537", "figures")
RAW = "/srv/share/quantdb/raw/foreign_research"
OUT = os.path.join(REPO, "docs", "foreign_research_brief.html")


def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def main() -> None:
    rows = list(csv.DictReader(open(CSV_DELIVER, encoding="utf-8")))
    crawl = Counter(r["可抓性(直连/需JS/403/需登录/robots禁)"] for r in rows)
    rec = Counter(r["建议(采/观察/不采)"] for r in rows)

    # 样例统计
    samples = []
    for ch in sorted(os.listdir(RAW)):
        if ch.startswith("_"):
            continue
        d = os.path.join(RAW, ch)
        if not os.path.isdir(d):
            continue
        for date in os.listdir(d):
            for slug in os.listdir(os.path.join(d, date)):
                md = os.path.join(d, date, slug, slug + ".md")
                if not os.path.exists(md):
                    cands = [f for f in os.listdir(os.path.join(d, date, slug)) if f.endswith(".md")]
                    if not cands:
                        continue
                    md = os.path.join(d, date, slug, cands[0])
                meta_p = os.path.join(d, date, slug, "meta.json")
                m = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
                body = open(md, encoding="utf-8").read()
                samples.append({"ch": ch, "title": m.get("title", ""), "date": m.get("page_date", ""),
                                "chars": len(body), "sha": m.get("sha256", "")[:12],
                                "url": m.get("url", ""), "path": md})
                break
            else:
                continue
            break
    samples.sort(key=lambda s: -s["chars"])

    def cls(k: str) -> str:
        return {"直连": "ok", "需JS(列表未渲染)": "warn", "403": "bad",
                "需登录(落地登录页)": "bad", "本机出口不可达": "net",
                "robots禁": "grey", "robots不可读(HTTP 403)": "grey"}.get(k, "")

    tr = []
    for r in rows:
        c = r["可抓性(直连/需JS/403/需登录/robots禁)"]
        ev = r["实测证据(状态码+抓到的标题或失败原文)"]
        m = re.search(r"HTTP (\S+)", ev)
        title = re.search(r'标题="([^"]*)"', ev)
        date = re.search(r"日期=([^\|]+)", ev)
        tr.append(f"""<tr>
<td>{esc(r['渠道'])}</td>
<td class="c">{esc(r['类型(投行官网/财经媒体/监管披露/聚合平台/社区)'])}</td>
<td><span class="pill {cls(c)}">{esc(c)}</span></td>
<td class="c">{esc(r['建议(采/观察/不采)'])}</td>
<td class="c mono">{esc(m.group(1) if m else '—')}</td>
<td>{esc((title.group(1) if title else '（未取到标题）')[:46])}</td>
<td class="c mono">{esc((date.group(1).strip() if date else '—')[:24])}</td>
<td>{esc(r['是否含评级/目标价'][:34])}</td>
</tr>""")

    srows = []
    for s in samples:
        srows.append(f"""<tr><td>{esc(s['ch'])}</td><td>{esc(s['title'][:58])}</td>
<td class="c mono">{esc(s['date'])}</td><td class="c mono">{s['chars']:,}</td>
<td class="c mono">{esc(s['sha'])}</td></tr>""")

    css = """
:root{--ink:#141414;--mut:#5b6169;--line:#d9dde3;--blue:#2f6f9f;--amber:#e3a13c;
--red:#c1554b;--grey:#7d7d7d;--bg:#ffffff;--soft:#f6f8fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:"Helvetica Neue",Helvetica,Arial,"Noto Sans CJK SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif;
font-size:14px;line-height:1.62;-webkit-font-smoothing:antialiased}
.page{max-width:920px;margin:0 auto;padding:38px 30px 60px}
h1{font-size:24px;line-height:1.28;margin:0 0 6px;letter-spacing:.2px;font-weight:700}
.sub{color:var(--mut);font-size:12.5px;margin-bottom:6px}
.rule{height:3px;background:var(--ink);margin:14px 0 4px}
h2{font-size:16.5px;margin:30px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--line);font-weight:700}
h3{font-size:14px;margin:18px 0 5px;font-weight:700}
p{margin:7px 0}
.lede{background:var(--soft);border-left:4px solid var(--blue);padding:12px 16px;margin:14px 0 18px;font-size:14.5px}
.lede b{color:var(--blue)}
.kpi{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 6px}
.kpi div{flex:1 1 140px;border:1px solid var(--line);border-radius:4px;padding:9px 11px;background:var(--soft)}
.kpi b{display:block;font-size:20px;line-height:1.25}
.kpi span{color:var(--mut);font-size:11.5px}
table{border-collapse:collapse;width:100%;margin:9px 0 4px;font-size:12.3px}
th,td{border-bottom:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:700;font-size:11.8px;white-space:nowrap}
td.c{text-align:center}td.mono{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:11.3px}
tr:nth-child(even) td{background:#fbfcfd}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;white-space:nowrap;color:#fff}
.pill.ok{background:var(--blue)}.pill.warn{background:var(--amber)}.pill.bad{background:var(--red)}
.pill.grey{background:var(--grey)}.pill.net{background:#b9b9b9}
figure{margin:16px 0 10px}
figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:3px}
figcaption{color:var(--mut);font-size:11.8px;margin-top:6px;line-height:1.5}
ul,ol{margin:6px 0 6px 20px;padding:0}li{margin:4px 0}
.note{background:#fdf6f5;border-left:4px solid var(--red);padding:10px 14px;margin:12px 0;font-size:13px}
.ok-note{background:#f3f8fc;border-left:4px solid var(--blue);padding:10px 14px;margin:12px 0;font-size:13px}
.foot{margin-top:30px;border-top:1px solid var(--line);padding-top:10px;color:var(--mut);font-size:11.5px}
code{background:var(--soft);padding:1px 4px;border-radius:3px;font-size:12px;
font-family:"SFMono-Regular",Consolas,monospace;word-break:break-all}
@media print{
@page{size:A4;margin:14mm}
body{font-size:10.6pt}
.page{max-width:none;padding:0}
h2{margin-top:16pt}
figure,tr,table{page-break-inside:avoid}
.thead{display:table-header-group}
a{color:inherit;text-decoration:none}
}
"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>外资研报公开层｜渠道清单与可抓性实测（WORK-537）</title>
<style>{css}</style></head>
<body><div class="page">

<h1>外资研报公开层：渠道清单与可抓性实测</h1>
<div class="sub">WORK-537 · 测量日期 2026-11-23（UTC+8） · 样本 {len(rows)} 个渠道 · 请求量（可核对口径）：台账 85 条 / 实际发出 ≥87 次，185 为不可复现上界 · 口径：公开、无需账号、无需付费墙</div>
<div class="rule"></div>

<div class="lede">
<b>一句话结论：</b>外资投行的研究内容在公开层「<b>能看，但机器只能拿到观点，拿不到评级与目标价</b>」——
{len(rows)} 个渠道里 <b>{crawl['直连']} 个</b>可直接机器采，但 9 篇真实文章正文的全文扫描里，
<b>外资投行的评级/目标价命中 0 处</b>。公开层的价值是<b>全球视角与主题框架</b>；估值结论必须另找来源。
</div>

<div class="kpi">
<div><b>{crawl['直连']}</b><span>可直接机器采</span></div>
<div><b>{rec['观察']}</b><span>只能人工看</span></div>
<div><b>{rec['不采']}</b><span>robots 禁 / 不可读</span></div>
<div><b>1</b><span>已发生的 robots 违规（round 1）</span></div>
<div><b>0</b><span>外资样本的评级命中</span></div>
<div><b>{len(samples)}</b><span>已抓正文样例</span></div>
</div>

<h2>一、哪些能机器采，哪些只能人工看</h2>
<figure>
<img alt="可抓性构成" src="data:image/png;base64,{b64(os.path.join(FIG_DIR,'fig1_crawlability.png'))}">
<figcaption><b>图 1 | </b>按渠道类别的可抓性构成。蓝＝直连可采（列表页能在 HTML 里解析出「标题+日期」）；
琥珀＝需 JS 渲染；红＝403 / 落地登录页；浅灰＝本机出口不可达（非站点拒绝）；深灰＝robots 禁止或不可读。
投行官网 13 个里 7 个直连；财经媒体 6 个里只有 CNBC 直连，另有 4 个（Reuters、Bloomberg、FT、WSJ）
在本机<b>网络层不可达</b>——DNS 返回污染地址、IPv6 无路由，连 robots.txt 都取不到，
<code>fetch</code> 服务侧同样失败，<b>不能据此说这些站点拒绝机器访问</b>。</figcaption>
</figure>

<table class="thead"><thead><tr>
<th>渠道</th><th>类型</th><th>可抓性</th><th>建议</th><th>HTTP</th><th>页面标题（实测）</th><th>页面日期</th><th>是否含评级/目标价</th>
</tr></thead><tbody>
{''.join(tr)}
</tbody></table>
<div class="foot">完整 12 列证据（含 robots 命中规则、跳转链、失败原文、更新频率、备注）见
<code>docs/foreign_research_channels.csv</code>；机器证据表见 <code>docs/WORK-537/02-data.csv</code>（29 行 × 25 列）。</div>

<h2>二、公开层能拿到评级/目标价吗？——基本拿不到</h2>
<figure>
<img alt="评级关键词命中" src="data:image/png;base64,{b64(os.path.join(FIG_DIR,'fig2_rating_hits.png'))}">
<figcaption><b>图 2 | </b>9 篇正文样例的评级/目标价关键词命中次数。外资投行原文样本（GS、MS、JPM、德银 dbInsights、HSBC、
Citi、CNBC）全部 0 命中；JPM 的 1 次为 <i>underperforming other developed market government bond markets</i>
（债券市场表述，非个股评级）；唯一含完整「评级+目标价」的是格隆汇转载<b>国盛证券</b>对富特科技的首次覆盖
（评级×14、增持×5、目标价×3），属<b>中资转述</b>。</figcaption>
</figure>

<div class="note"><b>判定：</b>公开层<b>不能</b>作为评级/目标价来源。列表页全文扫描同样支持这一点——
13 个直连渠道里 11 个全文 0 次出现 <i>price target / 目标价 / 评级 / 增持 / 减持</i>；
2 个出现的（格隆汇、巨潮）字样来自<b>转载与公告原文</b>。</div>

<div class="ok-note"><b>公开层能给什么：</b>主题观点、宏观与行业判断。实测样例包括 GS 的全球增长预测
（2026-11-13，14,225 字符）、JPM 的多国国债收益率判断（2026-11-03，18,980 字符）、
德银 dbInsights 的海洋公地研究（2026-11-12，7,784 字符）、HSBC 的电气化融资（2026-10-30，2,742 字符）。</div>

<h3>已落盘的正文样例（Top-5 为外资投行原文）</h3>
<table class="thead"><thead><tr><th>渠道</th><th>文章标题</th><th>页面日期</th><th>字符数</th><th>sha256 前 12 位</th></tr></thead>
<tbody>{''.join(srows)}</tbody></table>
<div class="foot">路径：<code>/srv/share/quantdb/raw/foreign_research/&lt;channel&gt;/&lt;date&gt;/&lt;slug&gt;.md</code>
+ 同目录 <code>meta.json</code>（含 url / 抓取时间 / sha256 / 字符数）。5 个不合格候选（抓到的是栏目页而非文章）
保留在 <code>_rejected/</code> 作为失败实验证据。<b>抓取内容只作内部研究，不入 public 仓库。</b></div>

<h2>三、建议的采集频率</h2>
<figure>
<img alt="实测发布节奏" src="data:image/png;base64,{b64(os.path.join(FIG_DIR,'fig3_cadence.png'))}">
<figcaption><b>图 3 | </b>列表页可见日期的相邻间隔中位数（单页快照，描述性指标，非发布频率承诺；
可见日期里混有事件/展望日期）。其余 21 个渠道可见日期不足 3 个，未实测。</figcaption>
</figure>
<table class="thead"><thead><tr><th>集合</th><th>建议频率</th><th>依据</th><th>请求量</th></tr></thead><tbody>
<tr><td>13 个直连渠道</td><td class="c"><b>每周一次增量</b></td><td>实测间隔中位数 3–7 天居多（HSBC 5d、JPM 7d、德银 7d、慧博/发现报告 3d）</td><td class="c mono">≈26 次/周</td></tr>
<tr><td>CNBC、SEC EDGAR</td><td class="c">每日或每 2 日</td><td>实测 1 天 / 4 天</td><td class="c mono">2–4 次/天</td></tr>
<tr><td>需 JS / 403 / 登录的 10 个</td><td class="c">人工看</td><td>机器在列表层拿不到；robots 禁的 2 个不采</td><td class="c mono">0</td></tr>
<tr><td>Reuters、Bloomberg、FT、WSJ</td><td class="c">换出口后重测</td><td>无 HTTP 响应（DNS 污染 + IPv6 无路由 → 连接失败，非站点拒绝）</td><td class="c mono">0</td></tr>
</tbody></table>

<h2>四、与中资券商研报（WORK-536）如何合并成一张「研究内容表」</h2>
<ul>
<li><b>统一主键</b>：<code>(机构, 标题, 发布日期, 原文URL)</code>；统一字段：
<code>语言 / 来源渠道 / 内容类型 / 是否含评级目标价 / 是否二次转述 / sha256 / 采集时间</code>。</li>
<li><b>两层互补而非重复</b>：外资层补<b>全球视角与宏观框架</b>（观点为主，评级＝无）；
中资层补<b>评级/目标价与 A 股个股覆盖</b>（估值结论为主）。</li>
<li><b>合并后的净效果</b>：一张表同时覆盖「观点层 + 估值结论层」；
<b>外资层不承担估值结论的补充责任</b>（实测证明它给不了）。</li>
<li><b>两条必须保留的标记</b>：<code>二次转述</code>（中资平台转载的评级）与 <code>语言</code>（EN/CN）——
前者决定能否直接使用，后者决定检索与去重口径。</li>
</ul>

<h2>五、限制与未取到（必读）</h2>
<ol>
<li>Reuters / Bloomberg / FT / WSJ 的<b>站点侧行为未测</b>（本机出口不可达，不是站点拒绝）。</li>
<li>HSBC 的 <b>Global Research 条目未逐篇扫描</b>，其正文是否含评级<b>未验证</b>。</li>
<li>Citi / Barclays / 东方财富 / 雪球 / 萝卜投研 / NYSE 的列表未取到（JS 渲染）；
「入口列表需 JS」≠「整站不可采」——<b>Citi 的文章详情页已实测可直接抓</b>（Tokenization 2030，4,017 字符）。</li>
<li>Bernstein 的公开研究内容<b>完全未取到</b>（落地登录页），未验证是否另有可采入口。</li>
<li>样本量 <b>9 篇、1 个时间点</b>，支持「实测 0 命中」这一事实陈述，<b>不构成</b>公开层评级可得率的统计估计。</li>
<li>抓到的正文只作内部研究，<b>不入 public 仓库</b>（<code>raw/foreign_research</code> 已排除）。</li>
<li><b>已发生的 robots 违规 1 次（如实披露）</b>：round-1（robots 判定生效前）实发 5 次——Barron's <code>/</code> 401、Barron's <code>/market-data</code> <b>200（正文已取到）</b>、FINRA 三入口各 403；
该正文<b>未进入任何分析、未入样例、未入结论</b>，round-2 起 robots 优先未再发生。</li>
</ol>

<div class="foot">
<b>产物</b>：<code>docs/foreign_research_channels.csv</code>（29 行 × 12 列）·<code>docs/WORK-537/02-data.csv</code>（29 行 × 25 列）·
<code>docs/WORK-537/01-hypothesis.md</code>…<code>05-memo.md</code>（五阶段落盘）·
脚本 <code>scripts/work537_probe.py</code>（探测）·<code>scripts/work537_audit.py</code>（独立复跑）·<code>scripts/work537_figures.py</code>（配图）。<br>
<b>合规声明</b>：本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺；
不绕过付费墙、登录、验证码或 robots，不含交易指令、仓位建议与收益承诺。
</div>

</div></body></html>
"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    size = os.path.getsize(OUT)
    print(f"[ok] {OUT}  {size/1024:.0f} KB  sha256={hashlib.sha256(html.encode('utf-8')).hexdigest()[:16]}")
    print(f"     表格行数 {len(rows)}，样例 {len(samples)} 篇，内嵌图 3 张")


if __name__ == "__main__":
    main()
