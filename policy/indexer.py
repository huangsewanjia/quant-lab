"""Build meta/index.parquet + coverage stats from stored policy documents."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from tagging import tag_document

DEFAULT_ROOT = Path("/srv/share/quantdb/policy")
COLUMNS = [
    "doc_id",
    "source_id",
    "source_name",
    "source_column",
    "text_source",
    "title",
    "agency",
    "document_number",
    "publish_date",
    "effective_date",
    "expiry_date",
    "industry_tags",
    "topic_tags",
    "tag_basis",
    "url",
    "fetched_at",
    "sha256",
    "text_bytes",
    "text_chars",
    "reprint_count",
    "text_path",
    "html_gz_path",
]


def load_docs(root: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted((root / "raw").glob("*/*/*.meta.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] unreadable meta {path}: {exc}")
            continue
        docs.append(meta)
    return docs


def build_frame(docs: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for doc in docs:
        row = {key: doc.get(key) for key in COLUMNS}
        row["industry_tags"] = list(doc.get("industry_tags") or [])
        row["topic_tags"] = list(doc.get("topic_tags") or [])
        row["tag_basis"] = list(doc.get("tag_basis") or [])
        rows.append(row)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame = frame.sort_values(["source_id", "publish_date", "doc_id"]).reset_index(drop=True)
    return frame


def compute_stats(frame: pd.DataFrame, root: Path) -> dict[str, Any]:
    if frame.empty:
        return {"total": 0}
    by_source = frame.groupby("source_id")
    matrix: dict[str, dict[str, int]] = defaultdict(dict)
    years = [str(y) for y in range(2016, datetime.now(timezone.utc).year + 1)]
    for source, part in by_source:
        counts = Counter(part["publish_date"].str[:4])
        matrix[source] = {year: int(counts.get(year, 0)) for year in years}
    effective = {}
    for source, part in by_source:
        known = int((part["effective_date"] != "unknown").sum())
        effective[source] = {
            "total": int(len(part)),
            "known": known,
            "unknown": int(len(part) - known),
            "coverage": round(known / len(part), 4) if len(part) else 0.0,
        }
    industry = Counter()
    for tags in frame["industry_tags"]:
        for tag in tags:
            industry[tag] += 1
    unlabeled = int(sum(1 for tags in frame["industry_tags"] if tags == ["unlabeled"]))
    docno_unknown = int((frame["document_number"] == "unknown").sum())
    duplication = 0
    dup_log = root / "meta" / "duplicates.jsonl"
    if dup_log.exists():
        duplication = sum(1 for _ in dup_log.open("r", encoding="utf-8"))
    reject_reasons = Counter()
    reject_log = root / "meta" / "rejects.jsonl"
    if reject_log.exists():
        for line in reject_log.open("r", encoding="utf-8"):
            try:
                reject_reasons[json.loads(line)["reason"]] += 1
            except Exception:  # noqa: BLE001
                continue
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "total": int(len(frame)),
        "sources": sorted(frame["source_id"].unique().tolist()),
        "by_source_year": matrix,
        "effective_date": effective,
        "effective_unknown_total": int((frame["effective_date"] == "unknown").sum()),
        "industry_counts": dict(industry.most_common()),
        "unlabeled": unlabeled,
        "unlabeled_ratio": round(unlabeled / len(frame), 4),
        "document_number_unknown": docno_unknown,
        "reprints": duplication,
        "reject_reasons": dict(reject_reasons),
        "text_bytes_total": int(frame["text_bytes"].sum()),
    }


def write_outputs(frame: pd.DataFrame, stats: dict[str, Any], root: Path) -> None:
    meta_dir = root / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    parquet_tmp = meta_dir / "index.parquet.tmp"
    frame.to_parquet(parquet_tmp, index=False, engine="pyarrow")
    parquet_tmp.replace(meta_dir / "index.parquet")
    (meta_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    matrix = stats.get("by_source_year") or {}
    if matrix:
        (meta_dir / "source-year.csv").write_text(
            pd.DataFrame(matrix).fillna(0).astype(int).to_csv(), encoding="utf-8"
        )
    rows = [
        {
            "source_id": source,
            **values,
        }
        for source, values in (stats.get("effective_date") or {}).items()
    ]
    if rows:
        (meta_dir / "effective-coverage.csv").write_text(
            pd.DataFrame(rows).to_csv(index=False), encoding="utf-8"
        )


def sample_for_review(frame: pd.DataFrame, root: Path, n: int, seed: int) -> Path:
    rng = random.Random(seed)
    if len(frame) <= n:
        picked = frame
    else:
        picked = frame.iloc[rng.sample(range(len(frame)), n)]
    rows = []
    for _, row in picked.iterrows():
        text_path = root / row["text_path"]
        first = ""
        if text_path.exists():
            first = " / ".join(text_path.read_text(encoding="utf-8").split("\n")[:3])[:200]
        rows.append(
            {
                "doc_id": row["doc_id"],
                "source": row["source_id"],
                "title": row["title"],
                "document_number": row["document_number"],
                "publish_date": row["publish_date"],
                "effective_date": row["effective_date"],
                "industry_tags": "|".join(row["industry_tags"]),
                "url": row["url"],
                "first_paragraph": first,
                "manual_check": "",
            }
        )
    out = root / "meta" / f"sample-{n}.csv"
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    return out


def retag(root: Path) -> int:
    """Rewrite industry/topic tags from stored text without re-downloading."""
    changed = 0
    for path in sorted((root / "raw").glob("*/*/*.meta.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        text_path = root / meta["text_path"]
        if not text_path.exists():
            continue
        text = text_path.read_text(encoding="utf-8")
        tags = tag_document(meta["title"], text, meta.get("agency") or "")
        if (
            meta.get("industry_tags") == tags["industry_tags"]
            and meta.get("topic_tags") == tags["topic_tags"]
        ):
            continue
        meta["industry_tags"] = tags["industry_tags"]
        meta["topic_tags"] = tags["topic_tags"]
        meta["tag_basis"] = tags["tag_basis"]
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="政策库索引与回填统计")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--sample", type=int, default=0, help="额外导出人工抽查样本")
    parser.add_argument("--seed", type=int, default=515)
    parser.add_argument("--retag", action="store_true", help="按当前规则重打行业/主题标签")
    args = parser.parse_args(argv)
    if args.retag:
        print("retagged", retag(args.root))
    docs = load_docs(args.root)
    frame = build_frame(docs)
    stats = compute_stats(frame, args.root)
    write_outputs(frame, stats, args.root)
    print(json.dumps(stats, ensure_ascii=False, indent=2)[:4000])
    if args.sample:
        print("sample ->", sample_for_review(frame, args.root, args.sample, args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
