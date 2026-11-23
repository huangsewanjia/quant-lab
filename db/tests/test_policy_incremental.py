"""Policy daily incremental and weekly reconciliation contracts."""

from __future__ import annotations

from db.policy import fetch_policy


SOURCE = {
    "id": "gov",
    "name": "中国政府网",
    "agency": "国务院",
    "base_url": "https://www.gov.cn",
    "content_selectors": ["#UCAP-CONTENT"],
}


def _html(title: str) -> str:
    body = (
        "为规范数据更新流程，现将有关事项通知如下。"
        "各有关单位应当按月核对数据分区、失败清单和备份校验结果，"
        "发现问题及时处置并留存记录。"
    )
    return (
        '<html><head><meta name="firstpublishedtime" content="2026-11-10-17:00:00" />'
        f"</head><body><div id=\"UCAP-CONTENT\"><p>{title}</p><p>{body}</p>"
        "<p>本通知自2026年11月10日起施行。</p></div></body></html>"
    )


class FakeFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str) -> str:
        self.urls.append(url)
        return _html(url)


class SameTextFetcher:
    def get(self, url: str) -> str:
        return _html("同一篇正文")


def _items() -> list[dict]:
    return [
        {"title": "已入库", "url": "https://www.gov.cn/old", "publish_hint": "2026-11-09"},
        {"title": "新条目一", "url": "https://www.gov.cn/new-1", "publish_hint": "2026-11-10"},
        {"title": "新条目二", "url": "https://www.gov.cn/new-2", "publish_hint": "2026-11-10"},
    ]


def test_daily_run_skips_known_urls_before_spending_budget(qdb, monkeypatch):
    index = {
        "generated_at": "2026-11-09T00:00:00Z",
        "documents": [{"url": "https://www.gov.cn/old", "sha256": "old"}],
    }
    monkeypatch.setattr(fetch_policy, "list_items", lambda source, fetcher: _items())
    fetcher = FakeFetcher()
    result = fetch_policy.run_source(
        SOURCE,
        fetcher,
        max_items=1,
        list_only=False,
        index=index,
    )
    assert result["known"] == 1
    assert result["stored"] == 1
    assert fetcher.urls == ["https://www.gov.cn/new-1"]
    assert result["remaining"] == 1


def test_reconcile_is_bounded_and_records_remaining(qdb, monkeypatch):
    index = {"generated_at": "2026-11-09T00:00:00Z", "documents": []}
    monkeypatch.setattr(fetch_policy, "list_items", lambda source, fetcher: _items())
    fetcher = FakeFetcher()
    summary = fetch_policy.reconcile(
        [SOURCE],
        fetcher,
        index,
        max_fetch=1,
    )
    assert summary["total_missing"] == 3
    assert summary["total_fetched"] == 1
    assert summary["total_remaining"] == 2
    assert (qdb.POLICY_DIR / "meta").exists()


def test_duplicate_text_url_is_persisted_as_alias(qdb):
    index = {"generated_at": "2026-11-09T00:00:00Z", "documents": []}
    by_sha: set[str] = set()
    by_url: set[str] = set()
    result = {"stored": 0, "duplicates": 0, "missing": 0, "errors": []}
    first = {
        "title": "原文",
        "url": "https://www.gov.cn/a",
        "publish_hint": "2026-11-10",
    }
    second = {
        "title": "转载",
        "url": "https://www.gov.cn/b",
        "publish_hint": "2026-11-10",
    }
    fetcher = SameTextFetcher()
    fetch_policy._store_item(first, SOURCE, fetcher, index, by_sha, by_url, result)
    fetch_policy._store_item(second, SOURCE, fetcher, index, by_sha, by_url, result)
    assert "https://www.gov.cn/b" in index["alias_urls"]
    assert "https://www.gov.cn/b" in fetch_policy._known_urls(index)
