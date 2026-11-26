#!/usr/bin/env python3
"""Minimal WeKnora REST client for the policy-library prune workflow.

Auth is `X-API-Key` (NOT `Authorization: Bearer`) — the workspace MCP entry does
not forward the header, so this module talks REST directly. The key is never
read from a file in the repo: it is decrypted from the systemd credential store.

Env:
  WEKNORA_BASE_URL  default http://127.0.0.1:8080/api/v1
  WEKNORA_API_KEY   optional override
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BASE = os.environ.get("WEKNORA_BASE_URL", "http://127.0.0.1:8080/api/v1").rstrip("/")
CRED = os.path.expanduser("~/.config/multica/secrets/WEKNORA_API_KEY.cred")

MACRO_KB = "6c461050-3cd3-4eb1-8775-ac3effd2cb09"      # 政策·宏观
ARCHIVE_KB = "308ead6b-636b-4937-addd-b1ddf6271a95"    # 政策原文库（存档，只读）


def api_key():
    key = (os.getenv("WEKNORA_API_KEY") or "").strip()
    if key:
        return key
    return subprocess.run(
        ["systemd-creds", "--user", "decrypt", CRED, "-"],
        capture_output=True, text=True, check=True).stdout.strip()


_KEY = None


def call(method, path, payload=None, retries=4):
    global _KEY
    if _KEY is None:
        _KEY = api_key()
    url = BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"X-API-Key": _KEY, "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return e.code, json.loads(body) if body else {}
            except Exception:
                return e.code, {"raw": body[:500]}
    raise RuntimeError("unreachable")


def list_kb_docs(kb_id, page_size=200, want_content=True):
    """Yield every document object in a knowledge base."""
    page = 1
    total = None
    seen = 0
    while True:
        st, d = call("GET", f"/knowledge-bases/{kb_id}/knowledge?page={page}&page_size={page_size}")
        if st != 200:
            raise RuntimeError(f"list failed {st}: {d}")
        if total is None:
            total = d.get("total", 0)
        rows = d.get("data") or []
        if not rows:
            return
        for it in rows:
            if not want_content:
                it["metadata"] = {}
            yield it
            seen += 1
        if seen >= total:
            return
        page += 1


def get_doc(doc_id):
    st, d = call("GET", f"/knowledge/{doc_id}")
    if st != 200:
        raise RuntimeError(f"get {doc_id} failed {st}: {d}")
    return d["data"]


def body_after_header(content):
    marker = "## 正文"
    idx = content.find(marker)
    return content[idx + len(marker):].strip() if idx >= 0 else content.strip()
