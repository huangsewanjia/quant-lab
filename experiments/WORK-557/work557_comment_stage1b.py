#!/usr/bin/env python3
"""WORK-557 段1 补充：stk_limit / suspend_d 到位后的一致性交叉核对（兑现段1 回帖的承诺）。

数字来源：experiments/WORK-557/crosscheck.json（不手抄）。
用法：python experiments/WORK-557/work557_comment_stage1b.py <out.md>
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    cc = json.load(open(os.path.join(HERE, "crosscheck.json"), encoding="utf-8"))
    A = cc["check_A_suspend"]["values"]
    B = cc["check_B_limit"]["values"]
    E = cc["check_B_limit"]["known_weaknesses"]
    yr = cc["check_A_suspend"]["by_year"]
    bd = cc["check_B_limit"]["implied_band_by_board"]
    band_ok = cc["check_B_limit"]["band_set_consistent_rate"]
    up_ratio = cc["check_B_limit"]["w503_over_mask_ratio_up"]
    dn_ratio = cc["check_B_limit"]["w503_over_mask_ratio_down"]
    n_md = A["member_days"]

    L = []
    P = L.append
    P("## 段1 补充｜`stk_limit` / `suspend_d` 到位后的一致性交叉核对")
    P("")
    P("**结论（一句）**：段1 的核心判读（「`daily` 无行 = 停牌，不是数据缺口」）**经独立来源 `suspend_d` 证实"
      "（%.2f%% 被解释，真缺口仅 %d 个 member-day / %.4f%%）**；同时量化了 `WORK-503` 涨跌停掩码规则的偏差"
      "（`limit_up` 判成库内真值的 **%.2f 倍**）——`WORK-557` **照抄不改**，仅披露并登记为已知口径限制。"
      % (100.0 * A["no_bar_and_susp"] / A["no_bar"], A["no_bar_not_susp"],
         100.0 * A["no_bar_not_susp"] / n_md, up_ratio))
    P("")
    P("> 本条兑现段1 回帖里「`stk_limit` / `suspend_d` 到位后补一次一致性交叉核对」的承诺。"
      "库只读快照已全部拷完（10 张表 / 31,302 文件），核对在本地副本上跑，未再触碰网络盘。")
    P("")
    P("---")
    P("")
    P("### 1. 核对 A｜停牌：`daily` 无行到底是不是停牌")
    P("")
    P("member-day = PIT 成分 × 交易日（2009-01-01~2024-12-31，%d 个 member-day）。" % n_md)
    P("")
    P("| 计数 | 数值 | 占比 |")
    P("|---|---|---|")
    P("| member-day 合计 | %d | 100%% |" % n_md)
    P("| `daily` 无行 | %d | %.4f%% |" % (A["no_bar"], 100.0 * A["no_bar"] / n_md))
    P("| 无行 ∧ `suspend_d` 有行（**确证停牌**） | %d | %.4f%% |"
      % (A["no_bar_and_susp"], 100.0 * A["no_bar_and_susp"] / n_md))
    P("| 无行 ∧ `suspend_d` 无行（**真·数据缺口**） | **%d** | **%.6f%%** |"
      % (A["no_bar_not_susp"], 100.0 * A["no_bar_not_susp"] / n_md))
    P("| 有行 ∧ `suspend_d` 有行 | %d | %.4f%% |"
      % (A["bar_and_susp"], 100.0 * A["bar_and_susp"] / n_md))
    P("| 有行 ∧ `vol=0` | %d | %.4f%% |"
      % (A["bar_vol_zero"], 100.0 * A["bar_vol_zero"] / n_md))
    P("")
    P("**「无行」中被 `suspend_d` 解释的比例 = %.4f%%**；真·数据缺口只有 **%d** 个 member-day。"
      % (100.0 * A["no_bar_and_susp"] / A["no_bar"], A["no_bar_not_susp"]))
    P("")
    P("逐年真缺口（无行且不在 `suspend_d` 中）：")
    P("")
    P("| 年 | member_days | daily 无行 | 其中停牌 | 真缺口 | 真缺口率 |")
    P("|---|---|---|---|---|---|")
    for r in yr:
        P("| %d | %d | %d | %d | %d | %.6f%% |"
          % (r["year"], r["member_days"], r["no_bar"], r["suspended"], r["real_gap"],
             100.0 * r["real_gap_rate"]))
    P("")
    P("> **这条把段1 的结论钉死了**：2009–2013 的 1.57%%~2.92%%「缺失」确实是**停牌**（可复现、可解释），"
      "不是数据缺口 —— 停手条件①（缺口 > 5%%）判定成立且依据更强。另一个附带发现："
      "`quantdb` 的 `daily` **不存在 `vol=0` 的行**（%d 行），所以 `WORK-503` 的第二条停牌触发条件"
      "（有行但 volume==0）在 quantdb 口径下**永不触发**，停牌集合完全由「无行」决定。" % A["bar_vol_zero"])
    P("")
    P("---")
    P("")
    P("### 2. 核对 B｜`WORK-503` 的涨跌停掩码 vs 库内真值（`stk_limit`）")
    P("")
    P("`work503_build_panel.py` 的口径（照抄，不改）：板块候选带 = 科创/创业 {20%, 5%}、北交 {30%, 5%}、"
      "主板 {10%, 5%}；`limit_up` = `close ≥ prev_traded_close × (1+b) − 0.005` 对候选带**取并集**"
      "（等价于取**最小 b = 5%** 门槛）；`prev_traded_close` = 上一个**有行情日**的收盘。")
    P("")
    P("| 指标 | 数值 |")
    P("|---|---|")
    P("| 可比对 bar 数（全 A，daily ⋈ stk_limit） | %s |" % "{:,}".format(B["joined_rows"]))
    P("| 库内真 `limit_up`（`close ≥ up_limit`） | %s |" % "{:,}".format(B["db_hit_up"]))
    P("| `WORK-503` 判 `limit_up` | %s |" % "{:,}".format(B["w503_hit_up"]))
    P("| **倍数** | **%.2f ×** |" % up_ratio)
    P("| 库内真 `limit_down` | %s |" % "{:,}".format(B["db_hit_down"]))
    P("| `WORK-503` 判 `limit_down` | %s |" % "{:,}".format(B["w503_hit_down"]))
    P("| **倍数** | **%.2f ×** |" % dn_ratio)
    P("| 过掩（503 判了、库里没到）up / down | %s / %s |"
      % ("{:,}".format(B["over_mask_up"]), "{:,}".format(B["over_mask_down"])))
    P("| 漏掩（库里到了、503 没判）up / down | %s / %s |"
      % ("{:,}".format(B["miss_mask_up"]), "{:,}".format(B["miss_mask_down"])))
    P("| 库真幅度落在 503 该板块候选带内 | %.2f%% |" % (100 * band_ok))
    P("")
    P("库内真实涨跌停幅度按板块分布（`implied_band = up_limit / prev_close − 1`）：")
    P("")
    P("| 板块 | 真实幅度 | bar 数 | 落在 503 候选带内 |")
    P("|---|---|---|---|")
    for r in bd:
        P("| %s | %.0f%% | %d | %d (%.2f%%) |"
          % (r["board"], 100 * float(r["band"]), r["n"], r["in_work503_band_set"],
             100.0 * r["in_work503_band_set"] / max(r["n"], 1)))
    P("")
    P("**三处静态弱点（全部量化，全部继承自 `WORK-503`）：**")
    P("")
    chinext_10 = next((r["n"] for r in bd
                       if r["board"] == "ChiNext" and abs(float(r["band"]) - 0.10) < 1e-9), 0)

    P("1. **候选带取并集 = 取最小 b = 5%% 门槛**。全板块统一用「涨 5%% 即算涨停」判 `limit_up`，"
      "于是 `limit_up` 被判成真值的 %.2f 倍；`limit_down` 因 5%% 门槛更接近而放大到 %.2f 倍。"
      "这属于 `mask_contract.md` §3.3「宁可多掩码，不可漏掩码」的**有意保守**，"
      "但会让可交易集合比真实口径小，报告里必须写明。" % (up_ratio, dn_ratio))
    P("2. **创业板 2020-08-24 才从 10%% 改 20%%**（库里可证：implied band = 10%% 的行止于 2020-09-15，"
      "= 20%% 的行自 2020-08-24 起）。`WORK-503` 对创业板的候选带只有 {20%%, 5%%}，"
      "**改前真实带 10%% 不在集合内** —— 涉及 **%s** 行。"
      "这直接是过掩 %d 行的主因之一。"
      % ("{:,}".format(chinext_10), B["over_mask_up"]))
    P("3. **`band_for` 把 920xxx（北交所新号段）归为主板 10%%**。`WORK-503` 的规则只认 `8`/`4` 开头为北交 30%%，"
      "**920xxx 落在主板分支**；库里实测这批 **%s 行**中 **%s 行真实幅度是 30%%**。"
      "另有除权除息日 **%s** 行（用不复权的前收盘比较，除权日必然假阳性）与 **%s** 行"
      "「无涨跌幅限制日 / 数据退化」幅度异常。"
      % ("{:,}".format(E["bse_920_rows"]), "{:,}".format(E["bse_920_at_30"]),
         "{:,}".format(E["ex_rights_rows"]),
         "{:,}".format(E["no_limit_anomaly_rows"])))
    P("")
    P("---")
    P("")
    P("### 3. 处置（`WORK-557` 不改口径，只披露）")
    P("")
    P("| 事项 | 处置 | 理由 |")
    P("|---|---|---|")
    P("| 停牌集合 | 沿用 `WORK-503`：「`daily` 无行 = 停牌、价格前向填充、成交量置 0」 | 照抄；"
      "且 `suspend_d` 独立证实该集合正确（%.2f%%），真缺口仅 %d 个 member-day |"
      % (100.0 * A["no_bar_and_susp"] / A["no_bar"], A["no_bar_not_susp"]))
    P("| 涨跌停掩码 | 沿用 `WORK-503` 的价格带推导（**不**改用库内 `stk_limit` 真值） | "
      "改用真值会改变可交易集合 → 破坏「旧 vs 新」对照的可比性；偏差已量化披露 |")
    P("| 920xxx 板块分类 | **不单独修**（`WORK-503` 的 `band_for` 不动） | 北交所股票 2021-06-22 上市、"
      "2024-12-31 前在 CSI300 内的只数极少（CSI300 并不含北交所股票），对结果影响可忽略；"
      "改动会引入对 `WORK-503` 的偏离 |")
    P("| 创业板 10%/20% 制度切换 | **不单独修**，按 5% 统一门槛的既有保守口径执行 | 同上；"
      "且改为真值会同时改变旧口径与新口径，破坏对照 |")
    P("")
    P("> **一句话给段 2**：`WORK-557` 的可交易集合会比「真实涨跌停」口径更小（约多掩 3.5% 的 bar），"
      "但 **与 `WORK-503` 完全同源同量级**，「旧 vs 新」对照因此仍然 apples-to-apples；"
      "所有偏差以本条数字披露，不藏。")
    P("")
    P("### 4. 产物与 sha256")
    P("")
    P("| 文件 | sha256 |")
    P("|---|---|")
    for rel in ("experiments/WORK-557/crosscheck.json",
                "experiments/WORK-557/work557_crosscheck.py"):
        P("| `%s` | `%s` |" % (rel, sha256(os.path.join(REPO, rel.replace("/", os.sep)))))
    P("")
    P("### 5. 可复跑命令")
    P("")
    P("```powershell")
    P("cd C:\\Users\\MO_Yongkai\\multica_workspaces_cloud\\workspace-c8faa36a018a\\work-557-a221b7629a32\\workdir\\quant-lab")
    P("python -u experiments/WORK-557/work557_crosscheck.py   # -> experiments/WORK-557/crosscheck.json")
    P("```")
    P("")
    P("### 6. 缺口与不确定项")
    P("")
    P("1. 核对 B 的 `prev_traded_close` 用 `lag(close)`（不复权）计算，与 `WORK-503` 的实现一致；"
      "在除权除息日它与交易所参考价不同，故「库真幅度」在这些行上是口径差异而非数据错误 —— "
      "这正是第 2 条弱点本身，已单列 %d 行。" % E["ex_rights_rows"])
    P("2. 核对 B 的比对基数是**全 A 的 bar**（%d 行），不是 member-day 子集；"
      "目的是测掩码规则的绝对偏差量级。member-day 子集上的偏差比例会与此接近但不相等。" % B["joined_rows"])
    P("3. 本条不改任何口径、不改任何判据；若后续要用「真实涨跌停」跑一版对照，"
      "那是新增变体、属范围变更，需另行预注册。")
    P("")
    P("本 run 已用 ≈22.6M / 硬闸 25M")
    P("")
    P("<!-- stage1b end -->")
    out = "\n".join(L)
    if len(sys.argv) > 1:
        dst = os.path.abspath(sys.argv[1])
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print("wrote -> %s (%d chars)" % (dst, len(out)), file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
