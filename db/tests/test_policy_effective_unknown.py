"""Missing effective_date is stored as ``unknown`` and queued for backfill."""

from __future__ import annotations

import json

from db.etl import common
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
        f'</head><body><div id="UCAP-CONTENT"><p>{title}</p><p>{body}</p>'
        "</div></body></html>"
    )


class NoEffectiveFetcher:
    def get(self, url: str) -> str:
        return _html(url)


def test_store_document_materialises_unknown_effective_date(qdb):
    doc = {
        "source_id": "gov",
        "source_name": "中国政府网",
        "agency": "国务院",
        "title": "没有生效日的通知",
        "url": "https://www.gov.cn/no-effective",
        "publish_date": "2026-11-10",
        "effective_date": None,
        "document_number": None,
        "sha256": "a" * 64,
        "text": "正文" * 100,
        "text_bytes": 400,
        "fetched_at": "2026-11-25T00:00:00Z",
    }
    meta = fetch_policy.store_document(doc)

    assert meta["effective_date"] == "unknown"
    assert meta["publish_date"] == "2026-11-10"
    stored = json.loads((qdb.QDB_HOME / meta["meta_path"]).read_text(encoding="utf-8"))
    assert stored["effective_date"] == "unknown"
    assert (qdb.QDB_HOME / meta["content_path"]).exists()


def test_missing_effective_date_is_stored_and_queued(qdb):
    index: dict = {"generated_at": "2026-11-25T00:00:00Z", "documents": []}
    result = {
        "stored": 0,
        "pending_effective": 0,
        "missing": 0,
        "duplicates": 0,
        "errors": [],
    }
    item = {
        "title": "没有生效日的通知",
        "url": "https://www.gov.cn/no-effective",
        "publish_hint": "2026-11-10",
    }

    status = fetch_policy._store_item(
        item, SOURCE, NoEffectiveFetcher(), index, set(), set(), result
    )

    assert status == "stored"
    assert result["stored"] == 1
    assert result["pending_effective"] == 1
    assert result["missing"] == 0
    meta = index["documents"][0]
    assert meta["effective_date"] == "unknown"

    missing_path = qdb.POLICY_DIR / "missing" / f"{common.today_tag()}.jsonl"
    records = [json.loads(line) for line in missing_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["reason"] == "缺 effective_date（已入库，待补生效日）"
    assert records[0]["doc_id"] == meta["sha256"][:16]
    assert records[0]["url"] == item["url"]


def test_backfill_missing_dedupes_and_stores(qdb):
    missing_path = qdb.POLICY_DIR / "missing" / "20261124.jsonl"
    records = [
        {
            "source": "gov",
            "title": "条目一",
            "url": "https://www.gov.cn/one",
            "publish_date": "2026-11-10",
            "reason": "缺 effective_date",
        },
        {
            "source": "gov",
            "title": "条目一重复",
            "url": "https://www.gov.cn/one",
            "publish_date": "2026-11-10",
            "reason": "缺 effective_date",
        },
        {
            "source": "gov",
            "title": "条目二",
            "url": "https://www.gov.cn/two",
            "publish_date": "2026-11-11",
            "reason": "缺 effective_date",
        },
    ]
    missing_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    index: dict = {"generated_at": "2026-11-25T00:00:00Z", "documents": []}

    summary = fetch_policy.backfill_missing(
        [SOURCE], NoEffectiveFetcher(), index, ["20261124"]
    )

    assert summary["total_records"] == 3
    assert summary["total_unique_urls"] == 2
    assert summary["total_stored"] == 2
    assert summary["total_pending_effective"] == 2
    assert summary["total_failed"] == 0
    assert summary["total_remaining"] == 0
    assert len(index["documents"]) == 2
    assert {doc["url"] for doc in index["documents"]} == {
        "https://www.gov.cn/one",
        "https://www.gov.cn/two",
    }
    report = json.loads(
        (qdb.POLICY_DIR / "meta" / "backfill-20261124.json").read_text(encoding="utf-8")
    )
    assert report["total_stored"] == 2
