#!/usr/bin/env python3
"""Score every 政策·宏观 document as keep / move / unlabeled.

口径（写进 /srv/share/quantdb/README.md §3.2）:
  - 判定材料 = 标题 + 正文首 500 字（正文取 WeKnora 文档 metadata.content 中
    `## 正文` 之后的部分）。
  - 关键词表 = docs/policy_prune/macro_prune_keywords.json；标题命中权重 3、
    正文命中权重 1，同一关键词只计一次。
  - 每个 move 主题独立算分，取最高分；`move_score >= move_threshold` 且
    `move_score > keep_score` 才移出，否则留在库内；move/keep 都低于阈值标
    `unlabeled`（宁可少移，不可错移）。

Usage:
  python3 scripts/policy_prune/classify.py --out docs/policy_prune/plan.jsonl
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KW_PATH = os.path.join(ROOT, "docs", "policy_prune", "macro_prune_keywords.json")

TITLE_PREFIX = re.compile(r"^\[[^\]]+\]\s*")


def clean_title(t):
    return TITLE_PREFIX.sub("", t).strip()


CHROME_MARK = "首页 > 政策"
CHROME_LINE = re.compile(r"^(索 ?引 ?号|主题分类|发文机关|发文字号|成文日期|发布日期|字号)[:：]?")


def strip_chrome(body):
    """Drop the gov.cn navigation / index header that some scrapes left in the body.

    Only applies when the body still starts with the site breadcrumb, so that the
    first 500 chars are actual document text rather than page chrome.
    """
    if not body.startswith(CHROME_MARK):
        return body
    lines = body.split("\n")
    out = []
    skipping = True
    for ln in lines:
        if skipping:
            if ln.startswith(CHROME_MARK) or CHROME_LINE.match(ln.strip()) or "打印" in ln and "收藏" in ln:
                continue
            skipping = False
        out.append(ln)
    return "\n".join(out).strip()


def load_kw(path=KW_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def strip_agencies(text, agencies):
    """Remove ministry names before scoring: a joint document must not score a
    move topic just because "文化和旅游部" is one of the issuing departments."""
    for a in agents_sorted(agencies):
        text = text.replace(a, " ")
    return text


_AG_SORTED = {}


def agents_sorted(agencies):
    key = id(agencies)
    if key not in _AG_SORTED:
        _AG_SORTED[key] = sorted(agencies, key=len, reverse=True)
    return _AG_SORTED[key]


def score(text, table, weight):
    """Return {category: [matched keywords]} for one text and one direction table."""
    hits = {}
    for cat, words in table.items():
        got = [w for w in words if w in text]
        if got:
            hits[cat] = got
    return hits


def classify(title, content, kw):
    p = kw["params"]
    body = strip_chrome(wk.body_after_header(content))[: p["body_chars"]]
    t = strip_agencies(clean_title(title), kw.get("agency_strip", []))
    body = strip_agencies(body, kw.get("agency_strip", []))
    move_title = score(t, kw["move"], p["title_weight"])
    keep_title = score(t, kw["keep"], p["title_weight"])
    move_body = score(body, kw["move"], p["body_weight"])
    keep_body = score(body, kw["keep"], p["body_weight"])

    move_cat, move_score, move_words = "", 0, []
    for cat in kw["move"]:
        tw = move_title.get(cat, [])
        bw = move_body.get(cat, [])
        s = p["title_weight"] * len(tw) + p["body_weight"] * len(bw)
        if s > move_score:
            move_cat, move_score, move_words = cat, s, tw + bw
    keep_score, keep_words = 0, []
    for cat in kw["keep"]:
        tw = keep_title.get(cat, [])
        bw = keep_body.get(cat, [])
        keep_score += p["title_weight"] * len(tw) + p["body_weight"] * len(bw)
        keep_words += tw + bw

    keeper_best, keeper_words = 0, []
    for cat in kw["keep"]:
        tw = keep_title.get(cat, [])
        bw = keep_body.get(cat, [])
        sc = p["title_weight"] * len(tw) + p["body_weight"] * len(bw)
        if sc > keeper_best:
            keeper_best, keeper_words = sc, tw + bw
    keep_score = sum(p["title_weight"] * len(v) for v in keep_title.values()) + sum(
        p["body_weight"] * len(v) for v in keep_body.values())

    guard_title = [w for w in kw.get("guard", []) if w in t]
    guard_body = [w for w in kw.get("guard", []) if w in body]
    guard_hit = guard_title + [w for w in guard_body if w not in guard_title]

    strong_move = move_score >= p["strong_move_threshold"]
    weak_move_ok = move_score >= p["move_threshold"] and keeper_best <= p["weak_keep_ceiling"]
    if (strong_move or weak_move_ok) and not guard_title:
        decision = "move"
        reason = "move 主题分达强阈(>=%d)或达弱阈且最高 keep 主题分<=%d，标题未命中投研 guard" % (
            p["strong_move_threshold"], p["weak_keep_ceiling"])
    elif move_score == 0 and keeper_best == 0:
        decision = "unlabeled"
        reason = "move/keep 主题均无命中，按口径留在库内"
    else:
        decision = "keep"
        if guard_title:
            reason = "标题命中投研 guard（%s），留在库内" % ",".join(guard_title)[:40]
        elif keeper_best >= move_score:
            reason = "最高 keep 主题分不低于 move 分"
        else:
            reason = "move 分低于阈值或正文 guard 拦截"

    return {
        "decision": decision,
        "title_display": clean_title(title),
        "reason": reason,
        "move_cat": move_cat,
        "move_score": move_score,
        "move_words": ";".join(dict.fromkeys(move_words)),
        "keep_score": keep_score,
        "keep_best": keeper_best,
        "keep_words": ";".join(dict.fromkeys(keep_words)),
        "guard": ",".join(dict.fromkeys(guard_hit)),
        "title_clean": t,
        "body_head": body[:120],
    }


def load_corpus(path):
    """Read a frozen corpus snapshot (JSONL produced by pull/pull_lists.py).

    Needed because after the prune runs the live KB no longer holds the removed
    documents; the snapshot keeps the classification reproducible.
    """
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def load_macro():
    """Read the live document list from WeKnora (macro KB)."""
    out = []
    for it in wk.list_kb_docs(wk.MACRO_KB):
        cm = it.get("custom_metadata") or {}
        out.append({
            "id": it["id"],
            "title": it.get("title", ""),
            "content": (it.get("metadata") or {}).get("content") or "",
            "doc_id": cm.get("doc_id", ""),
            "wenhao": cm.get("文号", ""),
            "url": cm.get("source_url", ""),
            "industry": cm.get("industry", ""),
            "theme": cm.get("theme", ""),
            "level": cm.get("level", ""),
            "publish_date": cm.get("publish_date", ""),
            "effective_date": cm.get("effective_date", ""),
            "agency": cm.get("发文机关", ""),
            "tags": [x.get("name", "") for x in (it.get("tags") or [])],
            "parse_status": it.get("parse_status"),
            "summary_status": it.get("summary_status"),
            "channel": it.get("channel"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "policy_prune", "plan.jsonl"))
    ap.add_argument("--kw", default=KW_PATH)
    ap.add_argument("--corpus", default="", help="frozen JSONL snapshot instead of the live KB")
    ap.add_argument("--csv", default="", help="optional CSV of move candidates")
    args = ap.parse_args()

    kw = load_kw(args.kw)
    docs = load_corpus(args.corpus) if args.corpus else load_macro()
    plan = []
    for d in docs:
        r = classify(d["title"], d["content"], kw)
        plan.append({**d, **r})

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in plan:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cnt = collections.Counter(r["decision"] for r in plan)
    print(f"total={len(plan)} " + " ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
    mc = collections.Counter(r["move_cat"] for r in plan if r["decision"] == "move")
    for k, v in mc.most_common():
        print(f"  move[{k}] = {v}")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["doc_id", "标题", "文号", "判定主题", "命中关键词", "得分", "原文URL"])
            for r in plan:
                if r["decision"] == "move":
                    w.writerow([r["doc_id"], r["title"], r["wenhao"], r["move_cat"],
                                r["move_words"], r["move_score"], r["url"]])
        print("csv ->", args.csv, sum(1 for r in plan if r["decision"] == "move"), "rows")


if __name__ == "__main__":
    main()
