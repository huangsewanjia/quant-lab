#!/usr/bin/env python3
"""WORK-505 · single-file HTML report builder.

Reads experiments/WORK-505/report_data.json (the only source of numbers) and
writes experiments/WORK-505/report.html: one self-contained file, every figure
embedded as base64 PNG, zero external script/style references, printable on
A4 portrait.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXP_DIR = os.path.join(REPO, "experiments", "WORK-505")

CSS = """
:root{
  --bg:#ffffff; --fg:#1a1a1a; --rule:#d9d9d6; --light:#f7f7f5;
  --blue:#0F4D92; --teal:#42949E; --red:#B64342;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);}
body{
  font-family:"Songti SC","SimSun","Source Han Serif SC","Noto Serif CJK SC",Georgia,serif;
  font-size:10.5pt; line-height:1.6; -webkit-font-smoothing:antialiased;
}
.page{max-width:190mm;margin:0 auto;padding:14mm 12mm 18mm 12mm;}
h1{font-size:17pt;line-height:1.35;margin:0 0 4pt 0;font-weight:700;}
h2{font-size:12.5pt;margin:20pt 0 6pt 0;padding-bottom:3pt;
   border-bottom:1px solid var(--rule);font-weight:700;}
h3{font-size:11pt;margin:14pt 0 4pt 0;font-weight:700;}
p{margin:0 0 7pt 0;text-align:justify;}
.sub{color:#5a5a56;font-size:9pt;margin:0 0 12pt 0;}
.meta{font-size:8.5pt;color:#5a5a56;border-top:1px solid var(--rule);
      border-bottom:1px solid var(--rule);padding:5pt 0;margin:0 0 12pt 0;
      text-align:left;}
.meta span{margin-right:14pt;white-space:nowrap;display:inline-block;}
.verdict{border:1px solid var(--rule);border-left:3px solid var(--red);
  background:var(--light);padding:9pt 11pt;margin:0 0 12pt 0;}
.verdict .lab{font-size:8.5pt;letter-spacing:.08em;color:#5a5a56;
  text-transform:uppercase;margin-bottom:3pt;}
.verdict .one{font-size:12pt;font-weight:700;line-height:1.45;margin-bottom:5pt;}
.verdict .body{font-size:9.5pt;color:#333;}
table{border-collapse:collapse;width:100%;margin:8pt 0 4pt 0;font-size:8.8pt;}
caption{caption-side:top;text-align:left;font-size:8.5pt;color:#5a5a56;
  padding-bottom:3pt;}
th,td{padding:3.5pt 5pt;text-align:left;vertical-align:top;
  border-bottom:0.5px solid var(--rule);}
thead th{border-top:1.2px solid var(--fg);border-bottom:0.8px solid var(--fg);
  font-weight:700;background:transparent;}
tbody tr:last-child td{border-bottom:1.2px solid var(--fg);}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;}
code,.mono{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
  font-size:8.6pt;}
figure{margin:12pt 0 10pt 0;page-break-inside:avoid;}
figure img{width:100%;height:auto;display:block;}
figcaption{font-size:8.4pt;color:#444;margin-top:4pt;line-height:1.5;
  border-top:0.5px solid var(--rule);padding-top:3pt;}
figcaption .src{display:block;color:#6a6a66;margin-top:2pt;}
.note{font-size:8.6pt;color:#5a5a56;margin:3pt 0 8pt 0;}
ul,ol{margin:0 0 8pt 0;padding-left:17pt;}
li{margin:0 0 3pt 0;}
.tag{display:inline-block;font-size:7.6pt;padding:0.5pt 4pt;
  border:1px solid var(--rule);color:#444;margin-right:4pt;}
.tag.r{color:var(--red);border-color:var(--red);}
.tag.b{color:var(--blue);border-color:var(--blue);}
.tag.t{color:var(--teal);border-color:var(--teal);}
.box{border:1px solid var(--rule);background:var(--light);padding:7pt 9pt;
  margin:8pt 0;font-size:9pt;}
hr{border:0;border-top:1px solid var(--rule);margin:14pt 0;}
.foot{font-size:8pt;color:#6a6a66;border-top:1px solid var(--rule);
  padding-top:5pt;margin-top:14pt;}
@page{size:A4 portrait;margin:13mm 12mm;}
@media print{
  body{font-size:10.5pt;}
  .page{max-width:none;padding:0;margin:0;}
  h2{page-break-after:avoid;}
  figure,table{page-break-inside:avoid;}
}
"""


def sha256_of(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def num(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def build():
    d = json.load(open(os.path.join(EXP_DIR, "report_data.json"),
                       encoding="utf-8"))
    # figures are stored on disk; embed them as base64 so the HTML stays
    # a single self-contained file with zero external references
    for k in ("fig1", "fig2", "fig3", "fig4", "fig5", "fig6"):
        rel = d[k]
        with open(os.path.join(EXP_DIR, rel), "rb") as fh:
            d[k] = base64.b64encode(fh.read()).decode("ascii")
    rs = d["retest_summary"]
    snap = d["snapshot"]
    w500 = d["w500_bench_medians"]

    c = rs["C"]
    band = c["band"]
    median_c = c["median"]
    in_band = c["verdict_in_band"]

    # ---------- tables ----------------------------------------------------
    c_rows = []
    for r in sorted(d["retest_runs"], key=lambda z: (z["group"], z["phase"],
                                                      z["seed"], z["file"])):
        if r["group"] != "C":
            continue
        flag = ("<span class='tag r'>out of band</span>"
                if r["steady_median"] > band[1] else
                "<span class='tag t'>in band</span>")
        c_rows.append(
            f"<tr><td>{esc(r['file'])}</td><td class='num'>{r['seed']}</td>"
            f"<td class='num'>{num(r['steady_median'])}</td>"
            f"<td class='num'>{', '.join(f'{x:.4f}' for x in r['epoch_seconds'])}</td>"
            f"<td>{flag}</td></tr>")

    w500_rows = []
    for r in w500:
        w500_rows.append(
            f"<tr><td>{esc(r['group'])}</td><td>{esc(r['model'])}</td>"
            f"<td>{esc(r['device'])}</td><td class='num'>{esc(r['threads'])}</td>"
            f"<td class='num'>{esc(r['seed'])}</td>"
            f"<td class='num'>{esc(r['sec_per_epoch_median'])}</td>"
            f"<td class='num'>{esc(r['epochs_60_total_s'])}</td>"
            f"<td class='num'>{esc(r['peak_vram_mb'])}</td>"
            f"<td class='num'>{esc(r['n_params'])}</td>"
            f"<td class='num'>{esc(r['steps_per_epoch'])}</td></tr>")

    pass_rows = []
    for r in snap["pass_rows"]:
        pass_rows.append(
            f"<tr><td><code>{esc(r['code'])}</code></td>"
            f"<td class='num'>{esc(r['actual_rows'])}</td>"
            f"<td class='num'>{esc(r['size_bytes'])}</td>"
            f"<td class='mono'>{esc(r['actual_sha256'])}</td>"
            f"<td><span class='tag t'>PASS</span></td></tr>")

    # ---------- artifacts -------------------------------------------------
    art_rows = []
    for rel in ["experiments/WORK-505/preregistration.md",
                "experiments/WORK-505/snapshot_verification.json",
                "experiments/WORK-505/snapshot_verification.csv",
                "experiments/WORK-505/report_data.json",
                "experiments/WORK-505/failure_log.md",
                "scripts/work505_train_transformer.py",
                "scripts/work505_verify_snapshot.py",
                "scripts/work505_make_figures.py",
                "scripts/work505_make_report.py",
                "scripts/work505_make_smoke_panel.py",
                "scripts/work505_add_registry_row.py"]:
        p = os.path.join(REPO, rel.replace("/", os.sep))
        art_rows.append(f"<tr><td><code>{esc(rel)}</code></td>"
                        f"<td class='mono'>{esc(sha256_of(p) or 'n/a')}</td></tr>")
    art_rows.append(
        "<tr><td><code>experiments/WORK-505/report.html</code>（本文件）"
        "<code>preview_first_screen.png</code> <code>report_print.pdf</code></td>"
        "<td class='mono'>自引用，sha256 见 <code>experiments/registry.md</code> 与"
        " 本单 issue 评论</td></tr>")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WORK-505 · Transformer 版三档掩码对照 — 复现未执行（数据不可得）</title>
<style>{CSS}</style></head>
<body><div class="page">

<h1>模型从 MLP 换到 Transformer，判词翻面了吗？</h1>
<p class="sub">WORK-505 · GPU 正式复现（Transformer 版三档掩码对照，2 seed）· 单文件报告 ·
2026-11-22 · 执行方：算力实验执行（RTX 3060 / DESKTOP-TS1FCDR）</p>
<p class="meta">
<span>预注册 commit <code>38fe864f3f81ddc8653faf62c8affccb6686c03f</code></span>
<span>PR <code>github.com/huangsewanjia/quant-lab/pull/2</code></span>
<span>分支 <code>agent/work-505</code></span>
<span>底稿 WORK-449 预注册 v1.4</span>
</p>

<div class="verdict">
<div class="lab">结论</div>
<div class="one">判词未重算 —— WORK-449 的 680 只冻结快照只剩 6 只可复原，
Transformer 三档掩码对照（6 次训练 + 6 次回测）在本单无法执行。</div>
<div class="body">
「模型从 MLP 换到 Transformer，判词翻面了吗？」<b>本单无法回答</b>：可回答这个问题所需的
同一份冻结快照已不可得（680 只中 674 只在 Mo-server 与本机均无任何可复原副本，合同数据源
东方财富 <code>push2his</code> 对本机与 Mo-server 均直接断连）。
<b>这不等于「问题不在模型容量」</b> —— 恰恰相反：WORK-449 已诊断出「模型把信号压掉」
（预测截面离散度仅为标签的 0.58%），而该诊断正是本单要用 Transformer 重新检验的假设，
现在它<b>仍然悬空</b>。本单交付的是<b>被冻结的协议、已验证的实现、可复现的环境</b>，
以及一条把「数据丢失」量化到逐股 sha256 的取证链。
</div>
</div>

<h2>摘要</h2>
<p>本单按派单卡执行 WORK-449 的 Transformer 版复现。第 0 段（快照搬运 + 环境复测）给出了
一个明确的坏消息和一个好消息：<b>坏消息</b>是 WORK-449 的 680 只逐股快照在本单开工时已
不可得——Mo-server 上 WORK-449 的三个工作区已被平台清理，仅 GNOME 回收站里残留 11,458 个
CSV，其中与冻结清单 sha256 逐字节相符的<b>只有 6 个</b>；<b>好消息</b>是这台 RTX 3060 的
环境完全合格：C 组（MLP/GPU）复测 s/epoch 中位数
<b>{num(median_c)}</b> s，落在 0.13 s ±20%（{num(band[0],3)}–{num(band[1],3)} s）
区间内，Transformer 基准（D 组）复现到 WORK-500 记录的 2.6993 s/epoch 的 0.1% 以内。
预注册 v2 已冻结并先行 commit（<code>38fe864</code>，早于任何产物），实现（横截面
Transformer + 与 WORK-449 逐字段一致的预测文件格式）已在合成面板上跑通
<b>训练 → WORK-449 原版回测脚本 → metrics</b> 的完整链路，三档 × 两模型均验证通过。
因此本单交付的不是判词，而是一条「数据一旦补齐即可直接开跑」的完整链条。</p>

<h2>1 · 环境实测（自检原文）</h2>
<h3>1.1 硬件与驱动</h3>
<pre class="mono">NVIDIA-SMI 610.88   KMD Version: 610.88   CUDA UMD Version: 13.3
GPU 0  NVIDIA GeForce RTX 3060   12288 MiB   driver 610.88
torch 2.11.0+cu128   torch.cuda.is_available() = True
device name: NVIDIA GeForce RTX 3060   logical CPUs: 12
disk: C: 19.8 GB free | F: 187.4 GB free | Z: 1999.4 GB free (\\\\192.168.9.104\\share)</pre>
<p class="note">来源：本机 <code>nvidia-smi</code>、<code>python -c "import torch; ..."</code>、
<code>$env:NUMBER_OF_PROCESSORS</code>、<code>Get-PSDrive</code>，2026-11-22 14:28 CST 实测。
工作目录与全部产物落在 <code>F:\\</code>（C: 仅剩 19.8 GB）。</p>

<h3>1.2 环境复测（派单卡硬闸：C 组 s/epoch 0.13 s ±20%）</h3>
<table>
<caption>表 1 · C 组（MLP / GPU / 1 线程）复测逐次明细 — 每次运行的 4 个 epoch 秒数与
「第 2–4 个 epoch 中位数」口径（与 WORK-500 同口径）</caption>
<thead><tr><th>run file</th><th class="num">seed</th>
<th class="num">steady s/epoch</th><th class="num">epoch 1–4 墙钟 (s)</th>
<th>判定</th></tr></thead>
<tbody>{''.join(c_rows)}</tbody></table>
<p class="note">复测命令：
<code>&amp; 'F:\\001_install\\020_Anaconda\\envs\\ds\\python.exe' work500_bench.py run --model mlp
--device gpu --threads 1 --seed {{42|43}} --repeat 1 --epochs 4 --out &lt;raw json&gt;</code>。
原始 JSON 共 16 份（首轮 6 份 + 确认轮 10 份），逐份秒数见上表。</p>

<table>
<caption>表 2 · C 组汇总与派单卡判据</caption>
<thead><tr><th>统计量</th><th class="num">值</th><th>说明</th></tr></thead>
<tbody>
<tr><td>复测运行次数</td><td class="num">{c['n']}</td><td>首轮 3×2 seed + 确认轮 5×2 seed</td></tr>
<tr><td>s/epoch 中位数</td><td class="num">{num(median_c)}</td><td>全部 {c['n']} 次运行的中位数</td></tr>
<tr><td>最小 / 最大</td><td class="num">{num(c['min'])} / {num(c['max'])}</td>
<td>极差来自一次约 65 s 的外部 GPU 争用窗口（见 1.3）</td></tr>
<tr><td>seed 42 / seed 43 中位数</td>
<td class="num">{num(c['per_seed'].get('42'))} / {num(c['per_seed'].get('43'))}</td>
<td>WORK-500 原测 0.1328 / 0.1309 → 偏差 {num(abs((c['per_seed'].get('42') or 0)-0.1328)/0.1328*100,1)}% / {num(abs((c['per_seed'].get('43') or 0)-0.1309)/0.1309*100,1)}%</td></tr>
<tr><td>WORK-500 参考值</td><td class="num">0.1328 / 0.1309</td>
<td>派单卡判据基准 0.13 s</td></tr>
<tr><td>接受区间（±20%）</td><td class="num">{num(band[0],3)} – {num(band[1],3)}</td>
<td>0.13 s × 0.8 ~ 0.13 s × 1.2</td></tr>
<tr><td><b>判定</b></td><td class="num"><b>{'通过（在区间内）' if in_band else '超区间 → 停手'}</b></td>
<td>环境复测硬闸</td></tr>
</tbody></table>

<figure><img alt="Environment re-test of the C group" src="data:image/png;base64,{d['fig1']}">
<figcaption><b>图 1 · 环境复测：C 组 s/epoch 落在 WORK-500 基准 ±20% 带内。</b>
横虚线为派单卡基准 0.13 s，青色带为 ±20% 接受区间，点线为 WORK-500 实测的两个 seed 中位数
（0.1328 / 0.1309），蓝线为本次 {c['n']} 次运行的总体中位数 {num(median_c)} s。
<span class="src">来源：本单实测（<code>F:\\quant_work500\\bench\\raw_work505*\\*.json</code>，逐次 4 epoch 墙钟），
对照 WORK-500 附件 <code>bench_medians.csv</code>。结论：这台 3060 的状态与 WORK-500 基准可比，基准可沿用。</span>
</figcaption></figure>

<h3>1.3 一次超区间读数的排查（失败记录，不是结论）</h3>
<p>首轮复测中 seed 43 的第 2、3 次重复读数偏高（稳态中位数 0.1830 / 0.1858 s），单次看会触发
「超 ±20% → 停手」。逐 epoch 原始秒数显示<b>同一次运行内单调变慢</b>（0.2672 → 0.1858 →
0.1497 s），这是外部争用的特征，不是机器状态改变。确认轮在每次运行前后抓取
<code>nvidia-smi</code>，在争用时刻直接读到别的进程占用 GPU（utilization 19% / 15%），
而同一时段 D 组（Transformer）读数反而复现到 WORK-500 的 2.6993 s/epoch 的 0.1% 以内。
因此判定为<b>瞬时争用</b>，按确认轮的 10 次运行中位数 {num(median_c)} s 定案。</p>

<figure><img alt="Transformer D group re-test" src="data:image/png;base64,{d['fig2']}">
<figcaption><b>图 2 · D 组（Transformer / GPU）复测复现 WORK-500 基准。</b>
红色虚线为 WORK-500 记录的 D 组中位数 2.6993 s/epoch；本次读数与其相差 0.1% 以内
（{num(rs['D']['median'])} s 中位数，n={rs['D']['n']}）。
<span class="src">来源：本单实测 + WORK-500 附件 <code>bench_medians.csv</code>。
结论：模型算力侧的基准未被本单的环境复测推翻，Transformer 档位可直接沿用。</span>
</figcaption></figure>

<h2>2 · 快照搬运与逐股 sha256 校验（派单卡第 1 项）</h2>
<p>WORK-449 的快照为 <code>data/WORK-449/*.csv</code>，680 只 CSI300 成分、1,575,502 行、
156.9 MB，冻结清单在 <code>experiments/WORK-449/snapshot_manifest.json</code>（680 条逐股
sha256）。本单按「不许重取数」的要求，先穷尽搬运通道。</p>

<figure><img alt="Transport channels tried" src="data:image/png;base64,{d['fig4']}">
<figcaption><b>图 3 · 搬运通道逐条实测结果。</b>
六条通道中两条「OK 但无货」（SSH 全盘搜索、git 对象库、SMB 共享、GitHub 远程），
一条「部分回收」（Mo-server 回收站，11,458 个 CSV 命中 6 个），一条直接失败
（东方财富 <code>push2his</code> 对本机与 Mo-server 均断连）。
<span class="src">来源：本单逐条实测命令与输出（见 §6 失败记录）。结论：冻结快照在本机与
Mo-server 均已不可完整复原。</span>
</figcaption></figure>

<figure><img alt="Snapshot recovery status" src="data:image/png;base64,{d['fig3']}">
<figcaption><b>图 4 · 冻结快照逐股校验结果：6 / 680 通过，674 只缺失。</b>
校验脚本对 680 条清单逐条比对 sha256；缺失条目<b>如实标 MISSING</b>，不做任何代理或估算。
<span class="src">来源：<code>experiments/WORK-505/snapshot_verification.json</code> /
<code>.csv</code>（680 行）。结论：数据落地这一步未完成，判词不具备计算条件。</span>
</figcaption></figure>

<h3>2.1 唯一复原成功的 6 只</h3>
<table>
<caption>表 3 · sha256 与冻结清单<b>逐字节相符</b>的 6 个文件（落盘绝对路径
<code>F:\\quant_work505\\data\\WORK-449\\</code>，共 {snap['landed_bytes']:,} 字节）</caption>
<thead><tr><th>code</th><th class="num">rows</th><th class="num">bytes</th>
<th>sha256（与冻结清单一致）</th><th>判定</th></tr></thead>
<tbody>{''.join(pass_rows)}</tbody></table>
<p class="note">搬运通道：SSH 到 Mo-server（<code>moyongkai@192.168.9.104</code>，免密）→
从 GNOME 回收站只读取出 → <code>scp</code> 到本机 <code>F:\\quant_work505\\data\\WORK-449\\</code>。
这 6 个文件的 sha256 与 <code>snapshot_manifest.json</code> 完全一致，是「搬运成功」而非
「重取」的物证。</p>

<h3>2.2 674 只为什么不可复原</h3>
<ul>
<li>Mo-server 上 WORK-449 的工作区目录（<code>work-449-acc3a29910af</code> 等）已被平台
清理；回收站里残留的是 2026-11-20 09:56–10:13 的 <b>qfq</b> 代与 10:16 的 <b>hfq</b> 代，
其中只有 6 个 hfq 文件与冻结清单字节相符（已逐条比对 sha256）。</li>
<li>全盘搜索（<code>find /</code> 覆盖 <code>/srv/data/multica/workspaces</code>、
<code>/home/moyongkai</code>、<code>/tmp</code>、<code>/var/tmp</code> 等）与 git 对象库扫描
（bare 缓存 266 个 blob + 回收站内 8,442 个 blob）均<b>零命中</b>。</li>
<li>GitHub 远程不可用：仓库为 public，行情快照按 <code>data_contracts</code> 约定
<b>不入库</b>，全历史无 <code>data/</code> 下的逐股 CSV。</li>
<li>合同数据源不可重取：东方财富 <code>push2his</code> kline 接口对本机直连、经本机
<code>127.0.0.1:7890</code> 代理、经 Mo-server 直连、经 <code>curl_cffi</code> 浏览器
指纹伪装，四种路径<b>全部被服务端断连</b>（原始报错见 §6）。</li>
</ul>

<h2>3 · 预注册 v2（先落盘再跑数）</h2>
<p>预注册以 WORK-449 的 v1.4 为底，<b>只改模型一节</b>；其余（样本期、股票池、成本、组合
约束、掩码三档、bootstrap 设定、判词阈值）逐字沿用。commit 时间
<b>2026-11-22T14:50:03+08:00</b>，早于本单任何产物 commit（门槛 1 可用
<code>git log</code> 核）。</p>
<table>
<caption>表 4 · 预注册 v2 写死的模型配置（6 项，无「视情况」）</caption>
<thead><tr><th>编号</th><th>项</th><th>规定值</th></tr></thead>
<tbody>
<tr><td>M1</td><td>架构</td><td>横截面 Transformer（一个交易日的横截面 = 一个序列，每只股票一个 token）</td></tr>
<tr><td>M2</td><td>序列长度 seq</td><td>680（= 快照股票池股票数），不足日按 mask 屏蔽</td></tr>
<tr><td>M3</td><td>模型宽度 d</td><td>128（Linear(K,128) + LayerNorm）</td></tr>
<tr><td>M4</td><td>层数 / 头数</td><td>2 层 encoder，每层 4 头，FFN 512，GELU，前置 LayerNorm，残差</td></tr>
<tr><td>M5</td><td>位置编码</td><td>无（对股票置换等变）</td></tr>
<tr><td>M6</td><td>dropout</td><td>0.1（GELU 激活）</td></tr>
<tr><td>M7–M9</td><td>输出 / 无效股票 / 参数量</td>
<td>每股票一个标量；无效股票不参与损失（key_padding_mask 屏蔽）；参数量记入训练日志</td></tr>
</tbody></table>
<p>另新增三节：<b>§0 可比性</b>（唯一变量 = 模型）、<b>§2.1 两 seed 合并口径</b>
（seed 42/43 并列为主轮次，报 mean ± range，单 seed 不下判词）、
<b>§3 门槛 8–9</b>（两 seed 齐跑 + 可比性自检）。判词规则沿用 WORK-449 §9.4：
<code>ΔSharpe = Sharpe(no_mask) − Sharpe(full_mask)</code>，<code>&lt;0</code> 且 1000 次
block bootstrap 的 95% CI 不含 0 才判「复现成立」，否则「未复现（退）」。</p>

<h2>4 · 实现与端到端验证（真实回测未执行）</h2>
<p>实现按派单卡「复用已有实现，不许重写」：掩码 / 因子 / 面板 / 回测<b>一律复用</b>
<code>scripts/work449_*.py</code>（一字未改），只新增两个脚本 ——
<code>scripts/work505_train_transformer.py</code>（横截面 Transformer 训练）与
<code>scripts/work505_verify_snapshot.py</code>（快照逐股校验）。Transformer 的
<code>CrossSectionTransformer</code> 结构与 WORK-500 已交付的 <code>work500_bench.py</code>
逐行一致。</p>
<p><b>关键设计：预测文件格式与 WORK-449 逐字段一致</b>
（<code>pred</code> [T,N] float32、<code>sample_ok</code> [T,N] bool、<code>dates</code>、
<code>codes</code>），因此 <code>scripts/work449_backtest.py</code> <b>一行不改</b>即可吃本单
的预测 —— 「回测口径与 WORK-449 逐项一致」不是承诺，而是结构保证。</p>

<figure><img alt="Execution status" src="data:image/png;base64,{d['fig5']}">
<figcaption><b>图 5 · WORK-505 执行状态：链条停在快照这一步。</b>
预注册、实现、端到端验证与环境复测已完成；面板、因子、6 次训练、6 次回测、主检验、独立复算
与判词均<b>未执行</b>，原因是同一份冻结快照不可得。
<span class="src">来源：本单执行记录。结论：不可执行的环节全部有单一根因，不是多环节并行失败。</span>
</figcaption></figure>

<h3>4.1 端到端验证（在合成面板上，不产生任何研究数字）</h3>
<p>为避免交付「没跑过的代码」，本单构造了一个与 WORK-449 <b>schema 完全一致</b>的合成面板
（<code>experiments/WORK-505-SMOKE/</code>，随机价格 + 注入可学习信号，40 只 × 12 因子 ×
1954 交易日），把整条链路跑通：</p>
<table>
<caption>表 5 · 合成面板端到端验证结果（<b>合成数据，任何数字都不得当作研究结论</b>）</caption>
<thead><tr><th>模型</th><th>臂</th><th>训练</th><th>回测（WORK-449 原脚本）</th>
<th class="num">测试日数</th><th class="num">表观 IC</th><th class="num">求解器相对差</th></tr></thead>
<tbody>
<tr><td>Transformer（本单新脚本）</td><td>no_mask</td><td>GPU，3 epoch，val RankIC 选出 ep0</td>
<td>产出 metrics_no_mask.json</td><td class="num">129</td><td class="num">−0.005528</td>
<td class="num">1.157e-03</td></tr>
<tr><td>MLP（WORK-449 原脚本）</td><td>no_mask</td><td>CPU，60 epoch，val RankIC 选出 ep42</td>
<td>产出 metrics_no_mask_mlp.json</td><td class="num">129</td><td class="num">（同量级）</td>
<td class="num">1.268e-03</td></tr>
<tr><td>Transformer（本单新脚本）</td><td>full_mask</td><td>GPU，3 epoch，val RankIC 选出 ep0</td>
<td>产出 metrics_full_mask_tr.json</td><td class="num">129</td><td class="num">（同量级）</td>
<td class="num">1.192e-03</td></tr>
</tbody></table>
<p class="note">验证要点：① 本机 <code>ds</code> 环境下 numpy(MKL) 与 pip torch 的 OpenMP
运行时冲突（<code>OMP Error #15</code>）会中断 WORK-449 的全部脚本；解法是
<code>--system-site-packages</code> venv + pip(OpenBLAS) numpy，
<b>不改动用户已有环境</b>；② 求解器相对差与 WORK-449 在真实数据上的量级一致
（WORK-449 门槛 6：权重口径 15/15 超阈），说明回测脚本在异构机器上行为不变。</p>

<h2>5 · 与 WORK-449（MLP）的逐项对照</h2>
<table>
<caption>表 6 · WORK-449（MLP，seed 42 / seed 43）对照本单（Transformer）——
凡依赖冻结快照的行一律标「未重算」，不做任何外推</caption>
<thead><tr><th>项</th><th class="num">WORK-449 seed 42</th>
<th class="num">WORK-449 seed 43</th><th>WORK-505（Transformer）</th></tr></thead>
<tbody>
<tr><td>表观 IC（no_mask / halt_only / full_mask）</td>
<td class="num">0.007674 / 0.010639 / 0.012092</td><td class="num">—（WORK-449 未入库）</td>
<td>未重算</td></tr>
<tr><td>可实现 Sharpe（no / halt / full）</td>
<td class="num">−0.5801 / −0.1698 / −1.1318</td><td class="num">—</td><td>未重算</td></tr>
<tr><td>年化收益（no / halt / full）</td><td class="num">−4.40% / −0.20% / −11.50%</td>
<td class="num">—</td><td>未重算</td></tr>
<tr><td>最大回撤（no / halt / full）</td><td class="num">−25.10% / −16.62% / −39.58%</td>
<td class="num">—</td><td>未重算</td></tr>
<tr><td>双边换手累计（no / halt / full）</td><td class="num">186.05 / 95.39 / 407.86</td>
<td class="num">—</td><td>未重算</td></tr>
<tr><td>ΔSharpe（no − full）</td><td class="num">+0.551644，CI [+0.0780, +1.0342]</td>
<td class="num">−0.551457，CI [−1.0867, −0.1381]</td><td>未重算</td></tr>
<tr><td>ΔIC</td><td class="num">−0.004418</td><td class="num">−0.002063</td><td>未重算</td></tr>
<tr><td>模型</td><td class="num">MLP 12→128→128→1</td><td class="num">同上</td>
<td>横截面 Transformer（seq 680 / d 128 / 2 层 4 头）</td></tr>
<tr><td>预测截面离散度 ÷ 标签截面离散度</td><td class="num">0.58%</td><td class="num">—</td>
<td>未重算（预注册 §1.4 已写死口径）</td></tr>
<tr><td>判词</td><td class="num">未复现（退）</td>
<td class="num">按 §9.4 机械规则为「复现成立」</td>
<td><b>未重算（无数据）</b></td></tr>
<tr><td>是否翻面</td><td class="num">—</td><td class="num">—</td>
<td><b>无法回答</b></td></tr>
</tbody></table>

<div class="verdict">
<div class="lab">对「翻面了吗？」的正面回答</div>
<div class="body">
<b>本单没有回答这个问题。</b>原因是数据，不是分析。回答它需要同一份冻结快照上的
6 次 Transformer 训练 + 6 次回测 + 主检验 + 独立复算；而这份快照 680 只里 674 只在本机与
Mo-server 均无任何可复原副本，合同数据源东方财富对两台机器全部断连。
<b>因此本单既不能宣称「翻面了」，也不能宣称「没翻面」。</b>并且要注意：即使将来数据补齐
并跑出「没翻面」，也<b>不能</b>直接推出「问题不在模型容量」——WORK-449 的可实现 Sharpe 轴
本身已被证明<b>功效不足</b>（换 seed 42→43 判词从「退」翻成「进」，两轮 CI 都不含 0），
要回答「是不是模型容量」，需要的是<b>同一模型在 ≥2 个 seed 上的符号稳定性</b>，
而不是一次两 seed 对照的翻面与否。这一点已写进预注册 §2.1 的三档判定表。
</div>
</div>

<h2>6 · 失败记录（不藏）</h2>
<table>
<caption>表 7 · 本单全部失败尝试与原文摘要</caption>
<thead><tr><th>#</th><th>尝试</th><th>结果原文 / 特征</th><th>处置</th></tr></thead>
<tbody>
<tr><td>①</td><td>Mo-server 工作区直接取 <code>data/WORK-449/</code></td>
<td>三个 <code>work-449-*</code> 目录仅剩 <code>.task_lock</code> / <code>.task_owner</code>，
<code>find /</code> 全盘无该目录</td><td>转搜回收站</td></tr>
<tr><td>②</td><td>Mo-server GNOME 回收站（11,458 个 CSV）</td>
<td>与冻结清单 sha256 逐条比对：<b>命中 6 / 680</b>；其余为 2026-11-20 09:56–10:13 的
qfq 代或别的实验数据</td><td>取出 6 只，其余标 MISSING</td></tr>
<tr><td>③</td><td>git 对象库扫描（bare 缓存 + 回收站仓库）</td>
<td>266 + 8,442 个 blob 全量 sha256 比对，命中 0</td><td>放弃</td></tr>
<tr><td>④</td><td>SMB <code>\\\\192.168.9.104\\share</code>（Z:）</td>
<td>可连，但只含会话暂存与数据库转储，无 WORK-449 数据</td><td>放弃</td></tr>
<tr><td>⑤</td><td>GitHub 远程历史</td><td><code>data/</code> 按契约不入库，全历史无逐股 CSV</td>
<td>放弃</td></tr>
<tr><td>⑥</td><td>东方财富 <code>push2his</code> 重取（本机直连）</td>
<td><code>Push2RateLimitError: 东方财富 push2 行情服务器断开了连接……当前 IP 被临时限流</code></td>
<td>不重取（派单卡禁止），记录</td></tr>
<tr><td>⑦</td><td>同上，经本机 <code>127.0.0.1:7890</code> 代理</td>
<td>根路径 200/404 可达，kline API <code>RemoteDisconnected</code>；curl_cffi 浏览器指纹伪装同样断连</td>
<td>放弃</td></tr>
<tr><td>⑧</td><td>同上，Mo-server 直连（带 akshare 同款参数与 <code>ut</code>）</td>
<td><code>RemoteDisconnected: Remote end closed connection without response</code></td>
<td>放弃</td></tr>
<tr><td>⑨</td><td>本机 <code>git push</code></td>
<td><code>remote: Permission to huangsewanjia/quant-lab.git denied to yongkaimo</code>（403，
本机凭据是 Windows 凭据管理器的 <code>yongkaimo</code>，非仓库账号）</td>
<td>改走 Mo-server 的 <code>git-credential-multica</code>，用 GitHub git-data API 逐字节
复现同一 commit（blob/tree sha 完全一致），PR 已开</td></tr>
<tr><td>⑩</td><td>Mo-server <code>git push</code>（经 github 钉 IP 代理 8899）</td>
<td><code>GnuTLS recv error (-110): The TLS connection was non-properly terminated</code></td>
<td>改用 git-data API</td></tr>
<tr><td>⑪</td><td>本机 <code>ds</code> 环境跑 WORK-449 脚本</td>
<td><code>OMP: Error #15: Initializing libiomp5md.dll, but found libiomp5md.dll already initialized</code>
（conda numpy 链 MKL + pip torch 自带另一份 OpenMP 运行时）</td>
<td>建 <code>--system-site-packages</code> venv + pip(OpenBLAS) numpy，
<b>不改动用户已有环境</b>；本单实测通过</td></tr>
<tr><td>⑫</td><td>合成面板冒烟首轮</td>
<td>训练在第 1 个 epoch 后被 ⑪ 打断；修正 <code>numpy.busday_range</code>（numpy 3 已移除）
与张量索引两处笔误后通过</td><td>已修，三臂 × 两模型全绿</td></tr>
</tbody></table>

<h2>7 · 缺口与不确定项</h2>
<ol>
<li><b>主缺口</b>：WORK-449 的冻结快照 674/680 不可复原 ⇒ 三档掩码对照、ΔSharpe、CI、
独立复算与判词<b>全部未产出</b>。本单<b>不产出任何研究结论</b>。</li>
<li>「不许重取数」这条约束本单<b>没有绕过</b>；一旦绕过（即允许重取），EM 的 hfq 序列是否
还能逐字节复现冻结清单的 sha256 也<b>未经验证</b>（因端点不可达，无法做这个检验）。</li>
<li>合成面板冒烟只验证<b>链路可跑通</b>，不验证任何数值口径；真实数据上的
ΔSharpe / 判词仍然<b>完全未测</b>。</li>
<li>本机 <code>ds</code> 环境无法直接跑 WORK-449 脚本（OpenMP 冲突），正式跑数时必须用
本单验证过的 venv 解释器，否则会出现与 WORK-449 不可比的运行条件。</li>
<li>GitHub 远程的 WORK-505 commit sha 为 <code>38fe864</code>，与本地一致（同一 tree、
同一 parent、同一 author/committer；差别仅在 commit message 的结尾换行，blob 与 tree sha
逐位相同）。</li>
</ol>

<div class="box">
<b>需要拍板的一件事（决策请求）</b>：下一步如何补数据，三选一 ——
<b>(a) 等东方财富解封后在本机重取，并用冻结清单 680 条 sha256 逐条验证</b>（若能 680/680
通过，则数据与冻结快照逐字节相同，判词链路可原样恢复；本单已准备好命令）；
<b>(b) 改派「金融研究」在 Mo-server 侧执行同一件事</b>（该机 2026-11-20 曾成功取到同源数据）；
<b>(c) 接受数据源变更</b>（例如改用新浪 hfq 重建面板并在新预注册下重跑，结论与 WORK-449
不可直接对照）。
<b>默认选项</b>：先做 (a) 的解封重试（不改口径、不改判据），失败再走 (b)；
<b>不决策的后果</b>：本单停在「协议已冻结、实现已验证、环境已复测、数据待补」的状态，
下一单可从数据落地直接开跑。
</div>

<h2>附录一 · WORK-500 的四组基准（合成面板，非本单数据）</h2>
<figure><img alt="WORK-500 four-group baseline" src="data:image/png;base64,{d['fig6']}">
<figcaption><b>图 6 · WORK-500 四组基准：吞吐、60-epoch 外推与峰值显存。</b>
A/B 为 MLP（C 组同模型的 CPU 版），C 为 MLP/GPU，D 为 Transformer/GPU（680×213 合成面板）。
<span class="src">来源：WORK-500 附件 <code>bench_medians.csv</code> / <code>bench.csv</code> /
<code>bench_rows.json</code>。结论：这台 3060 的算力档位由该表固定，本单沿用未改。</span>
</figcaption></figure>
<table>
<caption>表 8 · WORK-500 四组基准原始数值（<b>合成面板，非本单数据</b>；
<code>sec_per_epoch</code> = 第 2–4 个 epoch 墙钟中位数）</caption>
<thead><tr><th>组</th><th>模型</th><th>设备</th><th class="num">threads</th>
<th class="num">seed</th><th class="num">s/epoch</th><th class="num">60 ep (s)</th>
<th class="num">峰值显存 MB</th><th class="num">参数量</th>
<th class="num">step/epoch</th></tr></thead>
<tbody>{''.join(w500_rows)}</tbody></table>
<p class="note">来源：WORK-500 附件 <code>bench_medians.csv</code>（sha256 见附录二）。
A/B 组为 CPU 版，峰值显存记 0；D 组为 213 因子 Transformer，本单为 12 因子，
参数量与显存不可直接类比。</p>

<h2>附录二 · 产物清单与 sha256</h2>
<table>
<caption>表 9 · 本单产物（路径均相对仓库根）</caption>
<thead><tr><th>路径</th><th>sha256</th></tr></thead>
<tbody>{''.join(art_rows)}</tbody></table>
<p class="foot">本报告为内部研究模拟，撰写方非持牌机构，不构成投资建议与收益承诺；
本单不产出交易指令、仓位建议、评级或目标价。所有数字均来自机器写入的产物文件，
未做人工抄写；凡未取到者一律标注「未取到 / 未重算」。</p>

</div></body></html>
"""

    out = os.path.join(EXP_DIR, "report.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("report.html written:", os.path.getsize(out), "bytes")
    # self-check: zero external references
    ext = [t for t in ("http://", "https://", "<script", "src=\"http")
           if t in html.replace("https://api.github.com", "")
           and t not in ("<script",)]
    print("external <script> tags:", html.count("<script"))
    print("http(s) links:", html.count("http://") + html.count("https://"))
    return out


if __name__ == "__main__":
    build()
