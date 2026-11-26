#!/usr/bin/env python3
"""Re-ingest documents from a prune backup (rollback path).

Restoring is three calls per document, because `POST .../knowledge/manual` only
takes title + body: it does NOT carry custom_metadata or tags, and the new
document lands as `draft`.
  1) POST /knowledge-bases/{kb}/knowledge/manual  {title, content}   -> new id
  2) PUT  /knowledge/{id}                         {custom_metadata}
  3) PUT  /knowledge/tags                         {knowledge_ids, tag_ids}

Usage:
  python3 scripts/policy_prune/reingest.py --backup <jsonl> --dry-run
  python3 scripts/policy_prune/reingest.py --backup <jsonl> --limit 5
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wk  # noqa: E402


def kb_tags(kb):
    st, d = wk.call("GET", f"/knowledge-bases/{kb}/tags")
    if st != 200:
        return {}
    payload = d.get("data") or {}
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return {t["name"]: t["id"] for t in (rows or [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--kb", default=wk.MACRO_KB)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    recs = [json.loads(l) for l in open(args.backup, encoding="utf-8")]
    if args.limit:
        recs = recs[: args.limit]
    tags = kb_tags(args.kb)
    print(f"{len(recs)} records; kb tags available: {len(tags)}")
    if args.dry_run:
        for r in recs[:5]:
            print("  would restore", r["doc_id"], r["title"][:50], "| metadata keys",
                  len({k: v for k, v in r.items() if k in (
                      "agency", "wenhao", "publish_date", "effective_date", "industry", "theme", "level", "url")}))
        print("dry-run: nothing written")
        return

    ok = 0
    for r in recs:
        st, d = wk.call("POST", f"/knowledge-bases/{args.kb}/knowledge/manual",
                        {"title": r["title"], "content": r["content"], "channel": "work-620-reingest",
                         "publish": True})
        if st != 200:
            print("FAIL create", r["doc_id"], st, str(d)[:120])
            continue
        nid = d["data"]["id"]
        cm = {"发文机关": r["agency"], "文号": r["wenhao"], "publish_date": r["publish_date"],
              "effective_date": r["effective_date"], "expire_date": "unknown",
              "industry": r["industry"], "theme": r["theme"], "level": r["level"],
              "source_url": r["url"], "doc_id": r["doc_id"]}
        st2, d2 = wk.call("PUT", f"/knowledge/{nid}", {"custom_metadata": cm, "title": r["title"]})
        tag_ids = [tags[t] for t in (r.get("tags") or []) if t in tags]
        st3 = 0
        if tag_ids:
            st3, d3 = wk.call("PUT", "/knowledge/tags", {"knowledge_ids": [nid], "tag_ids": tag_ids})
        print(f"restored doc_id={r['doc_id']} -> {nid} meta={st2} tags={st3}")
        ok += st == 200 and st2 == 200
        if ok % 20 == 0:
            print(f"... {ok} restored")
    print("restored", ok, "of", len(recs))


if __name__ == "__main__":
    main()
