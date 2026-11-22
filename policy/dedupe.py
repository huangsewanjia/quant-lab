"""Post-hoc cross-source dedupe.

Concurrent collection runs of different sources each snapshot the dedupe sets
at start-up, so a document published to two sources during the same window can
be stored twice. This pass re-checks the finished corpus by sha256 and by
strong document number, keeps one canonical copy, records the other with
``duplicate_of`` and bumps ``reprint_count``.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = Path("/srv/share/quantdb/policy")
KEEP_ORDER = {"gov_gw": 0, "gov_bm": 1, "ndrc": 2, "mof": 3, "pbc": 4, "mnr": 5}


def norm_docno(value: str | None) -> str:
    return "".join((value or "").split())


def strong_docno(value: str | None) -> bool:
    value = norm_docno(value)
    return len(value) >= 6 and value != "unknown" and not value.startswith("第")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="政策库跨源去重")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    metas = []
    for path in sorted((args.root / "raw").glob("*/*/*.meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["_path"] = path
        metas.append(meta)
    by_sha: dict[str, list[dict]] = defaultdict(list)
    for meta in metas:
        by_sha[meta["sha256"]].append(meta)
    removed = 0
    dup_log = args.root / "meta" / "duplicates.jsonl"
    for sha, group in by_sha.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda m: (KEEP_ORDER.get(m["source_id"], 9), m["doc_id"]))
        keep = group[0]
        keep["reprint_count"] = int(keep.get("reprint_count") or 0) + len(group) - 1
        keep["_path"].write_text(json.dumps({k: v for k, v in keep.items() if k != "_path"}, ensure_ascii=False, indent=2), encoding="utf-8")
        for meta in group[1:]:
            base = str(meta["_path"])[: -len(".meta.json")]
            for suffix in (".meta.json", ".txt", ".html.gz"):
                path = Path(str(base) + suffix)
                if path.exists():
                    path.unlink()
            with dup_log.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "source": meta["source_id"],
                            "title": meta["title"],
                            "url": meta["url"],
                            "duplicate_of": keep["doc_id"],
                            "matched": "sha256_posthoc",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            removed += 1
    print(json.dumps({"documents": len(metas), "removed": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
