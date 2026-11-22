"""Tests that do not touch the network or the production data root."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import Store, is_strong_docno, normalize_docno  # noqa: E402
from extract import (  # noqa: E402
    build_document,
    clean_title,
    docno_from_title,
    extract_effective_date,
    extract_expiry_date,
    split_title_docno,
)
from tagging import tag_document  # noqa: E402

SOURCE = {
    "id": "gov_gw",
    "name": "测试源",
    "agency": "国务院",
    "content_selectors": ["#content"],
}


def html(body: str, title: str = "测试政策") -> str:
    return (
        "<html><head><title>%s</title>"
        "<meta name='PubDate' content='2026-03-01'></head>"
        "<body><h1>%s</h1><div id='content'><p>%s</p></div></body></html>"
    ) % (title, title, body)


def doc(**overrides):
    base = {
        "title": "关于印发测试管理办法的通知",
        "url": "https://example.gov.cn/a/2026/t20260301_1.html",
        "publish_date": "2026-03-01",
        "effective_date": "2026-05-01",
        "expiry_date": None,
        "document_number": "国办发〔2026〕7号",
        "agency": "国务院",
        "source_column": "通知",
        "sha256": "a" * 64,
        "text": "正文" * 300,
        "text_bytes": 3600,
        "text_chars": 600,
        "fetched_at": "2026-03-02T00:00:00Z",
        "html": "<html></html>",
    }
    base.update(overrides)
    return base


def test_effective_date_from_explicit_clause():
    text = "本办法自2026年5月1日起施行，有效期至2031年4月30日。"
    assert extract_effective_date(text, "2026-03-01") == "2026-05-01"
    assert extract_expiry_date(text) == "2031-04-30"


def test_effective_date_never_inferred_from_publish_date():
    text = "各有关单位：现将有关事项通知如下。"
    assert extract_effective_date(text, "2026-03-01") is None


def test_effective_from_publication_clause():
    text = "本办法自发布之日起施行。\n2026年3月1日"
    assert extract_effective_date(text, "2026-03-01") == "2026-03-01"


def test_publication_clause_without_matching_date_is_unknown():
    # Website release date is not the signing date; do not substitute it.
    text = "本办法自公布之日起施行。\n2025年12月20日"
    assert extract_effective_date(text, "2026-01-05") is None


def test_docno_and_title_cleanup():
    title, docno = split_title_docno("《对外投资管理办法》 2026年第49号令")
    assert title == "《对外投资管理办法》"
    assert docno == "2026年第49号令"
    assert docno_from_title("中国人民银行令〔2026〕第4号（关于修改部分规章的决定）") == (
        "中国人民银行令〔2026〕第4号"
    )
    assert docno_from_title("国家发展改革委直属单位涉企收费公示表 2019年第1号公告") == (
        "2019年第1号"
    )
    assert docno_from_title(
        "中国人民银行 国家发展和改革委员会 财政部 中国证券监督管理委员会令〔2019〕第5号"
    ) == "中国人民银行国家发展和改革委员会财政部中国证券监督管理委员会令〔2019〕第5号"
    assert clean_title("关于X的通知 | 国家发展和改革委员会") == "关于X的通知"


def test_missing_effective_date_becomes_unknown_in_meta(tmp_path):
    store = Store(root=tmp_path, source=SOURCE)
    doc_ = build_document(
        html=html("各有关单位：现将有关事项通知如下。" * 100, "关于测试事项的通知"),
        url="https://example.gov.cn/zhengce/202603/content_1.htm",
        title="关于测试事项的通知",
        source=SOURCE,
        publish_hint="2026-03-01",
    )
    doc_["html"] = "<html/>"
    assert store.store(doc_) == "stored"
    meta = next(tmp_path.glob("raw/gov_gw/2026/*.meta.json"))
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["publish_date"] == "2026-03-01"
    assert payload["effective_date"] == "unknown"
    assert payload["document_number"] == "unknown"


def test_duplicate_sha_is_recorded_with_reprint_of(tmp_path):
    store = Store(root=tmp_path, source=SOURCE)
    assert store.store(doc()) == "stored"
    again = doc(
        title="同一正文转载件",
        url="https://example.gov.cn/other/2026/t20260302_2.html",
        document_number="国办发〔2026〕99号",
    )
    assert store.store(again) == "dup_sha"
    lines = (tmp_path / "meta" / "duplicates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["matched"] == "sha256"
    assert len(row["duplicate_of"]) == 16


def test_duplicate_document_number_is_marked(tmp_path):
    store = Store(root=tmp_path, source=SOURCE)
    assert store.store(doc()) == "stored"
    again = doc(
        title="同一文号的另一份转载",
        url="https://example.gov.cn/other/2026/t20260303_3.html",
        sha256="b" * 64,
    )
    assert store.store(again) == "dup_docno"
    row = json.loads((tmp_path / "meta" / "duplicates.jsonl").read_text(encoding="utf-8"))
    assert row["matched"] == "document_number"


def test_short_body_rejected(tmp_path):
    store = Store(root=tmp_path, source=SOURCE)
    assert store.store(doc(text="短", text_chars=1)) == "rejected"
    reasons = (tmp_path / "meta" / "rejects.jsonl").read_text(encoding="utf-8")
    assert "正文过短" in reasons


def test_weak_docno_not_used_for_dedupe():
    assert not is_strong_docno("第49号")
    assert not is_strong_docno("unknown")
    assert is_strong_docno("国办发〔2026〕7号")
    assert normalize_docno("国办发 〔2026〕 7号") == "国办发〔2026〕7号"


def test_tagging_marks_unlabeled_instead_of_guessing():
    tags = tag_document("关于加强档案管理的通知", "现就档案管理工作通知如下。" * 5, "国务院")
    assert tags["industry_tags"] == ["unlabeled"]
    assert tags["tag_basis"] == ["none"]


def test_tagging_title_hit_and_multilabel():
    tags = tag_document(
        "关于促进新型储能高质量发展的指导意见",
        "新型储能是构建新型电力系统的重要支撑。" * 3,
        "国家发展改革委",
    )
    assert "电力设备" in tags["industry_tags"]
    assert "规划计划" in tags["topic_tags"]


def test_tagging_body_single_mention_ignored():
    tags = tag_document(
        "关于加强机关财务管理的通知",
        "本通知规范报销流程。" * 5 + "另提及一次储能。",
        "国务院",
    )
    assert "电力设备" not in tags["industry_tags"]
