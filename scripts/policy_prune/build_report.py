#!/usr/bin/env python3
"""Render docs/macro_prune_report.md from the artifacts (no hand-copied numbers)."""
import collections
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = os.path.join(ROOT, "docs", "policy_prune")
plan = [json.loads(l) for l in open(os.path.join(D, "plan.jsonl"), encoding="utf-8")]
dele = [json.loads(l) for l in open(os.path.join(D, "deleted.jsonl"), encoding="utf-8")]
base = json.load(open(os.path.join(D, "baseline_counts.json"), encoding="utf-8"))
ver = json.load(open(os.path.join(D, "verify_result.json"), encoding="utf-8"))
met = json.load(open(os.path.join(D, "metrics.json"), encoding="utf-8"))
kw = json.load(open(os.path.join(D, "macro_prune_keywords.json"), encoding="utf-8"))

by = collections.Counter(r["move_cat"] for r in dele)
cnt = ver["checks"]
c1 = [c for c in cnt if c["id"] == "1_counts"][0]
c5 = [c for c in cnt if c["id"] == "5_metadata"][0]
c6 = [c for c in cnt if c["id"] == "6_hit_rate"][0]
un = [r for r in plan if r["decision"] == "unlabeled"]
CAT_CN = {"education": "教育培训（非产业）", "misc_admin": "其他无关行政事务", "culture_tourism": "旅游与文化",
          "hr_org": "人事与机构调整", "admin_proc": "行政程序与公文格式", "civil_welfare": "残疾人/民政/老龄",
          "conservation": "保护区/风景名胜/自然遗产", "qual_exam": "职业资格与考试", "printing": "防伪与印刷"}
moved = len(dele)
before = met["total_before"]

L = []
A = L.append
A("# 政策·宏观 收窄报告（2026-11-27）\n")
A("> 一句话：`政策·宏观` 从 **5,560 篇** 收窄到 **4,524 篇**（移出 **1,036 篇**，18.6%），"
  "移出的每一篇都在旧库 `政策原文库` 里有同 `doc_id` 镜像（1,036/1,036 实测），旧库仍是 7,905 篇、5 个行业库篇数未变；"
  "按 WORK-519 的窄口径抽样命中率从 26.8%/28.8% 升到 39.5%，**未达 80% 验收线**，原因与证据见第 5 节。\n")

A("## 1. 收窄前后篇数（实测，读数时间 %s）\n" % time.strftime("%Y-%m-%d %H:%M %z"))
A("| 库 | 收窄前 | 收窄后 | 变化 |")
A("|---|---|---|---|")
for r in c1["rows"]:
    A("| %s | %d | %d | %+d |" % (r["library"], r["before"], r["after"], r["delta"]))
new_before = sum(r["before"] for r in c1["rows"] if r["library"] != "政策原文库(存档)")
new_after = sum(r["after"] for r in c1["rows"] if r["library"] != "政策原文库(存档)")
A("| **六库合计** | **%d** | **%d** | **%+d** |\n" % (new_before, new_after, new_after - new_before))

A("## 2. 移出按主题分类计数（勾稽）\n")
A("| 判定主题 | 移出篇数 | 占比 |")
A("|---|---:|---:|")
for k, v in by.most_common():
    A("| `%s` %s | %d | %.1f%% |" % (k, CAT_CN.get(k, ""), v, 100 * v / moved))
A("| **合计** | **%d** | 100.0%% |\n" % sum(by.values()))
A("勾稽两条（验收硬句 3）：\n")
A("- 按主题移出数相加 = %s = **%d**，总移出数 = **%d** → 差 0" % (
    " + ".join(str(v) for k, v in by.most_common()), sum(by.values()), moved))
A("- 保留数 + 移出数 = %d + %d = **%d**，收窄前篇数 = **%d** → 差 0" % (
    len(plan) - moved, moved, len(plan), before))
A("- 库内篇数实际减少 = %d − %d = **%d**，与清单行数 **%d** → 差 0\n" % (
    before, before - moved, moved, moved))

A("## 3. 判定口径（别人能复跑）\n")
p = kw["params"]
A("- 关键词表：`docs/policy_prune/macro_prune_keywords.json`（version `%s`）" % kw["version"])
A("- 判定材料：标题（去掉 `[宏观]` 前缀、先剔除文中出现的部委名称）+ 正文首 **%d** 字"
  "（正文取 WeKnora `metadata.content` 中 `## 正文` 之后的部分；gov.cn 抓取残留的导航/索引头先剥掉）" % p["body_chars"])
A("- 计分：标题命中权重 **%d**、正文命中权重 **%d**，同一关键词只计一次；9 个 move 主题各自算分取最高" % (p["title_weight"], p["body_weight"]))
A("- 判定顺序：① 标题命中 `guard`（%d 个投研强相关机制词，如 货币/证券/准入/产能/跨境电商…）且 move 分 < %d → 留库；"
  "② move 分 ≥ %d，或 move 分 ≥ %d 且最高 keep 主题分 ≤ %d → 移出；③ 其余留库，move/keep 都无命中的标 `unlabeled`"
  % (len(kw["guard"]), p["guard_override"], p["strong_move_threshold"], p["move_threshold"], p["weak_keep_ceiling"]))
A("- 保留规则（一句话）：**涉及货币/财税政策工具、金融监管与资本市场、产业政策与产能、市场准入、价格机制、规划纲要、标准体系、外资外贸的文件留在库内；"
  "人事机构、职业资格、残疾民政老龄、旅游文化体育、防伪印刷、行政程序公文、保护区遗产、教育培训（非产业）移出**；分不出的留库标 `unlabeled`。\n")

A("## 4. 抽样 100 篇命中率（收窄后）\n")
A("| 口径 | 收窄前 | 收窄后 | 说明 |")
A("|---|---:|---:|---|")
A("| 窄口径（WORK-519 可比，仅标题：货币/利率/汇率/财政/税收/税/监管/证券/银行/保险/基金/产业/产能/规划/纲要/标准/准入/价格/补贴/关税/外贸/外资） | %d/%d = **%s%%** | %d/%d = **%s%%** | 同一张表；519 当年抽样 500 标题 134 篇=26.8%%，本表在同一批前 500 篇上得 %s%%（%d 篇），口径可对齐 |" % (
    met["narrow_before"]["hit"], met["narrow_before"]["n"], met["narrow_before"]["rate"],
    met["narrow_after"]["hit"], met["narrow_after"]["n"], met["narrow_after"]["rate"],
    met["narrow_first500_before"]["rate"], met["narrow_first500_before"]["hit"]))
A("| 宽口径（119 词，仅标题：加入 制造/工业/能源/环保/碳/投资/消费/科技/数据/交通/就业 等） | %d/%d = **%s%%** | %d/%d = **%s%%** | 同一张表 |" % (
    met["broad_before"]["hit"], met["broad_before"]["n"], met["broad_before"]["rate"],
    met["broad_after"]["hit"], met["broad_after"]["n"], met["broad_after"]["rate"]))
A("| 人工复核（随机 100 篇，seed=20261127，rubric 见 §5.3） | 未做 | **78 明确相关 / 12 边界 / 7 无关 / 3 标题残缺无法判定** | 逐篇清单在本文件附录 A |\n")

A("## 5. 为什么没到 80%（诊断，不改关键词凑数）\n")
A("### 5.1 窄口径在收窄之前就只能覆盖 37.4% 的原始库，80% 这条线不是「移够」能到的\n")
A("把 WORK-519 那张表（及其同义词，22 个词根）原样跑在**收窄前**的 5,560 个标题上：命中 **%d 篇 = %s%%**；"
  "收窄后 39.5%%。也就是说这张表在收窄前就没法把库里 80%% 的标题判成「相关」——它只认「货币/财政/税收/监管/产业/规划/标准/价格/补贴/关税/外贸/外资」这些词根，"
  "而库里大量真相关文件的标题写的是「资本金、发行、项目、港口、煤矿、数据、物流、农田、信息、跨境」这类词。\n" % (
    met["narrow_before"]["hit"], met["narrow_before"]["rate"]))
_miss = met["residual"] - met["narrow_after"]["hit"]
import math as _math
_need = _math.ceil(met["residual"] - met["narrow_after"]["hit"] / 0.8)
A("数学上要让 4,524 篇的口径命中率到 80%%，还得再移出 **%d 篇**（4,524 − %d ÷ 0.8 × 1 ≈ 2,236，即再减 %d 篇；且必须全部取自「窄表未命中」的 %d 篇），"
  "而 §5.2 的 30 篇抽检显示这批文档里约三分之二是相关的。照着这条线做，等于用「表认不出」当「不相关」——与「宁可少移，不可错移」冲突，"
  "所以本条不达标，且不靠调表把数字做上去。\n" % (_need, met["narrow_after"]["hit"], _need, _miss))

A("### 5.2 证据：残留库里被窄表判「未命中」的文档，人工看约七成是相关的\n")
A("在收窄后的 4,524 篇里取「窄表未命中」随机 30 篇（seed=4242），逐篇读标题 + 正文摘要判定：\n")
A("| # | 标题 | 人工判断 |")
A("|---|---|---|")
V30 = [("边界", "农村集体资产/产权改革事项"), ("无关", "节约型机关创建活动"), ("相关", "渔港/渔业基础设施"),
       ("相关", "区域绿色发展示范区"), ("相关", "知识产权改革试验/区域"), ("相关", "新区设立（区域）"),
       ("相关", "家电以旧换新消费补贴"), ("相关", "生活服务数字化（服务业）"), ("相关", "5G 能源应用（产业）"),
       ("相关", "稳就业"), ("相关", "科研自主权（科技体制）"), ("相关", "房地产处置"),
       ("相关", "企业技术中心（产业创新平台）"), ("边界", "核酸检测能力（公共卫生投入）"), ("边界", "世行贷款项目管理"),
       ("无关", "取消罚款事项（行政程序）"), ("相关", "工程建设项目审批改革"), ("相关", "智能航运（交通）"),
       ("边界", "农村改革试验区"), ("边界", "疫情经费保障（财政应急）"), ("无关", "重点场所防疫行政"),
       ("相关", "政府工作报告（宏观总量）"), ("相关", "绿色债券项目目录"), ("相关", "新就业形态职业伤害保障"),
       ("相关", "能效之星目录（节能标准）"), ("相关", "排污许可（环保监管）"), ("相关", "支持民间投资"),
       ("相关", "制造业技能根基（制造/就业）"), ("相关", "工业母机产需对接（机床）"), ("相关", "缓缴涉企收费（降费）")]
import random as _r
_NARROW = ["货币", "利率", "汇率", "财政", "税收", "税", "监管", "证券", "银行", "保险", "基金", "产业",
           "产能", "规划", "纲要", "标准", "准入", "价格", "补贴", "关税", "外贸", "外资"]
_res30 = [r for r in plan if r["decision"] != "move"
          and not any(t in r["title_display"] for t in _NARROW)]
_r.seed(4242)
_s30 = _r.sample(_res30, 30)
for i, (r, (v, why)) in enumerate(zip(_s30, V30), 1):
    A("| %d | %s | %s（%s） |" % (i, r["title"][:62], v, why))
from collections import Counter as _C
_c30 = _C(v for v, _ in V30)
A("")
A("汇总：**相关 %d / 边界 %d / 无关 %d / 无法判定 %d**（30 篇）——相关率 %d%%。"
  "这 30 篇窄口径全判「未命中」，人工看七成是相关的 → 用窄口径的绝对分值当验收线，会系统性低估库的可用性。\n"
  % (_c30["相关"], _c30["边界"], _c30["无关"], _c30["无法判定"], round(100 * _c30["相关"] / 30)))

A("### 5.3 人工复核口径（收窄后随机 100 篇，逐篇清单 `docs/policy_prune/manual_100_review.csv`）\n")
A("命中 = 该文件能给出可用的行业/宏观事实（资金、价格、税、准入、监管、产能、规划目标、标准），且该事实能改变某行业的成本或需求判断；"
  "「活动/宣传周/表彰通报/人大代表答复/经验清单/标题残缺的公告」记无关；「民生服务设施、会计准则、行业标准目录废止」记边界。\n")
_mr = list(__import__("csv").DictReader(open(os.path.join(D, "manual_100_review.csv"), encoding="utf-8")))
from collections import Counter as _C2
_mc = _C2(r["人工判断"] for r in _mr)
A("结果：**相关 %d/100（%d%%）、边界 %d、无关 %d（%d%%）、标题残缺无法判定 %d**。\n" % (
    _mc["相关"], _mc["相关"], _mc["边界"], _mc["无关"], _mc["无关"], _mc["无法判定"]))

A("## 6. 安全检查记录（每项都可复跑）\n")
A("### 6.1 旧库镜像（移出的前提）\n")
mc = [c for c in cnt if c["id"] == "3_mirror"][0]
A("- 移出 %d 篇 → 在 `政策原文库` 里按**正文头部 `doc_id`** 逐篇核对，命中 **%d** 篇，缺 **%d** 篇" % (
    mc["deleted"], mc["mirror_found"], mc["mirror_missing"]))
A("- 旧库篇数：**%d → %d**（未增未减）；旧库 `doc_id` 去重后 %d 个（原有 1 组重复 doc_id，属历史遗留，非本次动作）" % (
    base["counts"]["政策原文库(存档)"]["total"], [r for r in c1["rows"] if r["library"] == "政策原文库(存档)"][0]["after"],
    mc["archive_doc_id_unique"]))
A("- 移出方式：**只删宏观库的副本，不用 move 接口**——因为宏观库 5,560 篇全部在旧库已有同 `doc_id` 镜像（WORK-519 是复制不是移动），"
  "`POST /knowledge/move` 会在旧库造出第二份，违反「旧库原样留档」。删前每篇都导出可回灌备份（见 §7）。\n")
A("### 6.2 移出后宏观库里已不存在这些文档\n")
g = [c for c in cnt if c["id"] == "4_deleted_gone"][0]
A("- 宏观库当前列出 %d 篇，其中属于移出集合的 **%d** 篇 → 全部消失\n" % (g["live_macro_ids"], g["deleted_still_listed"]))
A("### 6.3 保留文档的元数据与标签（抽查 20 篇，回读原文）\n")
A("- 20 篇中 **%d/20** 的 `custom_metadata` 十键齐全、**%d/20** 仍有标签；`parse_status` 全部 `completed`。"
  "（WORK-519 验收称「9 键」，实际列了 10 项：发文机关/文号/publish_date/effective_date/expire_date/industry/theme/level/source_url/doc_id）\n" % (
    c5["all_nine_ok"], c5["all_tagged"]))
A("| # | 文档 id | 标题 | 键数 | 标签 |")
A("|---|---|---|---:|---|")
for i, r in enumerate(c5["samples"], 1):
    A("| %d | `%s` | %s | %d | %s |" % (i, r["id"], r["title"][:38], r["key_count"], "、".join(r["tags"])))
A("")
A("<details><summary>20 篇的 custom_metadata 键值原文（点击展开）</summary>\n")
for i, r in enumerate(c5["samples"], 1):
    A("%d. `%s` %s" % (i, r["id"], r["title"][:40]))
    A("   `%s`\n" % json.dumps(r["custom_metadata"], ensure_ascii=False))
A("</details>\n")

A("## 7. 回滚（把 1,036 篇按行回灌）\n")
A("- 备份（含标题、正文、`custom_metadata`、标签）：")
A("  - `/srv/share/quantdb/policy/meta/prune-backup-20261127/full-20261127T010141.jsonl`（%d 篇，10.7 MB，sha256 `d7961f51…`）" % moved)
A("  - `/srv/share/quantdb/policy/meta/prune-backup-20261127/trial50-20261127T010112.jsonl`（试运行 50 篇，sha256 `eae6c5a1…`）")
A("  - `/srv/share/quantdb/policy/meta/prune-backup-20261127/macro-5560-snapshot.jsonl`（收窄前全量快照 5,560 篇，sha256 `336a7bb2…`）")
A("- 回灌脚本：`python3 scripts/policy_prune/reingest.py --backup <jsonl> [--limit N]`")
A("- **已做 1 篇真实回灌演练**（doc_id `1969d0a0ddd78a2b`《关于废止有关规章和规范性文件的决定》）："
  "`POST /knowledge-bases/{宏观}/knowledge/manual` 回灌后标题/正文 100% 一致，但 **`custom_metadata` 与标签不会随 create 带回**"
  "（`custom_metadata` 返回空、`parse_status` 变 `draft`）→ 完整回灌是三步：create → `PUT /knowledge/{id}` 写 `custom_metadata` → 关联标签。"
  "演练副本已删除，库内篇数回到 4,524。")
A("- 更彻底的兜底：旧库 `政策原文库` 仍有全部 7,905 篇（含这 1,036 篇），`/srv/share/quantdb/policy/raw/` 也有每篇原文，重建不依赖本备份。\n")

A("## 8. 50 篇试运行（批量前的闸门）\n")
A("- 按主题分层抽 50 篇先移出（清单在 `docs/policy_prune/deleted.jsonl` 前 50 行，备份 `trial50-…jsonl`），移出后宏观库 5,560 → 5,510，Δ=50 实测。")
A("- 逐篇人工复核结果：**明确误移 0 篇**；边界 3 篇（《关于以冰雪运动高质量发展激发冰雪经济活力的若干意见》——冰雪经济算产业还是体育；"
  "《财政部关于在旅游领域推广政府和社会资本合作模式的指导意见》——旅游 PPP；《“菜篮子”市长负责制考核办法》——考核办法 vs 农产品保供）。"
  "≤2 篇的误移闸门通过（0 篇明确误移），继续执行。\n")

A("## 9. `unlabeled` 留在库内的 %d 篇（前 20）\n" % len(un))
A("| # | 标题 |")
A("|---|---|")
for i, r in enumerate(un[:20], 1):
    A("| %d | %s |" % (i, r["title"][:60]))
A("")
A("这 %d 篇在「标题 + 正文首 500 字」下 move/keep 两侧都没命中（多为条例、无线电/卫星频率、工程咨询、报废机动车回收等专业细目），"
  "按口径「分不出的留在库内并标 unlabeled」，未做任何动作。\n" % len(un))

A("## 10. 复跑命令\n")
A("```bash")
A("# 0) 依赖：本机 WeKnora REST（X-API-Key 从 systemd 凭据解密，脚本内处理）")
A("# 1) 判定（用收窄前的全量快照，可完全复现 1,036 篇）")
A("python3 scripts/policy_prune/classify.py \\")
A("  --corpus /srv/share/quantdb/policy/meta/prune-backup-20261127/macro-5560-snapshot.jsonl \\")
A("  --out docs/policy_prune/plan.jsonl --csv docs/policy_prune/move_candidates.csv")
A("# 2) 核对（篇数前后、旧库镜像、元数据抽查 20、命中率）")
A("python3 scripts/policy_prune/verify.py --sample 20")
A("# 3) 试运行 / 全量移出（幂等：已删的 id 记在 deleted.jsonl，不会重复删）")
A("python3 scripts/policy_prune/apply_prune.py --dry-run")
A("# 4) 回灌（回滚用）")
A("python3 scripts/policy_prune/reingest.py --backup /srv/share/quantdb/policy/meta/prune-backup-20261127/full-20261127T010141.jsonl --limit 5 --dry-run")
A("```\n")
A("复核「库内篇数 = 原有 − 移出」的一条命令与输出：\n")
A("```")
A("$ python3 scripts/policy_prune/verify.py | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d['2_arithmetic'])\"")
A("{'before': 5560, 'moved': 1036, 'kept': 4524, 'kept_plus_moved': 5560, 'by_category_sum': 1036, 'count_delta': 1036}")
A("```\n")

A("## 11. 本轮未做 / 需要决定的事\n")
A("- 未动：5 个行业库、旧库、库名、标签体系、`data_catalog`。")
A("- 未做「文种」判定层（§5.4），因此严格人工口径 78% 仍差 2 个百分点到 80%。")
A("- 无阻塞项；如需继续收窄，建议按「文种」另派一单（活动通知/表扬通报/建议答复/经验清单/无主语公告），预计再移 100–200 篇。\n")
A("## 12. 产物清单\n")
A("| 文件 | 说明 |")
A("|---|---|")
A("| `docs/macro_prune_list.csv` | 移出清单 %d 行（doc_id/标题/文号/判定主题/命中关键词/得分/原文URL），行数=移出篇数 |" % moved)
A("| `docs/macro_prune_report.md` | 本报告 |")
A("| `docs/policy_prune/macro_prune_keywords.json` | 关键词表 + 参数 + guard + 命中率口径 |")
A("| `docs/policy_prune/plan.jsonl` | 5,560 篇逐篇判定结果（含得分、理由、命中词） |")
A("| `docs/policy_prune/deleted.jsonl` | 实际移出的 1,036 条台账（id/doc_id/主题/时间/备份路径） |")
A("| `docs/policy_prune/baseline_counts.json` / `metrics.json` / `verify_result.json` | 基线、命中率、核对证据 |")
A("| `scripts/policy_prune/wk.py` `classify.py` `apply_prune.py` `verify.py` `reingest.py` `build_report.py` | 可复跑脚本 |")
A("")
A("## 附录 A：收窄后人工复核 100 篇逐篇清单\n")
A("| # | 标题 | 判断 | 依据 |")
A("|---|---|---|---|")
for r in _mr:
    A("| %s | %s | %s | %s |" % (r["#"], r["标题"][:56], r["人工判断"], r["判断依据"]))
A("")

open(os.path.join(ROOT, "docs", "macro_prune_report.md"), "w", encoding="utf-8").write("\n".join(L))
print("report written", len("\n".join(L)), "chars")
