"""文案返工（2026-11-27 第 2 轮验收的 2 必改 + 2 nit）。只改文字/字段，不跑训练。"""
import io
import os

p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts",
                                 "work557_report_stage34.py"))
s = io.open(p, encoding="utf-8").read()


def rep(old, new):
    global s
    assert old in s, "NOT FOUND: " + old[:70]
    s = s.replace(old, new, 1)


# ---------- 必改 1：结论段（旧报价 3.4 h / 62 h 已被 10 轮实测推翻） ----------
rep('''② 修好后首次进入 stage-1 的实测把「要不要缩」变成了算术题：<b>单档 50 轮 ≈ 3.4 h &gt; 预注册停手闸 3 h</b>，
全量估算 <b>≈ 62 GPU 小时（2.6 天）</b>；''',
'''② 修好后把「要不要缩」量成了实测数（3 轮 / 10 轮 stage-1 真实循环，workers=2）：
<b>136.1 s/轮 → 单档 50 轮 = 1.89 h，<u>不越</u>预注册的 3 h 停手闸</b>；
全量按实测口径 <b>≈ 36.4 GPU 小时（1.5 天）</b>，早停口径 <b>15.3 h</b>，悲观上界 178 h。
（上一版写的「单档 3.4 h &gt; 3 h 闸、全量 62 h」是 50 步外推，<b>已被实测推翻并作废</b>，
差异来自冷启动被摊进前 50 步；三档口径并列在表 5 / 表 5b。）''')

# ---------- 必改 2：§7.2 摘要价格标签 ----------
rep('''而真正的省时装置是早停。从「缩窗 6 轮」（11 min）回到「全窗 + 早停」（1.07 h）是 <b>5.8×</b>的计算量 ——
这就是「可判性」的价格标签。</p>''',
'''而真正的省时装置是早停。从「缩窗 6 轮」（<b>6.2 min</b>）回到「全窗 + 早停」（<b>0.72 h</b>）是
<b>7.0×</b>的计算量 —— 这就是「可判性」的价格标签（两个数都按本轮实测 51 ms/batch 重算；
沉淀 v1 里的「11 min → 1.07 h = 5.8×」用的是被推翻的 90 ms，已作废）。</p>''')

# ---------- nit 3：§8.2 里仍用 62 h 的那句 ----------
rep('''<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 62 h 压到 &lt;16 h，仍要 ≥2 天。</li>''',
'''<li><b>换硬件</b>：≥4 张同代卡或一张显存足够、单步 ≥4× 快的卡 → 实测口径 36.4 h 压到 &lt;10 h，
早停口径 15.3 h 压到 &lt;4 h（也就无需占卡过夜）。</li>''')

io.open(p, "w", encoding="utf-8").write(s)
print("report generator patched")

# ---------- nit 3b：计时脚本硬编码的「8 worker spawn」 ----------
q = os.path.abspath(os.path.join(os.path.dirname(__file__), "work557_epoch_timing.py"))
t = io.open(q, encoding="utf-8").read()
old = '    ev("trainer built; fit() start (含 8 worker spawn)")'
assert old in t
t = t.replace(old, '    ev(f"trainer built; fit() start (num_workers={args.workers})")', 1)

# ---------- nit 4：s_per_batch_timestamps 删掉（口径含 spawn，容易被误读） ----------
old2 = '''        "s_per_batch_timestamps": round(fit_s / max(1, sum(ep)), 3) if ep else None,\n'''
assert old2 in t
t = t.replace(old2, "", 1)
io.open(q, "w", encoding="utf-8").write(t)
print("epoch_timing patched")

# ---------- 已在产物里的 JSON：补一句解释（v2 字段，向后兼容） ----------
import json
j = os.path.abspath(os.path.join(os.path.dirname(__file__), "throughput_bench_pl.json"))
d = json.load(io.open(j, encoding="utf-8"))
d.pop("s_per_batch_timestamps", None)
d["s_per_batch_steady_note"] = ("s_per_batch_steady = 稳态中位轮墙钟 / train batches(2675)；"
                                "分母只含 train 段，valid 段 486 batch 已摊在分子里，故该值略高于纯 train 步耗时。")
json.dump(d, io.open(j, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("json cleaned:", "s_per_batch_timestamps" in d, "| keys:", len(d))
