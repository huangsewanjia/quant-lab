"""WORK-623 采集器离线测试：解析、落盘、失败台账、清单页（不联网、不启动浏览器）。"""
from __future__ import annotations

import importlib.util
import json
import types
from pathlib import Path

import pytest
from pypdf import PdfWriter

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fxbaogao_fulltext.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fxbaogao_fulltext", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = load_module()

DETAIL_HTML = """<html><head>
<title>开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告</title>
<meta property="og:title" content="开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告"/>
<meta name="keywords" content="开源证券,宁德时代,研究报告,报告下载"/>
<meta property="article:published_time" content="2026-11-16 10:15:02 CST (+08:00)"/>
</head><body></body></html>"""

LISTING_HTML = """
<div class="card"><a target="_blank" href="/detail/5884540" style="">
<h3 class="text-ellipsis-2 docTitleHover">2026年中国制造业GEO行业技术白皮书</h3></a>
<div><span class="tag">机械设备</span><span>北京百云腾</span><span>2026-07-01</span></div></div>
<div class="card"><a target="_blank" href="/detail/5578594" style="">
<h3 class="text-ellipsis-2 docTitleHover">消费决策场景AI搜索洞察报告</h3></a>
<div><span>艾瑞咨询</span><span>2026-08-05</span></div></div>
<a href="/detail/5884540">重复链接应被去重</a>
"""


class FakeProbe:
    @staticmethod
    def robots_allows(rules, path):
        for pattern, allow in rules:
            if path.startswith(pattern):
                return allow, ("Allow:" if allow else "Disallow:") + pattern
        return True, "无匹配规则(允许)"


class FakeDriver:
    cdp_url = "http://127.0.0.1:9222"

    def __init__(self, pages=2, fail_ids=(), title_template="开源证券:《测试报告{rid}》-发现报告",
                 href=None):
        self.pages = pages
        self.fail_ids = set(fail_ids)
        self.title_template = title_template
        self.href = href

    def login_state(self):
        return {"logged_in": True, "advanced_vip": True, "vip_time": "2027-11-01T00:00:00.000Z"}

    def download(self, url, dest, selectors=None, path_guard=None):
        rid = url.rstrip("/").split("/")[-1]
        if rid in self.fail_ids:
            return {"ok": False, "reason": "download_control_not_found", "html": "<html>no control</html>"}
        if self.href and path_guard is not None:
            allowed, hit = path_guard(self.href)
            if not allowed:
                return {"ok": False, "reason": f"下载控件指向 robots 禁止路径: {hit}",
                        "control_href": self.href}
        dest.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        for _ in range(self.pages):
            writer.add_blank_page(width=595, height=842)
        with open(dest, "wb") as fh:
            writer.write(fh)
        return {"ok": True, "page_meta": {
            "title": self.title_template.format(rid=rid),
            "keywords": "开源证券,测试",
            "published": "2026-11-16 10:15:02 CST (+08:00)"}}

    def close(self):
        pass


def test_parse_detail_extracts_title_org_date():
    parsed = fx.parse_detail(DETAIL_HTML)
    assert parsed["title"].endswith("-发现报告")
    assert parsed["org"] == "开源证券"
    assert parsed["page_date"] == "2026-11-16"


def test_parse_candidates_dedupes_and_keeps_org_date():
    items = fx.parse_candidates(LISTING_HTML)
    assert [i["report_id"] for i in items] == ["5884540", "5578594"]
    assert items[0]["url"] == "https://www.fxbaogao.com/detail/5884540"
    assert items[0]["title"].endswith("技术白皮书")
    assert items[0]["org"] == "北京百云腾"
    assert items[0]["page_date"] == "2026-07-01"
    assert items[1]["org"] == "艾瑞咨询"


def test_dest_path_uses_page_date_and_keeps_cjk():
    dest = fx.dest_path(Path("/r"), "2026-11-16", "5884540", "艾奇在线:《GEO 报告》-发现报告")
    assert dest == Path("/r/2026-11-16/5884540-艾奇在线-GEO-报告.pdf")


def test_default_selectors_never_target_pdf_path():
    assert not any("/pdf" in sel for sel in fx.DEFAULT_SELECTORS)


def test_download_batch_blocks_control_pointing_to_robots_disallowed_path(tmp_path):
    root = tmp_path / "fxbaogao"
    driver = FakeDriver(pages=1, href="/pdf/5884540")
    stats = fx.download_batch(
        driver, [{"report_id": "5884540", "url": "https://www.fxbaogao.com/detail/5884540"}],
        root, FakeProbe, limit=1, gap=0,
        robots_lookup=lambda u: {"rules": [("/pdf", False)], "robots_status": 200},
        sleep=lambda _s: None)
    assert stats["ok"] == 0 and stats["failed"] == 1
    rec = json.loads((root / "failed.jsonl").read_text(encoding="utf-8"))
    assert "robots 禁止路径" in rec["reason"] and rec["report_id"] == 5884540
    assert not list(root.glob("*/5884540-*.pdf"))


def test_download_batch_removes_stale_file_for_same_report(tmp_path):
    root = tmp_path / "fxbaogao"
    first = FakeDriver(pages=1, title_template="开源证券:《报告A》-发现报告")
    fx.download_batch(first, [{"report_id": "9", "url": "https://www.fxbaogao.com/detail/9"}],
                      root, FakeProbe, limit=1, gap=0,
                      robots_lookup=lambda u: {"rules": [], "robots_status": 200},
                      sleep=lambda _s: None)
    second = FakeDriver(pages=1, title_template="开源证券:《报告A（修订版）》-发现报告")
    fx.download_batch(second, [{"report_id": "9", "url": "https://www.fxbaogao.com/detail/9"}],
                      root, FakeProbe, limit=1, gap=0,
                      robots_lookup=lambda u: {"rules": [], "robots_status": 200},
                      sleep=lambda _s: None)
    pdfs = list(root.glob("*/9-*.pdf"))
    metas = list(root.glob("*/9-*.meta.json"))
    assert len(pdfs) == 1 and len(metas) == 1
    assert "修订版" in pdfs[0].name


def _stub_driver(href, download_bytes=b"%PDF-1.4\n%%EOF\n"):
    """给 CdpDriver.download 用的最小 page/locator stub，不依赖 Playwright。"""

    class Control:
        def __init__(self):
            self.clicked = 0

        def is_visible(self):
            return True

        def get_attribute(self, name):
            return href if name == "href" else None

        def click(self):
            self.clicked += 1

    class Locator:
        def __init__(self, control):
            self.control = control
            self.first = self

        def count(self):
            return 1

        def is_visible(self):
            return self.control.is_visible()

        def get_attribute(self, name):
            return self.control.get_attribute(name)

        def click(self):
            self.control.click()

    class Download:
        suggested_filename = "x.pdf"

        def save_as(self, path):
            with open(path, "wb") as fh:
                fh.write(download_bytes)

    class Pending:
        value = Download()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Page:
        def __init__(self, control):
            self.control = control

        def locator(self, selector):
            return Locator(self.control)

        def goto(self, *a, **k):
            return None

        def wait_for_timeout(self, _ms):
            return None

        def evaluate(self, _js):
            return {"title": "开源证券:《报告》-发现报告", "keywords": "开源证券", "published": "2026-11-16"}

        def content(self):
            return "<html></html>"

        def expect_download(self, timeout=None):
            return Pending()

        def close(self):
            return None

    class Ctx:
        def __init__(self, control):
            self.page = Page(control)

        def new_page(self):
            return self.page

    control = Control()
    driver = object.__new__(fx.CdpDriver)
    driver._ctx = Ctx(control)
    driver._settle_ms = 0
    return driver, control


def test_cdpdriver_blocks_click_on_robots_disallowed_href(tmp_path):
    driver, control = _stub_driver("/pdf/5884540")
    dest = tmp_path / "x.pdf"
    guard = lambda href: (False, "Disallow:/pdf") if href.startswith("/pdf") else (True, "ok")  # noqa: E731
    result = driver.download("https://www.fxbaogao.com/detail/5884540", dest, path_guard=guard)
    assert result["ok"] is False and "robots 禁止路径" in result["reason"]
    assert control.clicked == 0 and not dest.exists()


def test_cdpdriver_allows_click_when_href_is_permitted(tmp_path):
    driver, control = _stub_driver("/detail/5884540")
    dest = tmp_path / "x.pdf"
    guard = lambda href: (False, "Disallow:/pdf") if href.startswith("/pdf") else (True, "ok")  # noqa: E731
    result = driver.download("https://www.fxbaogao.com/detail/5884540", dest, path_guard=guard)
    assert result["ok"] is True and control.clicked == 1 and dest.exists()


def test_download_batch_does_not_delete_inbox_files(tmp_path):
    root = tmp_path / "fxbaogao"
    inbox = root / "_inbox" / "7-待收编.pdf"
    inbox.parent.mkdir(parents=True)
    inbox.write_bytes(b"%PDF-1.4 manual")
    fx.download_batch(FakeDriver(pages=1), [{"report_id": "7", "url": "https://www.fxbaogao.com/detail/7"}],
                      root, FakeProbe, limit=1, gap=0,
                      robots_lookup=lambda u: {"rules": [], "robots_status": 200},
                      sleep=lambda _s: None)
    assert inbox.exists()


def test_slug_algorithms_agree_between_collector_and_etl():
    from db.etl import report_fulltext as rf

    titles = ["艾奇在线:《GEO & SEO 报告》-发现报告",
              "开源证券:《宁德时代(300750)：Q3业绩超预期》-发现报告",
              "消费决策场景AI搜索洞察——2026年重点行业GEO差异化策略研究报告",
              "报告/带 空格 与*星号*",
              "   "]
    for title in titles:
        assert fx.slugify(title) == rf._slug(title), title


def test_check_robots_treats_unreadable_as_denied():
    assert fx.check_robots(FakeProbe, {"rules": None, "robots_status": 403},
                           "https://www.fxbaogao.com/detail/1") == (
        False, "robots.txt 不可读（HTTP 403）")
    assert fx.check_robots(FakeProbe, {"rules": [("/pdf", False)], "robots_status": 200},
                           "https://www.fxbaogao.com/detail/1")[0] is True


def test_ledger_creates_missing_directory(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "work537_probe", SCRIPT.with_name("work537_probe.py"))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    target = tmp_path / "newdir" / "request_ledger.json"
    probe.LEDGER_PATH = str(target)
    probe.LEDGER = [{"url": "https://example.invalid/", "ts": "2026-11-27T00:00:00+08:00"}]
    probe._save_ledger()
    assert target.exists()


def test_dest_path_falls_back_when_date_is_not_iso():
    dest = fx.dest_path(Path("/r"), "2026/11/16", "1", "t")
    assert dest.parent.name != "2026/11/16"
    assert len(dest.parent.name) == 10


def test_download_batch_writes_meta_and_failed_log(tmp_path):
    root = tmp_path / "fxbaogao"
    items = [
        {"report_id": "1", "url": "https://www.fxbaogao.com/detail/1", "title": "x"},
        {"report_id": "2", "url": "https://www.fxbaogao.com/detail/2", "title": "y"},
        {"report_id": "3", "url": "https://www.fxbaogao.com/view/3", "title": "z"},
    ]
    driver = FakeDriver(pages=2, fail_ids={"2"})
    stats = fx.download_batch(
        driver, items, root, FakeProbe,
        limit=10, gap=0, selectors=None, success_log="",
        robots_lookup=lambda url: {"rules": [("/view", False)], "robots_status": 200},
        sleep=lambda _s: None)
    assert stats["ok"] == 1 and stats["failed"] == 2 and stats["skipped_robots"] == 1
    pdf = Path(stats["destinations"][0])
    assert pdf.name.startswith("1-") and pdf.parent.name == "2026-11-16"
    meta = json.loads(pdf.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["pages"] == 2 and meta["fetch_mode"] == "cdp"
    assert meta["org"] == "开源证券" and meta["page_date"] == "2026-11-16"
    assert meta["sha256"] == fx.sha256_file(pdf)
    lines = [json.loads(l) for l in (root / "failed.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    for rec in lines:
        assert set(rec) >= {"report_id", "url", "http_status", "reason", "path", "ts"}
    assert any("robots 禁止" in rec["reason"] for rec in lines)
    diag = root / "_diagnostics" / "2.html"
    assert diag.exists() and "no control" in diag.read_text(encoding="utf-8")


def test_download_batch_rejects_non_pdf(tmp_path):
    root = tmp_path / "fxbaogao"
    driver = FakeDriver()
    def fake_download(url, dest, selectors=None, path_guard=None):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"<html>login</html>")
        return {"ok": True, "page_meta": {"title": "t", "keywords": "", "published": ""}}

    driver.download = fake_download
    stats = fx.download_batch(driver, [{"report_id": "9", "url": "https://www.fxbaogao.com/detail/9"}],
                              root, FakeProbe, limit=1, gap=0, robots_lookup=lambda u: {"rules": [], "robots_status": 200},
                              sleep=lambda _s: None)
    assert stats["ok"] == 0 and stats["failed"] == 1
    assert "PDF 校验失败" in json.loads((root / "failed.jsonl").read_text(encoding="utf-8"))["reason"]


def test_assert_advanced_vip_writes_evidence(tmp_path):
    class NoVip:
        cdp_url = "http://127.0.0.1:9222"

        def login_state(self):
            return {"logged_in": True, "advanced_vip": False, "standard_vip": False}

    record = fx._assert_advanced_vip(NoVip(), tmp_path, allow_standard=False)
    assert record["ok"] is False
    saved = json.loads((tmp_path / "login_state.json").read_text(encoding="utf-8"))
    assert saved["login_state"]["advanced_vip"] is False
    record2 = fx._assert_advanced_vip(NoVip(), tmp_path, allow_standard=False)
    assert record2["ok"] is False


def test_sheet_is_single_file_and_escapes_titles(tmp_path):
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "report_id": "42", "url": "https://www.fxbaogao.com/detail/42",
        "title": "艾奇在线:《GEO & SEO <script>alert(1)</script>》-发现报告",
        "org": "艾奇在线", "page_date": "2026-11-16"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    out = tmp_path / "sheet.html"
    code = fx.main(["sheet", "--input", str(queue), "--out", str(out),
                    "--root", str(tmp_path / "root")])
    assert code == 0
    doc = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc
    assert "42-艾奇在线-GEO-SEO-script-alert-1-script.pdf" in doc
    assert "src=" not in doc.replace("src=\"\"", "")  # 无外链资源
    assert "http://" not in doc.split("<body>")[0]
