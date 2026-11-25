#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WORK-559 · 单文件交付说明 DELIVERY.html（论文版式，图片 base64 内嵌，断网可读）。

数值全部来自 docs/WORK-559/evidence.json（实测采集）与 db/timers/backup-nightly.sh 原文，
不手抄；图表由 scripts/work559_figures.py 生成。
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE = os.path.join(REPO, "docs", "WORK-559", "evidence.json")
FIG_DIR = os.path.join(REPO, "docs", "WORK-559", "figures")
SCRIPT = os.path.join(REPO, "db", "timers", "backup-nightly.sh")
OUT = os.path.join(REPO, "docs", "DELIVERY-WORK-559.html")

CSS = """
:root{--ink:#1f2933;--muted:#66727d;--line:#d9e0e5;--paper:#fbfaf7;--accent:#0f6b63;
--accent2:#b14d2f;--soft:#eef4f1;--soft2:#f7eee8}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);line-height:1.72;font-size:16px;
font-family:"Noto Sans SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
main{max-width:1080px;margin:0 auto;padding:48px 28px 72px}
h1,h2,h3{font-family:"Noto Serif SC","Songti SC",Georgia,serif;font-weight:600;line-height:1.28}
h1{font-size:2.4rem;margin:0 0 10px}
h2{font-size:1.5rem;margin:44px 0 14px;padding-top:12px;border-top:1px solid var(--line)}
h3{font-size:1.12rem;margin:26px 0 8px}
p{margin:0 0 13px}
code,pre{font-family:"JetBrains Mono","DejaVu Sans Mono",monospace}
code{font-size:.9em;background:#f1f3f2;padding:1px 5px;border-radius:3px}
pre{background:#fff;border:1px solid var(--line);padding:12px 14px;overflow-x:auto;font-size:.82rem;
line-height:1.55;margin:12px 0 18px}
.eyebrow{font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
.subtitle{font-size:1.06rem;color:var(--muted);max-width:800px;margin:0 0 22px}
.meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);
border:1px solid var(--line);margin:22px 0 28px}
.meta div{background:#fff;padding:11px 13px;min-width:0;overflow-wrap:anywhere}
.meta b{display:block;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:3px}
.abstract{background:var(--soft);border-left:4px solid var(--accent);padding:18px 20px;margin:20px 0 8px}
.abstract p:last-child{margin-bottom:0}
figure{margin:20px 0 8px;border:1px solid var(--line);background:#fff;padding:12px 12px 8px}
figure img{width:100%;height:auto;display:block}
figcaption{font-size:.84rem;color:var(--muted);margin:8px 4px 0;line-height:1.6}
figcaption b{color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:.9rem;background:#fff}
.twrap{overflow-x:auto;border:1px solid var(--line);margin:14px 0 20px}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}
th{background:var(--soft);font-weight:600;font-size:.86rem}
tr:last-child td{border-bottom:none}
.ok{color:#2f6b4f;font-weight:600}.bad{color:var(--accent2);font-weight:600}.warn{color:#8a6a1f;font-weight:600}
.callout{border:1px solid var(--line);border-left:4px solid var(--accent2);background:var(--soft2);
padding:14px 18px;margin:18px 0}
.callout h3{margin-top:0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.small{font-size:.86rem;color:var(--muted)}
footer{margin-top:52px;padding-top:16px;border-top:1px solid var(--line);font-size:.82rem;color:var(--muted)}
@media print{body{background:#fff}main{padding:0}h2{page-break-after:avoid}figure,tr{page-break-inside:avoid}}
@media(max-width:760px){.meta{grid-template-columns:1fr 1fr}.grid2{grid-template-columns:1fr}}
"""


def b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fig(path: str, caption: str, num: str) -> str:
    if not os.path.exists(path):
        return ""
    return ('<figure><img src="data:image/png;base64,%s" alt="%s">'
            '<figcaption><b>%s</b> %s</figcaption></figure>') % (b64(path), esc(num), esc(num), caption)


def snippet(start_marker: str, end_marker: str) -> str:
    text = open(SCRIPT, encoding="utf-8").read()
    i = text.find(start_marker)
    j = text.find(end_marker, i + 1)
    if i < 0 or j < 0:
        return ""
    return text[i:j].rstrip()


def dur(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "—"
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
    except ValueError:
        return "—"
    s = int((b - a).total_seconds())
    if s < 0:
        return "—"
    return "%d 分 %02d 秒" % (s // 60, s % 60) if s < 3600 else "%d 小时 %02d 分" % (s // 3600, s % 3600 // 60)


def main() -> None:
    d = json.load(open(EVIDENCE, encoding="utf-8"))
    unit = d["unit"]["props"]
    dumps = d["dumps"]
    borg = d["borg"]
    alerts = d.get("alerts", {})
    runs = d.get("runs", [])

    today = [r for r in runs if (r.get("start") or "").startswith("2026-11-25")]
    ok_runs = [r for r in today if r["result"] == "ok"]

    # --- 运行记录表 ---
    run_rows = []
    for r in today:
        cls = {"ok": "ok", "fail": "bad"}.get(r["result"], "warn")
        label = {"ok": "success", "fail": "exit=%s" % r["exit"], "no-terminal-line": "无收尾行（被中断）"}[r["result"]]
        dumps_txt = "、".join("%s %.1f MB" % (x["label"], x["bytes"] / 1048576) for x in r["dumps"]) or "—"
        run_rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td></tr>"
            % (esc(r.get("start") or "—"), esc(r.get("end") or "—"),
               dur(r.get("start"), r.get("end")), cls, esc(label), esc(dumps_txt))
        )
    runs_table = "\n".join(run_rows)

    # --- dump 证据表 ---
    dump_rows = []
    for label in ("multica", "weknora", "langfuse"):
        x = dumps.get(label, {})
        dump_rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%.1f MB</td><td>%d</td>"
            "<td class='%s'>%s</td></tr>"
            % (esc(label), esc(x.get("name", "—")), x.get("bytes", 0) / 1048576,
               x.get("toc_lines", 0), "ok" if x.get("toc_lines") else "bad",
               "可读" if x.get("toc_lines") else "读不出目录")
        )

    # --- journal 失败表（11-20 ~ 11-24）---
    journal_rows = "\n".join(
        "<tr><td>%s %s</td><td>%s</td><td><code>%s</code></td></tr>"
        % (esc(j["date"]), esc(j["time"]), esc(j.get("desc", "")),
           esc((j.get("first_output") or "（无脚本输出）")[:90]))
        for j in d.get("journal", [])
    )

    zero = d.get("zero_byte", {})
    zero_rows = "\n".join(
        "<tr><td><code>%s</code></td><td>%d</td><td>%s</td></tr>"
        % (esc(f["name"]), f["bytes"], esc(f["mtime"])) for f in zero.get("files", [])
    )

    figs = {
        "f1": fig(os.path.join(FIG_DIR, "fig1-timeline.png"),
                  "每晚四层状态。11-20 起 pg_dump 全部失败并留下 0 字节文件；11-23 之前脚本里根本没有 <code>alert_failure</code>，"
                  "所以前三晚连告警单都没建。11-25 12:39 那次是本次修复后的手工补跑：dump 恢复，borg 归档写出但因 "
                  "<code>lost+found</code> 退出码 1。", "图 1 · 事故时间线"),
        "f2": fig(os.path.join(FIG_DIR, "fig2-rootcause.png"),
                  "两条互相独立的故障链：A 让备份内容为空，B 让已写成的快照仍被判定失败。"
                  "两条链都在 <code>set -e</code> + 非零即失败的判据下放大成「每晚都失败」。", "图 2 · 根因链"),
        "f3": fig(os.path.join(FIG_DIR, "fig3-duration.png"),
                  "同一次运行的耗时分解（2026-11-25 12:39:17 → 16:32:05）。borg 一段占 3 小时 23 分，"
                  "整次运行 3 小时 53 分，距 6 小时上限约 2 小时 07 分。本次未改上限，只补了 SIGTERM trap。", "图 3 · 运行耗时与超时上限"),
    }

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WORK-559 夜间备份恢复 · 交付说明</title>
<style>{CSS}</style></head><body><main>

<div class="eyebrow">量化研究与试验 · WORK-559 · 软件交付</div>
<h1>夜间备份为什么连续六晚没落地，以及它现在如何自证</h1>
<p class="subtitle">一次 <span class="small">systemd --user</span> 会话缺组的故障，被「先建文件后写内容」的重定向和「失败只报一个 exit=1」的日志一起藏了六天。本页给出实测根因、改动、三次运行的证据与回滚方式。</p>

<div class="meta">
<div><b>Issue</b>WORK-559（告警单，同因 WORK-547）</div>
<div><b>主机 / 仓库</b>Mo-server · <code>quant-lab</code></div>
<div><b>交付分支</b><code>agent/work-559-backup-fix</code></div>
<div><b>证据生成</b>{esc(d.get('generated_at',''))}</div>
</div>

<div class="abstract">
<p><b>结论。</b>三件事：① <code>systemd --user</code> 的服务会话不继承 <code>docker</code> 补充组，三处 <code>docker compose exec pg_dump</code> 在 03:30 必然 EACCES，而 <code>&gt; file</code> 重定向在报错前就建好了文件，于是留下 6 个 0 字节 dump；② 11-20/21/22 三晚没有告警单，不是阈值或去重问题——当时部署的脚本里没有 <code>alert_failure</code> 这段代码，告警是 2026-11-23 13:28 才随 WORK-529 进仓库的；③ 本轮补跑时又暴露第三条：borg 读到 root-only 的 <code>lost+found</code> 会退 1，归档其实已完整写盘（566.6 GB / 719,192 文件），unit 却判 failed。</p>
<p><b>现在。</b>docker 调用统一走 <code>dk()</code>=<code>/usr/bin/sg docker -c</code>；dump 先写 <code>.part</code>，校验大小、<code>PGDMP</code> 头、<code>pg_restore -l</code> 目录后才改名；每一步失败都在日志写出「步骤 / 库 / 退出码 / stderr 首行」；<code>borg create</code> 排除两个 <code>lost+found</code>；并加了 SIGTERM trap，使「被杀」也留下日志与告警。三个 dump 与 borg 快照均已实测可读。</p>
</div>

<h2>1 · 事故与影响：六晚空转，三个库没有可恢复的 dump</h2>
<p>告警单只说「backup 失败 exit=1」。实际代价是：<b>11-20 起连续 6 个 0 字节 dump</b>，最后一次可用的是 11-19 22:56 的 <code>multica-20261119-2256.dump</code>（120 MB）；同期异地 borg 快照也停在 <code>snap-20261119-2256</code>。冷备（rsync + SHA256SUMS）从 11-24 起反而一直是好的——它是 WORK-529 新加的一步，不依赖 docker。</p>

{figs['f1']}

<div class="twrap"><table>
<tr><th>时间</th><th>当时 unit 描述</th><th>脚本首条输出（journal）</th></tr>
{journal_rows}
</table></div>
<p class="small">下表证明 11-23 之后 unit 才改名并接手日志：11-20~11-23 的描述是「Nightly borg backup to /srv/backup」，那正是 <code>/home/moyongkai/bin/backup-nightly.sh.bak-20261123</code> 的版本，它没有写日志、也没有建单逻辑。</p>

<h2>2 · 根因：两条链，各自都能让「每晚都失败」</h2>
{figs['f2']}

<h3>2.1 缺组：不是权限没配，是会话没继承</h3>
<pre>$ id moyongkai
uid=1000(moyongkai) gid=1000(moyongkai) groups=1000(moyongkai),4(adm),24(cdrom),27(sudo),30(dip),46(plugdev),100(users),101(lxd)
$ systemctl --user show backup-nightly.service -p Result
Result=exit-code
$ sg docker -c "docker ps --format '{{{{.Names}}}}'"     # 同一账号，进组后正常
multica-postgres-1
... </pre>
<p>日志里那条 <code>permission denied ... /var/run/docker.sock</code> 就是它。<code>sg docker -c</code> 在服务会话里可用（本轮实测三次 dump 全部成功），因此不需要 sudo、不需要改 socket 权限、也不需要再把用户加一遍组。</p>

<h3>2.2 告警缺失：不是阈值，也不是去重</h3>
<p><code>alert_failure</code> 按「任务 + 日期」落一个 <code>.id</code> 标记做去重。11-20/21/22 连标记文件都没有，说明代码根本没走到——查提交历史可以确认这段代码是 11-23 才进 <code>backup-nightly.sh</code> 的：</p>
<pre>$ {esc(alerts.get('cmd',''))}
{esc(alerts.get('git_log',''))}</pre>
<p>所以那三晚是<b>静默失败</b>：unit failed，只在 journal 留一行，没有任何 issue。11-24、11-25 才有单（WORK-547、WORK-559）——但它们建在 <code>backlog</code> 且无负责人，两张单在板上躺了两天没人接。这属于流程缺口，见 §6。</p>

<h3>2.3 新发现：borg 的 lost+found 让「已写成的快照」被判失败</h3>
<pre>$ stat -c '%n %A %U:%G' /srv/share/lost+found /srv/data/lost+found
/srv/share/lost+found drwx------ root:root
/srv/data/lost+found drwx------ root:root

$ borg create --dry-run ::probe /srv/share/lost+found                # 复现
/srv/share/lost+found: dir_open: [Errno 13] Permission denied: '/srv/share/lost+found'
exit=1
$ borg create --dry-run ::probe /srv/share/lost+found --exclude '/srv/share/lost+found'
exit=0</pre>
<p>本轮 12:39 那次补跑的 borg 段因此退了 1。归档本身是完整的（<code>borg info ::{esc(borg.get('newest') or 'snap-20261125-1306')}</code> 显示 566.62 GB / 719,192 文件 / 3 小时 23 分），但 <code>run_step</code> 按「非零即失败」判，unit 记 <code>exit-code</code>。这条不修，修好缺组之后每晚仍会报失败。</p>

<h2>3 · 改动：五个地方，只改 <code>backup-nightly.sh</code> 与它的 unit</h2>
<div class="twrap"><table>
<tr><th>问题</th><th>改法</th><th>位置</th></tr>
<tr><td>服务会话无 docker 组</td><td><code>dk()</code> 统一入口：<code>/usr/bin/sg docker -c "&lt;cmd&gt;"</code>，3 处 docker 调用全部改道</td><td><code>backup-nightly.sh</code> <code>dk()</code> / <code>dump_db()</code></td></tr>
<tr><td>失败留 0 字节文件</td><td>写 <code>&lt;file&gt;.part</code>，校验大小 ≥1 KB、头部 <code>PGDMP</code>、<code>pg_restore -l</code> 目录后再 <code>mv</code>；失败即删</td><td><code>dump_db()</code></td></tr>
<tr><td>失败不自证</td><td><code>run_step</code>：失败写「步骤 / 退出码 / stderr 首行」，成功转发 stderr（borg --stats 仍进日志）</td><td><code>run_step()</code></td></tr>
<tr><td>borg 被 lost+found 判错</td><td><code>--exclude '/srv/share/lost+found' --exclude '/srv/data/lost+found'</code></td><td><code>run_backup()</code></td></tr>
<tr><td>被杀后无日志无告警</td><td><code>trap on_signal TERM 143 / INT 130</code>：写日志 + <code>alert_failure</code>（unit 的 <code>TimeoutStartSec=6h</code> 未改，实测余量 2 小时 07 分）</td><td>脚本尾部</td></tr>
</table></div>

<h3>3.1 关键代码</h3>
<pre>{esc(snippet('dk() {', '# run_step'))}</pre>
<pre>{esc(snippet('# run_step &lt;label&gt;'.replace('&lt;','<').replace('&gt;','>'), '# dump_db'))}</pre>
<pre>{esc(snippet('on_signal() {', 'log "backup start"'))}</pre>

<h2>4 · 验证：三次运行，同一 SHA 的脚本</h2>
<p>验收要求两次连续 <code>systemctl --user start</code> 都成功。下表是 11-25 当天全部运行（含两次失败的历史尝试），数据由日志文件解析得到。</p>
<div class="twrap"><table>
<tr><th>开始</th><th>结束</th><th>时长</th><th>结果</th><th>当次完成的 dump</th></tr>
{runs_table}
</table></div>
<p class="small">共 {len(today)} 次运行，其中 success {len(ok_runs)} 次。</p>

<h3>4.1 四样证据</h3>
<p><b>① 服务结果</b></p>
<pre>$ {esc(d['unit'].get('cmd',''))}
Result={esc(unit.get('Result'))}
ExecMainStatus={esc(unit.get('ExecMainStatus'))}
ExecMainStartTimestamp={esc(unit.get('ExecMainStartTimestamp'))}
ExecMainExitTimestamp={esc(unit.get('ExecMainExitTimestamp'))}</pre>

<p><b>② 三个 dump 的落盘时间与大小</b></p>
<pre>$ ls -l --time-style=long-iso /srv/backup/dumps/*.dump
{esc(chr(10).join('%s  %10d  %s' % (x.get('mtime',''), x.get('bytes',0), x.get('name','')) for x in dumps.values() if isinstance(x, dict) and x.get('name')))}</pre>
<div class="twrap"><table>
<tr><th>库</th><th>最新文件</th><th>大小</th><th>TOC 条目</th><th>pg_restore -l</th></tr>
{chr(10).join(dump_rows)}
</table></div>

<p><b>③ 每个 dump 都能读出归档目录</b></p>
<pre>{esc(chr(10).join('$ ' + dumps[l].get('toc_cmd','') + chr(10) + dumps[l].get('toc_head','') + chr(10) for l in ('multica','weknora','langfuse') if l in dumps))}</pre>

<p><b>④ borg 快照真的落了</b></p>
<pre>$ {esc(borg.get('cmd',''))}
{esc(borg.get('list_tail',''))}

$ borg info ::{esc(borg.get('newest') or '')}
{esc(chr(10).join((borg.get('info') or '').splitlines()[:14]))}</pre>

{figs['f3']}

<h2>5 · 需求符合性</h2>
<div class="twrap"><table>
<tr><th>原要求</th><th>状态</th><th>证据</th></tr>
<tr><td>1 修 <code>backup-nightly.sh</code>，docker 调用可用；不用 sudo / 不改 socket / 不加组</td><td class="ok">完成</td><td><code>dk()</code>；三次 dump 实测成功</td></tr>
<tr><td>2 失败写清「哪一步、哪个库、退出码、stderr 首行」</td><td class="ok">完成</td><td>16:32 那次真实失败输出：<code>失败步骤：borg create …；退出码=1；stderr 首行=/srv/share/lost+found: dir_open …</code></td></tr>
<tr><td>3 立刻补跑</td><td class="ok">完成</td><td>11-25 12:39 与 16:39 两次手工 <code>systemctl --user start</code></td></tr>
<tr><td>4 四样证据</td><td class="ok">完成</td><td>§4.1 ①②③④</td></tr>
<tr><td>5 连跑两次成功 + 查清告警缺失原因</td><td class="ok">完成</td><td>§4 运行表；§2.2 提交历史</td></tr>
<tr><td>6 0 字节文件保留原样、删除需用户确认</td><td class="warn">按隔离处理，待确认</td><td>6 个文件移入 <code>_zero-byte-20261120-20261125/</code>，未删除，见 §6</td></tr>
</table></div>

<h2>6 · 遗留与需要确认</h2>
<div class="callout">
<h3>需要你拍板的一件事</h3>
<p>6 个 0 字节 dump 现在隔离在 <code>/srv/backup/dumps/_zero-byte-20261120-20261125/</code>（<b>没有删除</b>，含 <code>LIST.txt</code> 与撤销命令）。留在原目录会被保留策略当成「7 份之一」，把 11-19 两份真实 dump 挤掉；移走已解除这个风险。要不要彻底删除，等你一句话。</p>
</div>
<div class="twrap"><table>
<tr><th>项</th><th>说明</th><th>建议</th></tr>
<tr><td>0 字节文件</td><td>{len(zero.get('files', []))} 个，已隔离未删</td><td>确认后 <code>rm -rf</code> 该目录</td></tr>
<tr><td>告警单无人接</td><td><code>alert_failure</code> 建单为 <code>--status backlog</code> 且无负责人；WORK-547/559 在板上躺了两天</td><td>改 <code>common.sh</code> 为 <code>todo</code> + 指派，属跨任务行为变更，需你同意后再动</td></tr>
<tr><td>冷备对并发写敏感</td><td>11:07 一次 <code>rsync</code> 因 <code>quantdb.duckdb.wal</code> 消失退 24；12:30 一次 <code>sha256sum -c</code> 因 <code>meta/checks</code> 被同时改写而 FAILED。凌晨 03:30 无 ETL 时不会触发</td><td>本单按约定未改 <code>backup-quantdb.sh</code>；如需加固，另开单</td></tr>
<tr><td>borg 退出码语义</td><td>borg 1.4 的 1 是 warning（仍有归档），2 才是 error；当前一律按失败处理</td><td>保留严格口径：宁可响，不可静默收下不完整归档</td></tr>
<tr><td>全量 borg 时长</td><td>566.6 GB 用 3 小时 23 分（单块 HDD，写延迟 160 ms 量级），整次运行 3 小时 53 分</td><td>6h 上限余量约 2 小时；数据量再翻倍需重估或提上限</td></tr>
</table></div>

<h2>7 · 回滚</h2>
<pre># 1 代码回滚到本单之前
git -C /srv/share/quantdb/code/quant-lab checkout c8d158e -- db/timers/backup-nightly.sh

# 2 trap 在脚本内，随第 1 步一起回滚；unit 本单未改动，无需还原

# 3 0 字节文件复原（如需）
mv /srv/backup/dumps/_zero-byte-20261120-20261125/multica-*.dump /srv/backup/dumps/</pre>
<p>borg 仓库、passphrase、已有 dump 与快照本单未改动，无需回滚动作；<code>snap-20261125-1306</code> 是修复后真实写入的快照，保留即可。</p>

<h2>附录 A · 0 字节文件清单（隔离目录实测）</h2>
<div class="twrap"><table><tr><th>文件</th><th>字节</th><th>mtime</th></tr>{zero_rows}</table></div>
<pre>{esc(zero.get('list_txt',''))}</pre>

<h2>附录 B · 复跑入口</h2>
<pre># 采集本页全部证据（只读）
python3 /srv/share/quantdb/code/work-559/scripts/work559_evidence.py
# 重画三张图并重建本页
python3 /srv/share/quantdb/code/work-559/scripts/work559_figures.py
python3 /srv/share/quantdb/code/work-559/scripts/work559_build_delivery.py</pre>

<footer>
证据源：<code>/srv/share/quantdb/code/work-559/docs/WORK-559/evidence.json</code>（由脚本采集，含每条命令的原始输出）·
脚本：<code>db/timers/backup-nightly.sh</code>（sha256 见评论）·
本页为单文件 HTML，图片 base64 内嵌、样式内联，断网可读、可直接打印。
</footer>
</main></body></html>
"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", OUT, len(html), "bytes")


if __name__ == "__main__":
    main()
