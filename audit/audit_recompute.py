# -*- coding: utf-8 -*-
"""
独立复算闸门（模型风控审计角色）

**审计契约（硬规定）**
1. 本脚本**禁止 import** 建模阶段（scripts/build_analysis.py 等）的任何函数、类或变量。
   数值一律用 `csv` 模块直读快照、用纯 Python 列表/字典重算。
2. 任何要出现在对外材料里的数字，必须能由本脚本在同一份快照上独立算出。
3. 判据先写死再跑；不改阈值让结论变绿。

审计对象：
  - 数据快照：data/<EXP>/A_tencent_*.csv（sha256 见 experiments/<EXP>/snapshot_manifest.json）
  - 交叉校验源：Tushare index_daily（服务端出口 IP，与本机源A 不同）
  - 图件 QA 产物：figures/<EXP>/*.alignment.json、*.collision-audit.json
  - 成品：experiments/<EXP>/report.html（合规扫描）

审计方法（不复用建模阶段的任何函数）：
  1. 用 csv 模块 + 纯 Python 重算 16 个对外数字，与声称值逐个比对；
  2. 用 Tushare 的独立收盘值重算 6 个关键点，验证源A 数值；
  3. 校验快照 sha256 与 manifest 一致（防快照漂移）；
  4. 校验图件 QA 产物：对齐审计 verdict、碰撞审计 verdict；
  5. 扫描最终 HTML 是否出现评级/目标价/收益承诺等禁止内容。

输出：experiments/<EXP>/audit_findings.json + 终端判决
退出码：0 = 无 blocking；1 = 存在 blocking

运行（**不需要任何环境变量**；脚本内已把 stdout/stderr 强制切到 UTF-8，
Windows 默认 GBK 控制台与「重定向到文件」两种情形都能直接跑）：
  python audit/audit_recompute.py --exp WORK-404
"""
import argparse
import csv
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# ---------------------------------------------------------------- 路径（仓库相对，无写死的任务目录）
EXP = os.environ.get("QUANTLAB_EXP", "WORK-404")
DATA = os.path.join(REPO, "data", EXP)
FIG = os.path.join(REPO, "figures", EXP)
OUT = os.path.join(REPO, "experiments", EXP)

findings = []


def add(sev, item, measured, expected, gap, where, fix):
    findings.append({"severity": sev, "item": item, "measured": measured, "expected": expected,
                     "gap": gap, "location": where, "how_to_verify": fix})


def series(code, data_dir):
    """独立读取：csv 模块，不用 pandas。返回 {date: close}, {date: volume}"""
    p = os.path.join(data_dir, f"A_tencent_{code}.csv")
    closes, vols = {}, {}
    with open(p, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row["date"][:10]
            closes[d] = float(row["close"])
            vols[d] = float(row["volume"])
    return closes, vols


def last_on_or_before(closes, d):
    ks = [k for k in closes if k <= d]
    if not ks:
        return None, None
    k = max(ks)
    return k, closes[k]


def ret(closes, d0, d1):
    k0, c0 = last_on_or_before(closes, d0)
    k1, c1 = last_on_or_before(closes, d1)
    return c1 / c0 - 1, k0, k1, c0, c1


def check(name, measured, claimed, tol=0.0005, unit=""):
    gap = measured - claimed
    ok = abs(gap) <= tol
    add("PASS" if ok else "BLOCKING", name, f"{measured:.6f}{unit}", f"{claimed:.6f}{unit}",
        f"{gap:+.6f}{unit}", "experiments/<EXP>/metrics.json", f"重跑 scripts/build_analysis.py 与本脚本比对 {name}")
    print(f"  [{'OK ' if ok else 'FAIL'}] {name:<42} 实测={measured:+.4f}{unit} 声称={claimed:+.4f}{unit} 差={gap:+.4f}")
    return ok


def _force_utf8_stdio():
    """把 stdout/stderr 显式切到 UTF-8。

    Windows 默认控制台编码是 GBK(cp936)。输出被重定向到文件或管道时，
    sys.stdout 会用 GBK 编码，脚本里任何非 GBK 字符（如 U+2212 数学减号）
    都会直接抛 UnicodeEncodeError，进程以 exit 1 收场——审计结论根本产不出来。

    在脚本内强制重设编码，使 `python audit/audit_recompute.py --exp <EXP>`
    **不需要任何环境变量**即可跑完，并返回真实退出码（0 = 无 blocking）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="独立复算（不复用建模阶段函数）")
    ap.add_argument("--exp", default=EXP, help="实验 ID")
    args = ap.parse_args()
    exp = args.exp
    data_dir = os.path.join(REPO, "data", exp)
    out_dir = os.path.join(REPO, "experiments", exp)
    fig_dir = os.path.join(REPO, "figures", exp)
    os.makedirs(out_dir, exist_ok=True)

    print("== 1. 独立重算对外数字（源：data/%s/A_tencent_*.csv，csv 模块直读）==" % exp)
    c1000, v1000 = series("sh000852", data_dir)
    c300, _ = series("sh000300", data_dir)
    c2000, _ = series("sz399303", data_dir)
    cred, _ = series("sh000922", data_dir)
    ck50, _ = series("sh000688", data_dir)
    call, _ = series("sh000985", data_dir)

    r, *_ = ret(c1000, "2026-09-30", "2026-10-30"); check("中证1000 2026-10 区间收益", r, -0.1559, unit="")
    r, *_ = ret(c300, "2026-09-30", "2026-10-30"); check("沪深300 2026-10 区间收益", r, -0.0854)
    r, *_ = ret(c1000, "2026-09-30", "2026-11-13"); check("中证1000 09-30→11-13", r, -0.1290)
    r, *_ = ret(c300, "2026-09-30", "2026-11-13"); check("沪深300 09-30→11-13（正文引用）", r, -0.0783)
    r1, *_ = ret(c1000, "2026-09-30", "2026-11-13")
    r2, *_ = ret(c300, "2026-09-30", "2026-11-13")
    check("10月以来 中证1000-沪深300（pp）", (r1 - r2) * 100, -5.07, tol=0.01, unit="pp")
    r, *_ = ret(c1000, "2026-10-30", "2026-11-13"); check("中证1000 11-01→11-13", r, 0.0318)
    r, *_ = ret(cred, "2026-06-30", "2026-07-31"); check("中证红利 2026-07", r, 0.1089)
    r, *_ = ret(ck50, "2026-06-30", "2026-07-31"); check("科创50 2026-07", r, -0.2590)
    r, *_ = ret(c2000, "2026-06-30", "2026-07-31"); check("国证2000 2026-07", r, -0.1946)
    r1, *_ = ret(c1000, "2026-06-30", "2026-07-31")
    r2, *_ = ret(cred, "2026-06-30", "2026-07-31")
    check("7月 中证1000-中证红利（pp）", (r1 - r2) * 100, -30.58, tol=0.01, unit="pp")
    r1, *_ = ret(c1000, "2026-09-30", "2026-10-30")
    r2, *_ = ret(c300, "2026-09-30", "2026-10-30")
    check("10月 中证1000-沪深300（pp）", (r1 - r2) * 100, -7.05, tol=0.01, unit="pp")
    # 隐含超额下界：私募指数 ≤ -17% 与基准收益之差
    check("C1 隐含超额下界（10月/中证1000）", (-0.17 - r1) * 100, -1.41, tol=0.01, unit="pp")
    check("C1 隐含超额下界（10月/国证2000）", (-0.17 - ret(c2000, "2026-09-30", "2026-10-30")[0]) * 100,
          -1.70, tol=0.01, unit="pp")
    # 成交额代理
    _, vall = series("sh000985", data_dir)
    tp_all = {d: vall[d] * call[d] for d in call}
    months = {}
    for d, val in tp_all.items():
        months.setdefault(d[:7], []).append(val)
    h1 = [sum(v) / len(v) for k, v in months.items() if "2026-01" <= k <= "2026-06"]
    h1m = sum(h1) / len(h1)
    oct_m = sum(months["2026-10"]) / len(months["2026-10"])
    check("成交额代理 H1 月均（万亿元）", h1m / 1e12, 8.90, tol=0.02, unit="万亿")
    check("成交额代理 2026-10 月均（万亿元）", oct_m / 1e12, 5.11, tol=0.02, unit="万亿")
    check("成交额代理 10月相对H1 变化", (oct_m / h1m - 1) * 100, -42.6, tol=0.3, unit="%")

    print("\n== 2. 源C（Tushare，服务端）独立点校验 ==")
    T = [("sh000852", "2026-09-30", 7298.8208), ("sh000852", "2026-10-30", 6161.2275),
         ("sh000300", "2026-09-30", 4357.6155), ("sh000300", "2026-10-30", 3985.501),
         ("sz399303", "2026-09-30", 9648.5035), ("sz399303", "2026-10-30", 8171.9678)]
    closes = {"sh000852": c1000, "sh000300": c300, "sz399303": c2000}
    for code, d, exp_v in T:
        got = closes[code][d]
        # 源A 快照点位保留 2 位小数、源C 为原始精度：判据 = 四舍五入半格（绝对差 ≤ 0.005）
        absd = abs(got - exp_v)
        rel = absd / exp_v
        ok = absd <= 0.005 + 1e-9
        add("PASS" if ok else "BLOCKING", f"跨源点值 {code} {d}", got, exp_v,
            f"绝对差 {absd:.4f}（相对差 {rel:.2e}）", "data_contracts/data_source_registry.md §4",
            "重跑 MCP tushare index_daily 同参数比对")
        print(f"  [{'OK ' if ok else 'FAIL'}] {code} {d} 源A={got} 源C={exp_v} 绝对差={absd:.4f} 相对差={rel:.2e}")

    print("\n== 3. 快照 sha256 与 manifest 一致性 ==")
    mpath = os.path.join(out_dir, "snapshot_manifest.json")
    man = json.load(open(mpath, encoding="utf-8"))
    checked = 0
    for it in man["items"]:
        if not it.get("ok"):
            add("SUGGESTION", f"数据源未取到：{it.get('name')}", it.get("error", ""), "—", "—",
                "data_contracts/data_source_registry.md §3", "见数据源登记表坑位清单")
            continue
        p = it["path"]
        if not os.path.isabs(p):
            p = os.path.join(REPO, p.replace("/", os.sep))   # manifest 存仓库相对路径
        h = hashlib.sha256(open(p, "rb").read()).hexdigest()
        ok = h == it["sha256"]
        checked += 1
        if not ok:
            add("BLOCKING", f"快照漂移：{os.path.basename(p)}", h[:16], it["sha256"][:16], "sha256 不一致",
                f"data/{exp}/", "重跑 scripts/fetch_market_data.py 并比对 manifest")
        print(f"  [{'OK ' if ok else 'FAIL'}] {os.path.basename(p):<34} sha256={h[:16]}")
    print(f"  校验快照数：{checked}")

    print("\n== 4. 图件 QA 产物（对齐 / 碰撞）==")
    for stem in ("fig1_schematic", "fig2_nav", "fig3_reproduce", "fig4_matrix_radar"):
        ap = os.path.join(fig_dir, f"{stem}.alignment.json")
        cp = os.path.join(fig_dir, f"{stem}.collision-audit.json")
        if os.path.exists(ap):
            a = json.load(open(ap, encoding="utf-8"))
            v = a.get("verdict", "")
            # 单面板图的对齐门按契约记 NOT APPLICABLE，属预期结果，不判 blocking
            ok = v in ("PASS", "NOT APPLICABLE")
            add("PASS" if ok else "BLOCKING", f"{stem} 面板对齐", v, "PASS / NOT APPLICABLE",
                a.get("summary", {}), f"figures/{exp}/", "重跑 audit_panel_alignment")
            print(f"  [{'OK ' if ok else 'FAIL'}] {stem} 对齐 verdict={v}")
        if os.path.exists(cp):
            c = json.load(open(cp, encoding="utf-8"))
            v = c.get("verdict", "")
            ok = v in ("PASS", "REVIEW REQUIRED")
            sev = "PASS" if v == "PASS" else ("SUGGESTION" if v == "REVIEW REQUIRED" else "BLOCKING")
            add(sev, f"{stem} 碰撞审计", v, "PASS", c.get("summary", {}), f"figures/{exp}/",
                "重跑 audit_figure_collisions.py")
            print(f"  [{'OK ' if ok else 'FAIL'}] {stem} 碰撞 verdict={v} {c.get('summary')}")

    print("\n== 5. 最终 HTML 禁止内容扫描 ==")
    rp = os.path.join(out_dir, "report.html")
    if os.path.exists(rp):
        html = open(rp, encoding="utf-8").read()
        banned = ["目标价", "买入", "卖出", "增持", "减持评级", "收益率承诺", "保本", "建议配置"]
        hits = [b for b in banned if b in html]
        # 允许出现在"免责"与"否定语境"里的词，需人工看上下文
        print(f"  命中词（需人工判读上下文）：{hits if hits else '无'}")
        need = ["内部研究模拟", "非持牌机构", "不构成投资建议"]
        miss = [n for n in need if n not in html]
        ok = not miss
        add("PASS" if ok else "BLOCKING", "首页三行免责", "缺：" + ",".join(miss) if miss else "齐全",
            "齐全", "；".join(miss), f"experiments/{exp}/report.html", "检查首页免责三行")
        print(f"  [{'OK ' if ok else 'FAIL'}] 首页免责三行：{'齐全' if ok else miss}")
    else:
        add("SUGGESTION", "最终 HTML 未生成", "report.html 不存在", "存在", "—",
            f"experiments/{exp}/", "先跑 scripts/build_report.py")
        print("  [SKIP] report.html 尚未生成")

    blocking = [f for f in findings if f["severity"] == "BLOCKING"]
    sugg = [f for f in findings if f["severity"] == "SUGGESTION"]
    verdict = "BLOCKING" if blocking else "PASS"
    out = {"verdict": verdict, "experiment": exp, "blocking": len(blocking), "suggestion": len(sugg),
           "pass": sum(1 for f in findings if f["severity"] == "PASS"), "findings": findings}
    with open(os.path.join(out_dir, "audit_findings.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n== 审计判决：{verdict}（PASS {out['pass']} / SUGGESTION {len(sugg)} / BLOCKING {len(blocking)}）==")
    for f in findings:
        if f["severity"] != "PASS":
            print(f"  {f['severity']}: {f['item']} | 实测={f['measured']} | 阈值={f['expected']} | 差距={f['gap']}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
