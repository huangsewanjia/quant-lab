"""Re-derive timeline fields from stored HTML with the current extractor.

Useful after an extractor fix: it reads `raw/**/<doc_id>.html.gz` (no network),
recomputes effective/expiry date and document number, and rewrites the meta
file. Text from PDF attachments is kept as-is because the PDF is not stored.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import yaml

from extract import build_document, extract_effective_date, extract_expiry_date

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = Path("/srv/share/quantdb/policy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按当前抽取规则重建单篇元数据")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--config", type=Path, default=HERE / "sources.yaml")
    parser.add_argument("--source", default="all")
    args = parser.parse_args(argv)
    payload = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    sources = {row["id"]: row for row in payload["sources"]}
    changed = 0
    scanned = 0
    docno_changes = []
    for meta_path in sorted((args.root / "raw").glob("*/*/*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if args.source != "all" and meta["source_id"] != args.source:
            continue
        if meta.get("text_source") == "html+pdf":
            # PDF text is not reproducible offline; re-derive timeline fields
            # from the stored merged text only.
            text_path = args.root / meta["text_path"]
            if not text_path.exists():
                continue
            text = text_path.read_text(encoding="utf-8")
            scanned += 1
            new_effective = extract_effective_date(text, meta["publish_date"]) or "unknown"
            new_expiry = extract_expiry_date(text) or "unknown"
            if (meta["effective_date"], meta["expiry_date"]) != (new_effective, new_expiry):
                meta["effective_date"], meta["expiry_date"] = new_effective, new_expiry
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                changed += 1
            continue
        html_path = args.root / meta["html_gz_path"]
        if not html_path.exists():
            continue
        scanned += 1
        html = gzip.open(html_path, "rt", encoding="utf-8").read()
        doc = build_document(
            html=html,
            url=meta["url"],
            title=meta["title"],
            source=sources[meta["source_id"]],
            publish_hint=meta["publish_date"],
            meta={"title": meta["title"], "publish_date": meta["publish_date"]},
        )
        before = (meta["effective_date"], meta["expiry_date"], meta["document_number"])
        new_effective = doc["effective_date"] or "unknown"
        new_docno = meta["document_number"]
        candidate = doc["document_number"]
        if candidate and candidate != new_docno and len(candidate) >= 6:
            # A number taken from the title is authoritative: body searches can
            # pick up a referenced document instead of this one's own number.
            if (
                doc.get("docno_source") == "title"
                or new_docno in {"unknown", ""}
                or len(new_docno) <= 6
                or new_docno.startswith("第")
            ):
                docno_changes.append({"doc_id": meta["doc_id"], "old": new_docno, "new": candidate})
                new_docno = candidate
        if new_docno in {"unknown", ""} and doc["document_number"]:
            new_docno = doc["document_number"]
        after = (new_effective, doc["expiry_date"] or "unknown", new_docno)
        if before != after:
            meta["effective_date"], meta["expiry_date"], meta["document_number"] = after
            meta["docno_source"] = doc.get("docno_source") or ""
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            changed += 1
    print(
        json.dumps(
            {"scanned": scanned, "changed": changed, "docno_examples": docno_changes[:10]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
