"""Re-fetch a random sample and compare stored fields with the live page.

Checks per document:
  * title still present in the live HTML and matches the stored title
  * document number matches when the stored value is not `unknown`
  * publish date matches when the page still exposes one
  * first paragraph of the re-extracted text equals the stored first paragraph

This is a freshness/consistency audit, not a substitute for a human reading
the page; the CSV keeps a `manual_review` column for that.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

import pandas as pd
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import build_document  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = Path("/srv/share/quantdb/policy")
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36 quantlab-policy-audit/1.0"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="政策抽样回核")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=515)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    frame = pd.read_parquet(args.root / "meta" / "index.parquet")
    rng = random.Random(args.seed)
    sample = frame.iloc[rng.sample(range(len(frame)), min(args.n, len(frame)))]
    sources = {
        row["id"]: row
        for row in yaml.safe_load((HERE / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    }
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    rows = []
    for _, row in sample.iterrows():
        source = sources.get(row["source_id"], {"id": row["source_id"], "content_selectors": []})
        stored_text = (args.root / row["text_path"]).read_text(encoding="utf-8")
        stored_first = norm(stored_text.split("\n")[0])[:120]
        record = {
            "doc_id": row["doc_id"],
            "source": row["source_id"],
            "title": row["title"],
            "document_number": row["document_number"],
            "publish_date": row["publish_date"],
            "effective_date": row["effective_date"],
            "industry_tags": "|".join(row["industry_tags"]),
            "url": row["url"],
            "http": "",
            "title_ok": "",
            "docno_ok": "",
            "publish_ok": "",
            "first_para_ok": "",
            "note": "",
            "manual_review": "",
        }
        try:
            resp = session.get(row["url"], timeout=30)
            record["http"] = resp.status_code
            if resp.encoding.lower() in {"iso-8859-1", "ascii"}:
                resp.encoding = resp.apparent_encoding or "utf-8"
            fresh = build_document(
                html=resp.text,
                url=row["url"],
                title=row["title"],
                source=source,
                publish_hint=row["publish_date"],
            )
            fresh_first = norm(fresh["text"].split("\n")[0])[:120]
            record["title_ok"] = norm(row["title"]) in norm(resp.text) or fresh["title"] == row["title"]
            record["publish_ok"] = (
                fresh["publish_date"] == row["publish_date"] if fresh["publish_date"] else ""
            )
            if row["document_number"] != "unknown":
                record["docno_ok"] = norm(row["document_number"]) in norm(resp.text) or norm(
                    row["document_number"]
                ) in norm(fresh["text"])
            record["first_para_ok"] = bool(stored_first) and stored_first == fresh_first
            if not record["first_para_ok"]:
                record["note"] = f"stored={stored_first[:40]!r} fresh={fresh_first[:40]!r}"
        except Exception as exc:  # noqa: BLE001
            record["note"] = f"{type(exc).__name__}: {exc}"[:200]
        rows.append(record)

    out = args.out or (args.root / "meta" / f"audit-{args.n}.csv")
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    ok = sum(
        1
        for r in rows
        if r["title_ok"] and r["first_para_ok"] and r["docno_ok"] in (True, "")
    )
    summary = {
        "sample": len(rows),
        "full_match": ok,
        "title_missing": sum(1 for r in rows if r["title_ok"] is False),
        "docno_mismatch": sum(1 for r in rows if r["docno_ok"] is False),
        "publish_mismatch": sum(1 for r in rows if r["publish_ok"] is False),
        "first_para_mismatch": sum(1 for r in rows if not r["first_para_ok"]),
        "http_errors": sum(1 for r in rows if r["http"] != 200),
        "out": str(out),
    }
    (args.root / "meta" / f"audit-{args.n}.summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["full_match"] >= int(len(rows) * 0.9) else 1


if __name__ == "__main__":
    raise SystemExit(main())
