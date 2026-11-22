"""Push policy documents into the WeKnora knowledge base "政策原文库".

The MCP endpoint cannot create a knowledge base, so the KB is created once via
the WeKnora REST API (``POST /knowledge-bases``) and this script only adds
documents. Each document is added as editable Markdown with the dual timeline
in the header, so retrieval answers can cite publish_date and effective_date
separately.

Resume is idempotent: titles already present in the KB are skipped, and every
attempt is appended to ``meta/weknora-sync.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DEFAULT_ROOT = Path("/srv/share/quantdb/policy")
DEFAULT_BASE = os.environ.get("WEKNORA_BASE_URL", "https://weknora.kbreminder.com/api/v1")
DEFAULT_KB = "308ead6b-636b-4937-addd-b1ddf6271a95"
CRED_PATH = Path.home() / ".config/multica/secrets/WEKNORA_API_KEY.cred"


def load_key() -> str:
    value = (os.getenv("WEKNORA_API_KEY") or "").strip()
    if value:
        return value
    out = subprocess.run(
        ["systemd-creds", "--user", "decrypt", str(CRED_PATH), "-"],
        capture_output=True,
        check=True,
        text=True,
    )
    return out.stdout.strip()


class WeKnora:
    def __init__(self, base: str, key: str) -> None:
        self.base = base.rstrip("/")
        self.key = key
        self.local = threading.local()

    @property
    def session(self) -> requests.Session:
        if not hasattr(self.local, "session"):
            session = requests.Session()
            session.headers.update({"X-API-Key": self.key, "Accept": "application/json"})
            self.local.session = session
        return self.local.session

    def call(self, method: str, path: str, payload: dict | None = None, timeout: int = 120):
        for attempt in range(3):
            try:
                resp = self.session.request(
                    method, self.base + path, json=payload, timeout=timeout
                )
                if resp.status_code >= 500 and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                try:
                    body = resp.json()
                except Exception:  # noqa: BLE001
                    body = {"raw": resp.text[:300]}
                return resp.status_code, body
            except requests.RequestException as exc:
                if attempt == 2:
                    return 0, {"error": f"{type(exc).__name__}: {exc}"}
                time.sleep(2 * (attempt + 1))
        return 0, {"error": "unreachable"}

    def list_titles(self, kb_id: str) -> set[str]:
        titles: set[str] = set()
        page = 1
        while True:
            status, body = self.call(
                "GET", f"/knowledge-bases/{kb_id}/knowledge?page={page}&page_size=100"
            )
            if status != 200:
                raise RuntimeError(f"list_documents failed: HTTP {status} {body}")
            rows = body.get("data") or []
            if not rows:
                break
            for row in rows:
                titles.add(row.get("title") or "")
            if len(rows) < 100:
                break
            page += 1
        return titles

    def add(self, kb_id: str, title: str, content: str):
        return self.call(
            "POST",
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            {
                "title": title,
                "content": content,
                "status": "publish",
                "channel": "work-515-policy",
            },
            timeout=180,
        )


def render(row: pd.Series, text: str) -> str:
    industries = "、".join(row["industry_tags"]) or "unlabeled"
    topics = "、".join(row["topic_tags"]) or "-"
    header = [
        f"# {row['title']}",
        "",
        f"- 发文机关：{row['agency']}",
        f"- 文号：{row['document_number']}",
        f"- 发布日：{row['publish_date']}",
        f"- 生效日：{row['effective_date']}",
        f"- 失效日：{row['expiry_date']}",
        f"- 行业标签：{industries}",
        f"- 主题标签：{topics}",
        f"- 原文链接：{row['url']}",
        f"- doc_id：{row['doc_id']}",
        "",
        "> 生效日为 unknown 表示原文未写明施行日期，不得用发布日代替。",
        "",
        "## 正文",
        "",
    ]
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return "\n".join(header) + body


def doc_title(row: pd.Series) -> str:
    return f"{row['title']}（{row['publish_date']}）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步政策原文到 WeKnora")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--kb-id", default=DEFAULT_KB)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--limit", type=int, default=0, help="最多提交条数，0 为全部")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--industry-first", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    index = args.root / "meta" / "index.parquet"
    frame = pd.read_parquet(index)
    # Prefer industry-tagged documents, then the rest by publish date.
    frame = frame.assign(
        _pri=frame["industry_tags"].apply(
            lambda tags: 1 if list(tags) == ["unlabeled"] else 0
        )
    ).sort_values(["_pri", "publish_date", "doc_id"])
    if args.limit:
        frame = frame.head(args.limit)

    client = WeKnora(args.base, load_key())
    existing = set() if args.dry_run else client.list_titles(args.kb_id)
    print(f"[weknora] kb={args.kb_id} existing={len(existing)} candidates={len(frame)}")
    log_path = args.root / "meta" / "weknora-sync.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    counts = {"ok": 0, "skip": 0, "fail": 0}

    todo = []
    for _, row in frame.iterrows():
        title = doc_title(row)
        if title in existing:
            counts["skip"] += 1
            continue
        todo.append((title, row))
    print(f"[weknora] to_add={len(todo)} skip={counts['skip']}")
    if args.dry_run:
        for title, _ in todo[:10]:
            print("  ", title)
        return 0

    def work(title: str, row: pd.Series) -> tuple[str, str, dict]:
        text_path = args.root / row["text_path"]
        if not text_path.exists():
            return title, "fail", {"error": "text missing"}
        content = render(row, text_path.read_text(encoding="utf-8"))
        status, body = client.add(args.kb_id, title, content)
        if status == 200:
            kid = (body.get("data") or {}).get("id")
            return title, "ok", {"knowledge_id": kid}
        return title, "fail", {"http": status, "body": str(body)[:300]}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, title, row) for title, row in todo]
        for i, future in enumerate(as_completed(futures), 1):
            title, outcome, detail = future.result()
            with lock:
                counts[outcome] += 1
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "title": title,
                                "status": outcome,
                                **detail,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            if i % 50 == 0:
                print(f"[weknora] {i}/{len(todo)} {counts}", flush=True)
    print(json.dumps(counts, ensure_ascii=False))
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
