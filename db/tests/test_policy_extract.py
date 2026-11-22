from __future__ import annotations

from db.policy.extract import (
    extract_effective_date,
    extract_publish_date,
    extract_text,
)


def test_extract_publish_and_effective_date():
    html = """
    <html><head>
      <meta name="firstpublishedtime" content="2026-11-10-17:00:00" />
    </head><body>
      <div id="UCAP-CONTENT">
        <p>《基金会管理条例》已经2026年9月11日国务院第96次常务会议修订通过，</p>
        <p>现予公布，自2027年1月1日起施行。</p>
      </div>
    </body></html>
    """
    text = extract_text(html, ["#UCAP-CONTENT"])
    publish = extract_publish_date(
        html, text, "https://www.gov.cn/zhengce/content/202611/content_1.htm"
    )
    effective = extract_effective_date(text, publish)
    assert publish == "2026-11-10"
    assert effective == "2027-01-01"
    assert "基金会管理条例" in text


def test_missing_effective_date_returns_none():
    text = "这是一条新闻，没有施行日期。"
    assert extract_effective_date(text, "2026-11-10") is None


def test_effective_from_publish_date():
    assert extract_effective_date("本通知自发布之日起施行。", "2026-11-10") == "2026-11-10"
