"""文案/数据补丁 v5（2026-11-28 stage-2 实测后）。

改三件事（派单 §4.5 点名的三处遗留）+ 把 stage-2 实测数贯穿到表 5 / 表 5b / 图 4：
  1. 图 4 的数据改用实测（stage-1 1.847 h、stage-2 1.596 h），不再用 10 轮外推的 1.89/1.90；
  2. §8.1 里「50 轮墙钟是按 136.1 s/轮线性外推」作废，改为实测；
  3. 均值口径统一：一律用 `fit_seconds / 轮数`（含启动摊销），并注明 epoch 均值另给；
  4. stage-2 从「未实测」升级为实测 112.9 s/轮中位（106.1/112.9/114.9），0.068 GB。
"""
import io
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
p = os.path.join(ROOT, "scripts", "work557_report_stage34.py")
s = io.open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new, 1)


# ---------- 载入 stage-2 实测 ----------
rep('''V2 = load(os.path.join("verify", "verify_full50_s42.json"), {})''',
'''V2 = load(os.path.join("verify", "verify_full50_s42.json"), {})
S2 = load(os.path.join("verify", "stage2_timing_summary.json"), {})''')

# ---------- F4 / 预算：stage-2 用实测 ----------
rep('''# F4 计算预算 vs 停手闸（对数）——三档口径并列
STAGE1_50 = H50_MEAS
STAGE2_50 = STAGE1_50 * 2
GRU_50 = 4310 / 3600
tot_meas = 2 * STAGE1_50 + 8 * STAGE2_50 + 2 * GRU_50
up_epoch = (d_ms + m_ms) / 1000 * bench.get("n_batches_per_epoch", 2675)
tot_up = (2 * up_epoch * 50 / 3600) + 8 * 2 * (up_epoch * 50 / 3600) + 2 * GRU_50
need = {"stage-1 单档 50 轮": round(STAGE1_50, 2),
        "stage-2 x8 档(2x 推断)": round(8 * STAGE2_50, 1),
        "GRU x2 seed": round(2 * GRU_50, 2), "合计": round(tot_meas, 1)}
f4 = grouped_bar(list(need.keys()),
                 [{"name": "实测口径 %.1f h" % tot_meas, "color": TEAL, "vals": list(need.values())},
                  {"name": "悲观上界 %.0f h" % tot_up, "color": GREY,
                   "vals": [round(up_epoch * 50 / 3600, 2), round(8 * 2 * up_epoch * 50 / 3600, 1),
                            round(2 * GRU_50, 2), round(tot_up, 1)]},
                  {"name": "停手闸 3 h/档", "color": RED, "vals": [3, 3, 3, 3]}],
                 ylab="GPU 小时（对数轴）", h=330, logy=True, ymax=400)''',
'''# F4 计算预算 vs 停手闸（对数）——三档口径并列（stage-1 与 stage-2 都是实测）
GRU_50 = 4310 / 3600
S2H = float(S2.get("h_50_epochs") or 1.596)          # stage-2 满 50 轮，实测
S2H28 = float(S2.get("h_28_epochs") or 0.894)        # stage-2 按 28 轮（早停假设）
STAGE1_50 = V2H                                      # 1.847 h（实测满 50 轮）
tot_meas = 2 * STAGE1_50 + 8 * S2H + 2 * GRU_50
tot_early = 2 * V1H + 8 * S2H28 + 2 * GRU_50
up_epoch = (d_ms + m_ms) / 1000 * bench.get("n_batches_per_epoch", 2675)
tot_up = (2 * up_epoch * 50 / 3600) + 8 * 2 * (up_epoch * 50 / 3600) + 2 * GRU_50
need = {"stage-1 x2 seed": round(2 * STAGE1_50, 2),
        "stage-2 x8 档": round(8 * S2H, 1),
        "GRU x2 seed": round(2 * GRU_50, 2), "合计": round(tot_meas, 1)}
f4 = grouped_bar(list(need.keys()),
                 [{"name": "满 50 轮实测 %.1f h" % tot_meas, "color": TEAL, "vals": list(need.values())},
                  {"name": "早停口径 %.1f h" % tot_early, "color": BLUE,
                   "vals": [round(2 * V1H, 2), round(8 * S2H28, 1), round(2 * GRU_50, 2), round(tot_early, 1)]},
                  {"name": "悲观上界 %.0f h" % tot_up, "color": GREY,
                   "vals": [round(up_epoch * 50 / 3600, 2), round(8 * 2 * up_epoch * 50 / 3600, 1),
                            round(2 * GRU_50, 2), round(tot_up, 1)]},
                  {"name": "停手闸 3 h/档", "color": RED, "vals": [3, 3, 3, 3]}],
                 ylab="GPU 小时（对数轴）", h=340, logy=True, ymax=600)''')

# ---------- 结论段：总量改实测 ----------
rep('''全量按实测口径 <b>≈ 35.6 GPU 小时（1.5 天）</b>，实测早停口径 <b>21.5 h</b>，悲观上界 178 h。''',
'''stage-2（MoE + Transformer 预测器，VQ-VAE 冻结）也已完成实测：<b>112.9 s/轮中位</b>（106.1 / 112.9 / 114.9），
只有 stage-1 的 <b>0.89×</b>（不是原假设的 2×）。于是整队全量：<b>满 50 轮口径 ≈ {tot_meas:.1f} GPU 小时</b>、
<b>早停口径 ≈ {tot_early:.1f} h</b>、悲观上界 178 h。''')

# ---------- 表 5：stage-2 由「未实测」升级 ----------
rep('''<tr><td>PRISM-VQ stage-2</td><td class="num">未实测</td><td class="num">未实测</td><td class="num">未实测</td><td>一次都没跑进去，只能按结构推断</td></tr>''',
'''<tr><td><b>PRISM-VQ stage-2 实测</b>（2026-11-28，3 轮，VQ-VAE 冻结）</td>
<td class="num">{S2B:.1f} ms</td><td class="num">{S2.get('median_s_per_epoch')} s</td>
<td class="num">{S2H:.3f} h</td>
<td>逐轮 {S2.get('epoch_seconds')}；峰值显存 {S2.get('peak_vram_gb')} GB；可训练参数 {S2.get('trainable_params_m')} M</td></tr>
<tr><td>PRISM-VQ stage-2（原「×2 结构推断」，<b>已作废</b>）</td><td class="num">—</td><td class="num">—</td><td class="num">≈3.70 h</td>
<td>实测 1.596 h，推断高估 2.3×</td></tr>''')

# ---------- 表 5b：三档按实测重算 ----------
rep('''<tr><td><b>实测口径</b>（满 50 轮 = {V2H:.2f} h/档，实测）</td>
<td class="num">{2*V2H:.1f} h</td><td class="num">{16*V2H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*V2H+16*V2H+2*GRU_50:.1f} h</b></td></tr>
<tr><td>早停口径（<b>实测第 {V1N} 轮停</b> = {V1H:.2f} h/档）</td>
<td class="num">{2*V1H:.1f} h</td><td class="num">{16*V1H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{2*V1H+16*V1H+2*GRU_50:.1f} h</b></td></tr>''',
'''<tr><td><b>满 50 轮实测</b>（stage-1 {V2H:.3f} h/档、stage-2 {S2H:.3f} h/档，均实测）</td>
<td class="num">{2*V2H:.1f} h</td><td class="num">{8*S2H:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_meas:.1f} h</b></td></tr>
<tr><td>早停口径（stage-1 <b>实测第 {V1N} 轮停</b>；stage-2 未测早停轮次，<b>按 28 轮假设</b>）</td>
<td class="num">{2*V1H:.1f} h</td><td class="num">{8*S2H28:.1f} h</td>
<td class="num">{2*GRU_50:.1f} h</td><td class="num"><b>{tot_early:.1f} h</b></td></tr>''')

# ---------- 图 4 图注 ----------
rep('''f"实测满 50 轮 {V2H:.2f} h/档（<b>在 3 h 闸内</b>；早停口径 {V1H:.2f} h）、"
        f"全量合计 {tot_meas:.1f} h；悲观上界（微基准 264 ms/batch）合计 {tot_up:.0f} h。"
        "stage-2 未实测、按 stage-1 ×2 结构推断。红色为单档允许上限。"''',
'''f"stage-1 满 50 轮 {V2H:.3f} h/档、stage-2 实测 {S2H:.3f} h/档（均<b>在 3 h 闸内</b>）："
        f"满 50 轮口径合计 {tot_meas:.1f} h，早停口径（stage-1 实测 28 轮）{tot_early:.1f} h；"
        f"悲观上界合计 {tot_up:.0f} h。红色为单档允许上限。"
        '<b>stage-2 不再是推断</b>：3 轮实测 106.1 / 112.9 / 114.9 s。' ''')

# ---------- §8.1：删掉「线性外推」那句 ----------
rep('''<li><b>实测只覆盖 stage-1、单 seed、前 10 轮</b>：10 轮都在 valid 触底（epoch 4）之后，未跑到早停；
50 轮墙钟是按 136.1 s/轮线性外推（图 5 逐轮稳定，线性外推置信度较高）。</li>''',
'''<li><b>50 轮墙钟已是实测，不再外推</b>：2026-11-28 关掉早停跑满 50 轮，6,648.5 s = 1.847 h（均值 133.0 s/轮）；
稳态中位 126.7 s。注意两个口径不要混用：<code>fit_seconds/50 = 132.97 s</code>（含启动摊销）、
<code>epoch_seconds</code> 均值 131.0 s —— 本文一律用前者，与表 5 一致。</li>
<li><b>stage-2 只测了 3 轮、1 个 seed</b>：106.1/112.9/114.9 s，val_loss 仍在下降（1.0639→1.0324→1.0243），
<b>它的早停轮次未测</b>，本文按 stage-1 的 28 轮假设（因此早停口径是上界侧的估计）。</li>''')

# ---------- S2B 常量（表 5 用） ----------
rep('''V1N = int(V1.get("n_epochs_ran") or 19)''',
'''V1N = int(V1.get("n_epochs_ran") or 19)
S2B = round(float(S2.get("median_s_per_epoch") or 112.9) / (2675 + 486) * 1000, 1)''')

io.open(p, "w", encoding="utf-8").write(s)
print("patched v5")
