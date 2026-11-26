#!/usr/bin/env python3
"""Reconciliation for the 政策·宏观 prune (read-only).

Checks, in order:
  1. live counts of the 7 libraries vs docs/policy_prune/baseline_counts.json
  2. kept + moved = before ; sum(moved by category) = moved total
  3. every deleted doc_id still exists in the archive library (mirror survives)
  4. none of the deleted ids is still listed in 政策·宏观
  5. 20 retained docs: custom_metadata 9 keys + tags still present
  6. hit rate before/after under the fixed metric table (macro_prune_keywords.json)

Usage: python3 scripts/policy_prune/verify.py [--sample 20]
"""
import argparse
import collections
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
D = os.path.join(ROOT, "docs", "policy_prune")
BASE = json.load(open(os.path.join(D, "baseline_counts.json"), encoding="utf-8"))
PLAN = [json.loads(l) for l in open(os.path.join(D, "plan.jsonl"), encoding="utf-8")]
DELETED = [json.loads(l) for l in open(os.path.join(D, "deleted.jsonl"), encoding="utf-8")]
KW = json.load(open(os.path.join(D, "macro_prune_keywords.json"), encoding="utf-8"))
METRIC = KW["metric"]["terms"]
NINE = ["发文机关", "文号", "publish_date", "effective_date", "expire_date", "industry",
        "theme", "level", "source_url", "doc_id"]


def total(kb):
    return wk.call("GET", f"/knowledge-bases/{kb}/knowledge?page=1&page_size=1")[1].get("total")


def hit(title):
    return any(t in title for t in METRIC)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20)
    args = ap.parse_args()
    ev = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": []}

    # 1. live counts
    live = {}
    for name, v in BASE["counts"].items():
        live[name] = total(v["id"])
    rows = []
    for name, v in BASE["counts"].items():
        before, after = v["total"], live[name]
        rows.append({"library": name, "before": before, "after": after, "delta": after - before})
    ev["checks"].append({"id": "1_counts", "rows": rows})

    macro_before = BASE["counts"]["政策·宏观"]["total"]
    macro_after = live["政策·宏观"]
    moved = len(DELETED)
    kept = len(PLAN) - moved
    by_cat = collections.Counter(r["move_cat"] for r in DELETED)
    ev["checks"].append({"id": "2_arithmetic", "before": macro_before, "moved": moved, "kept": kept,
                         "kept_plus_moved": kept + moved, "by_category": dict(by_cat),
                         "by_category_sum": sum(by_cat.values()),
                         "count_delta": macro_before - macro_after})

    # 3. archive mirror survives
    arch_doc_ids = {}
    for it in wk.list_kb_docs(wk.ARCHIVE_KB, want_content=True):
        c = (it.get("metadata") or {}).get("content") or ""
        i = c.find("doc_id")
        if i >= 0:
            did = c[i:i + 40].replace("：", ":").split(":")[1].strip().split()[0].strip("，,。")
            arch_doc_ids.setdefault(did, []).append(it["id"])
    mirror_ok = sum(1 for r in DELETED if r["doc_id"] in arch_doc_ids)
    missing = [r for r in DELETED if r["doc_id"] not in arch_doc_ids]
    ev["checks"].append({"id": "3_mirror", "deleted": moved, "mirror_found": mirror_ok,
                         "mirror_missing": len(missing),
                         "missing_sample": [{"doc_id": r["doc_id"], "title": r["title"]} for r in missing[:5]],
                         "archive_doc_id_unique": len(arch_doc_ids),
                         "archive_docs": live["政策原文库(存档)"]})

    # 4. deleted ids gone from macro
    live_ids = set()
    for it in wk.list_kb_docs(wk.MACRO_KB, want_content=False):
        live_ids.add(it["id"])
    still_there = [r for r in DELETED if r["id"] in live_ids]
    ev["checks"].append({"id": "4_deleted_gone", "live_macro_ids": len(live_ids),
                         "deleted_still_listed": len(still_there),
                         "sample": [r["id"] for r in still_there[:5]]})

    # 5. metadata on retained docs
    retained = [r for r in PLAN if r["id"] in live_ids]
    random.seed(20261127)
    samp = random.sample(retained, args.sample)
    recs = []
    for r in samp:
        st, d = wk.call("GET", f"/knowledge/{r['id']}")
        doc = d["data"]
        cm = doc.get("custom_metadata") or {}
        tags = [t["name"] for t in (doc.get("tags") or [])]
        recs.append({"id": doc["id"], "title": doc["title"], "keys_present": sorted(cm.keys()),
                     "key_count": len(cm), "custom_metadata": cm, "tags": tags,
                     "all_nine": all(k in cm for k in NINE),
                     "parse_status": doc.get("parse_status")})
    ev["checks"].append({"id": "5_metadata", "sample_size": len(recs),
                         "all_nine_ok": sum(1 for r in recs if r["all_nine"]),
                         "all_tagged": sum(1 for r in recs if r["tags"]), "samples": recs})

    # 6. hit rate
    before_titles = [r["title_clean"] for r in PLAN]
    after_titles = [r["title_clean"] for r in retained]
    hb = sum(1 for t in before_titles if hit(t))
    ha = sum(1 for t in after_titles if hit(t))
    random.seed(20261127)
    s100 = random.sample(retained, 100)
    h100 = sum(1 for r in s100 if hit(r["title_clean"]))
    ev["checks"].append({"id": "6_hit_rate", "metric_terms": len(METRIC),
                         "before": {"n": len(before_titles), "hit": hb, "rate": round(hb / len(before_titles), 4)},
                         "after": {"n": len(after_titles), "hit": ha, "rate": round(ha / len(after_titles), 4)},
                         "after_sample100": {"n": 100, "hit": h100},
                         "after_sample100_docs": [r["title_clean"] for r in s100]})

    out = os.path.join(D, "verify_result.json")
    json.dump(ev, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({c["id"]: {k: v for k, v in c.items() if k not in ("samples", "after_sample100_docs")}
                      for c in ev["checks"]}, ensure_ascii=False, indent=1))
    print("full evidence ->", out)


if __name__ == "__main__":
    main()
