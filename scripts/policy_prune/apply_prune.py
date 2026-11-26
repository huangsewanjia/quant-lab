#!/usr/bin/env python3
"""Remove the `move` set from 政策·宏观 after proving each item is mirrored in the
archive library (政策原文库).

Why delete instead of the WeKnora move API: every one of the 5,560 macro docs is
already present in 政策原文库 under the same doc_id (WORK-519 split = copy, not
move). Moving the macro copy back would create a second copy in the archive,
which the task forbids ("旧库原样留档"). So the macro copy is deleted only after
(a) the same doc_id is verified live in the archive and (b) the full document
(title, custom_metadata, tags, body) is exported to a re-ingestible JSONL backup.

Usage:
  python3 scripts/policy_prune/apply_prune.py --dry-run
  python3 scripts/policy_prune/apply_prune.py --limit 50 --tag trial
  python3 scripts/policy_prune/apply_prune.py --all
"""
import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLAN = os.path.join(ROOT, "docs", "policy_prune", "plan.jsonl")
STATE = os.path.join(ROOT, "docs", "policy_prune", "deleted.jsonl")
BACKUP_DIR = "/srv/share/quantdb/policy/meta/prune-backup-20261127"


def load_plan():
    with open(PLAN, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def archive_doc_ids():
    """Live doc_id set of the archive knowledge base (read-only)."""
    ids = set()
    for it in wk.list_kb_docs(wk.ARCHIVE_KB, want_content=True):
        c = (it.get("metadata") or {}).get("content") or ""
        marker = "doc_id"
        i = c.find(marker)
        if i >= 0:
            seg = c[i:i + 40].replace("：", ":").split(":")[1]
            ids.add(seg.strip().split()[0].strip("，,。"))
    return ids


def already_done():
    if not os.path.exists(STATE):
        return set()
    return {json.loads(l)["id"] for l in open(STATE, encoding="utf-8")}


def kb_total(kb):
    st, d = wk.call("GET", f"/knowledge-bases/{kb}/knowledge?page=1&page_size=1")
    return d.get("total")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default="batch")
    ap.add_argument("--chunk", type=int, default=50)
    args = ap.parse_args()

    plan = load_plan()
    todo = [r for r in plan if r["decision"] == "move"]
    done = already_done()
    todo = [r for r in todo if r["id"] not in done]
    # stable order: by category then doc_id, so a --limit trial is stratified
    todo.sort(key=lambda r: (r["move_cat"], r["doc_id"]))

    if args.limit:
        per = collections.defaultdict(list)
        for r in todo:
            per[r["move_cat"]].append(r)
        total = sum(len(v) for v in per.values())
        picked, quotas = [], {k: max(1, round(len(v) * args.limit / total)) for k, v in per.items()}
        for k in sorted(per):
            picked += per[k][: quotas[k]]
        picked = picked[: args.limit]
        todo = picked

    print(f"candidates to delete: {len(todo)} (already deleted: {len(done)})")
    if args.dry_run:
        print("dry-run: nothing deleted")
        for r in todo[:10]:
            print("  ", r["move_cat"], r["title"][:60])
        return

    arch = archive_doc_ids()
    print(f"archive mirror doc_ids: {len(arch)}")
    missing = [r for r in todo if r["doc_id"] not in arch]
    if missing:
        print(f"ABORT: {len(missing)} docs lack an archive mirror, e.g.")
        for r in missing[:5]:
            print("   ", r["doc_id"], r["title"][:60])
        sys.exit(1)

    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, f"{args.tag}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")
    before = kb_total(wk.MACRO_KB)
    print(f"macro KB total before: {before}")

    with open(backup_path, "w", encoding="utf-8") as bf:
        for r in todo:
            bf.write(json.dumps({k: r[k] for k in (
                "id", "title", "doc_id", "wenhao", "url", "agency", "industry", "theme",
                "level", "publish_date", "effective_date", "tags", "content")}, ensure_ascii=False) + "\n")
    print("backup ->", backup_path, os.path.getsize(backup_path), "bytes")

    deleted = 0
    for i in range(0, len(todo), args.chunk):
        chunk = todo[i:i + args.chunk]
        st, resp = wk.call("POST", "/knowledge/batch-delete",
                           {"kb_id": wk.MACRO_KB, "ids": [r["id"] for r in chunk]})
        print(f"batch {i//args.chunk + 1}: {len(chunk)} ids -> HTTP {st} {str(resp)[:160]}")
        if st != 200:
            print("ABORT on failed batch; already-deleted ids are recorded in", STATE)
            break
        with open(STATE, "a", encoding="utf-8") as sf:
            for r in chunk:
                sf.write(json.dumps({"id": r["id"], "doc_id": r["doc_id"], "move_cat": r["move_cat"],
                                     "title": r["title"], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "backup": backup_path}, ensure_ascii=False) + "\n")
        deleted += len(chunk)
        print(f"  deleted so far this run: {deleted}; macro total now: {kb_total(wk.MACRO_KB)}")

    after = kb_total(wk.MACRO_KB)
    print(f"macro KB total after: {after} (Δ={before - after})")
    print("deleted this run:", deleted)


if __name__ == "__main__":
    main()
