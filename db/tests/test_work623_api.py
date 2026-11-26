"""WORK-623 官方 API 采集器离线测试（不联网、不需要真 token）。"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fxbaogao_fulltext.py"
CST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
MS_2026_11_16 = int(__import__("datetime").datetime(2026, 11, 16, tzinfo=CST).timestamp() * 1000)


def load_module():
    spec = importlib.util.spec_from_file_location("fxbaogao_fulltext", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = load_module()

SEARCH_OK = {"status": 200, "body": json.dumps({"code": 200, "msg": "ok", "data": {
    "dataList": [{"reportId": 5884540, "title": "储能行业深度：市场化拐点",
                  "orgName": "开源证券", "publishTime": MS_2026_11_16},
                 {"reportId": "5884541", "reportTitle": "光伏月报",
                  "orgShortName": "华创证券", "publishDate": "2026-11-20"}]}}),
    "bytes": 200, "headers": {}, "error": "", "raw": b""}

PDF_BYTES = None


def make_pdf_bytes(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class FakeProbe:
    def __init__(self, routes, robots=None):
        self.routes = routes
        self.calls = []
        self.robots = robots if robots is not None else {"rules": [], "robots_status": 200}

    def http_request(self, url, method="GET", body=None, headers=None, ua=None,
                     timeout=20, binary=False):
        self.calls.append({"url": url, "method": method, "body": body,
                           "headers": headers or {}, "binary": binary})
        for key, resp in self.routes:
            if key in url:
                out = dict(resp)
                out.setdefault("headers", {})
                out.setdefault("error", "")
                out.setdefault("bytes", len(out.get("body", "") or out.get("raw", b"")))
                return out
        return {"status": 404, "body": "", "bytes": 0, "error": "no route", "headers": {}, "raw": b""}

    def http_get(self, url, **kwargs):
        return self.http_request(url)

    def get_robots_cached(self, url):
        return self.robots

    @staticmethod
    def robots_allows(rules, path):
        for pattern, allow in rules:
            if path.startswith(pattern):
                return allow, ("Allow:" if allow else "Disallow:") + pattern
        return True, "无匹配规则(允许)"


def pdf_route(pages=2, status=200, body=b""):
    raw = make_pdf_bytes(pages)
    return {"status": status, "body": "", "bytes": len(raw), "headers": {},
            "error": "", "raw": raw if not body else body}


def download_ok(url="https://dr.fxbaogao.com/x.pdf"):
    return {"status": 200, "body": json.dumps({"code": 200, "msg": "ok",
                                               "data": {"pdfUrl": url, "name": "x"}}),
            "bytes": 100, "headers": {}, "error": "", "raw": b""}


@pytest.fixture()
def token_file(tmp_path):
    path = tmp_path / "fxbaogao_api_key"
    path.write_text("sk-test-1234567890\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_norm_date_variants():
    assert fx.norm_date(MS_2026_11_16) == "2026-11-16"
    assert fx.norm_date(str(MS_2026_11_16 // 1000)) == "2026-11-16"
    assert fx.norm_date("2026-11-20") == "2026-11-20"
    assert fx.norm_date("2026年11月3日") == "2026-11-03"
    assert fx.norm_date("not a date") == ""
    assert fx.norm_date(None) == ""


def test_normalize_report_variants_and_reject():
    assert fx.normalize_report({"reportId": 1, "title": "t", "orgName": "o",
                                "publishTime": MS_2026_11_16}) == {
        "report_id": "1", "title": "t", "org": "o", "page_date": "2026-11-16",
        "url": "https://www.fxbaogao.com/view?id=1",
        "raw_item": {"reportId": 1, "title": "t", "orgName": "o",
                     "publishTime": MS_2026_11_16}}
    assert fx.normalize_report({"report_id": "2", "reportTitle": "r", "org": "x",
                                "date": "2026-11-20"})["report_id"] == "2"
    assert fx.normalize_report({"id": "abc", "title": "t"}) is None
    assert fx.normalize_report({"title": "no id"}) is None
    assert fx.normalize_report("not a dict") is None


def test_iter_report_items_shapes():
    assert len(fx.iter_report_items([{"reportId": 1}])) == 1
    assert len(fx.iter_report_items({"dataList": [{"reportId": 1}]})) == 1
    assert len(fx.iter_report_items({"list": [{"reportId": 2}]})) == 1
    assert fx.iter_report_items({"other": 1}) == []
    assert fx.iter_report_items(None) == []


def test_extract_pdf_urls_relative_and_dedupe():
    data = {"pdfUrl": "https://dr.fxbaogao.com/a.pdf",
            "nested": {"fileUrl": "/b.pdf"},
            "list": [{"downloadUrl": "https://x.example/c.pdf"}, {"url": "not-a-url"}]}
    urls = fx.extract_pdf_urls(data)
    by_field = {u["field"]: u["url"] for u in urls}
    assert by_field["pdfUrl"] == "https://dr.fxbaogao.com/a.pdf"
    assert by_field["fileUrl"] == "https://dr.fxbaogao.com/b.pdf"
    assert by_field["downloadUrl"] == "https://x.example/c.pdf"
    assert len(urls) == 3


def test_api_call_accepts_string_code(tmp_path, token_file, monkeypatch):
    ok = {"status": 200, "body": json.dumps({"code": "200", "msg": "ok", "data": {}}),
          "bytes": 40, "headers": {}, "error": "", "raw": b""}
    probe = FakeProbe([("/mofoun/agent/search", ok)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    assert fx.main(["check-auth", "--root", str(tmp_path / "r"), "--token-file", str(token_file)]) == 0


def test_search_pages_out_of_range_rejected(tmp_path, token_file, monkeypatch):
    probe = FakeProbe([("/mofoun/agent/search", SEARCH_OK)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    for pages in ("0", "11"):
        assert fx.main(["search", "--root", str(tmp_path / "r"), "--token-file", str(token_file),
                        "--keywords", "储能", "--pages", pages,
                        "--out", str(tmp_path / f"c{pages}.jsonl"), "--gap", "0"]) == 2
    assert probe.calls == []


def test_api_call_requires_code_field(tmp_path, token_file, monkeypatch):
    no_code = {"status": 200, "body": json.dumps({"msg": "ok", "data": {"dataList": []}}),
               "bytes": 40, "headers": {}, "error": "", "raw": b""}
    probe = FakeProbe([("/mofoun/agent/search", no_code)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    assert fx.main(["check-auth", "--root", str(tmp_path / "r"), "--token-file", str(token_file)]) == 4
    rec = json.loads((tmp_path / "r" / "check_auth.json").read_text(encoding="utf-8"))
    assert rec["ok"] is False and rec["code"] is None


def test_search_page_rejects_page_out_of_range(token_file):
    with pytest.raises(ValueError):
        fx.search_page(FakeProbe([]), "sk-test", "储能", [], "", "", 11)
    with pytest.raises(ValueError):
        fx.search_page(FakeProbe([]), "sk-test", "储能", [], "", "", 0)


def test_load_token_errors(tmp_path):
    with pytest.raises(fx.AuthError):
        fx.load_token(tmp_path / "missing")
    multi = tmp_path / "multi"
    multi.write_text("sk-a\nsk-b", encoding="utf-8")
    with pytest.raises(fx.AuthError):
        fx.load_token(multi)


def test_token_file_permissions_must_be_600(tmp_path, monkeypatch):
    probe = FakeProbe([])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    token = tmp_path / "loose_key"
    token.write_text("sk-test\n", encoding="utf-8")
    token.chmod(0o644)
    code = fx.main(["check-auth", "--root", str(tmp_path / "r"),
                    "--token-file", str(token)])
    assert code == 3 and probe.calls == []
    record = json.loads((tmp_path / "r" / "check_auth.json").read_text(encoding="utf-8"))
    assert "权限" in record["error"]


def test_check_auth_missing_token_exits_3(tmp_path, monkeypatch):
    probe = FakeProbe([])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    code = fx.main(["check-auth", "--root", str(tmp_path / "root"),
                    "--token-file", str(tmp_path / "nope")])
    assert code == 3
    assert probe.calls == []
    record = json.loads((tmp_path / "root" / "check_auth.json").read_text(encoding="utf-8"))
    assert record["ok"] is False


def test_check_auth_success_and_business_error(tmp_path, token_file, monkeypatch):
    probe = FakeProbe([("/mofoun/agent/search", SEARCH_OK)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    assert fx.main(["check-auth", "--root", str(tmp_path / "r1"),
                    "--token-file", str(token_file)]) == 0
    record = json.loads((tmp_path / "r1" / "check_auth.json").read_text(encoding="utf-8"))
    assert record["ok"] is True and record["code"] == 200
    assert len(record["token_sha256_8"]) == 8
    assert "sk-test" not in json.dumps(record)

    bad = {"status": 200, "body": json.dumps({"code": 300011, "msg": "请传入正确Authorization",
                                              "data": None}), "bytes": 80,
           "headers": {}, "error": "", "raw": b""}
    probe2 = FakeProbe([("/mofoun/agent/search", bad)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe2)
    assert fx.main(["check-auth", "--root", str(tmp_path / "r2"),
                    "--token-file", str(token_file)]) == 4
    record2 = json.loads((tmp_path / "r2" / "check_auth.json").read_text(encoding="utf-8"))
    assert record2["code"] == 300011 and "Authorization" in record2["msg"]


def test_search_writes_candidates_raw_and_meta(tmp_path, token_file, monkeypatch):
    probe = FakeProbe([("/mofoun/agent/search", SEARCH_OK)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    out = tmp_path / "cand" / "candidates.jsonl"
    code = fx.main(["search", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--keywords", "储能", "--pages", "2", "--out", str(out), "--gap", "0"])
    assert code == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [r["report_id"] for r in rows] == ["5884540", "5884541"]
    assert rows[0]["page_date"] == "2026-11-16"
    assert (out.parent / "search_raw" / "page1.json").exists()
    assert (out.parent / "search_meta.json").exists()
    # 页数上限 10：请求 12 页只发 10 次
    assert len([c for c in probe.calls if "/search" in c["url"]]) == 2


def test_search_error_logs_code_and_msg(tmp_path, token_file, monkeypatch):
    bad = {"status": 200, "body": json.dumps({"code": 300011, "msg": "请传入正确Authorization",
                                              "data": None}), "bytes": 80,
           "headers": {}, "error": "", "raw": b""}
    probe = FakeProbe([("/mofoun/agent/search", bad)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    out = tmp_path / "cand" / "c.jsonl"
    code = fx.main(["search", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--keywords", "储能", "--out", str(out), "--gap", "0"])
    assert code == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert rec["code"] == 300011 and "Authorization" in rec["msg"]


def write_candidates(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def test_fetch_success_writes_pdf_meta_and_download_note(tmp_path, token_file, monkeypatch):
    routes = [("/mofoun/agent/download", download_ok()),
              ("dr.fxbaogao.com", pdf_route(pages=2))]
    probe = FakeProbe(routes)
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "储能行业深度：市场化拐点",
                             "org": "开源证券", "page_date": "2026-11-16",
                             "url": "https://www.fxbaogao.com/view?id=5884540"}])
    root = tmp_path / "root" / "fxbaogao"
    code = fx.main(["fetch", "--root", str(root), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"])
    assert code == 0
    pdf = root / "2026-11-16" / "5884540-储能行业深度-市场化拐点.pdf"
    assert pdf.exists() and pdf.read_bytes().startswith(b"%PDF-")
    meta = json.loads(pdf.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["pages"] == 2 and meta["fetch_mode"] == "api"
    assert meta["site_url"] == "https://www.fxbaogao.com/view?id=5884540"
    assert meta["pdf_url"] == "https://dr.fxbaogao.com/x.pdf"
    note = json.loads(pdf.with_suffix(".download.json").read_text(encoding="utf-8"))
    assert note["code"] == 200 and "raw" in note
    auth = [c for c in probe.calls if "dr.fxbaogao.com" in c["url"]][0]
    assert "Authorization" not in auth["headers"]  # 非 API 域不带 token


def test_fetch_skips_existing_without_consuming_quota(tmp_path, token_file, monkeypatch):
    routes = [("/mofoun/agent/download", download_ok()),
              ("dr.fxbaogao.com", pdf_route(pages=1))]
    probe = FakeProbe(routes)
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "t", "org": "o",
                             "page_date": "2026-11-16"}])
    root = tmp_path / "root" / "fxbaogao"
    argv = ["fetch", "--root", str(root), "--token-file", str(token_file),
            "--input", str(cand), "--limit", "1", "--gap", "0"]
    assert fx.main(argv) == 0
    calls_after_first = len(probe.calls)
    assert fx.main(argv) == 0
    assert len(probe.calls) == calls_after_first  # 第二次零请求


def test_fetch_business_error_logs_code_and_msg(tmp_path, token_file, monkeypatch):
    bad = {"status": 200, "body": json.dumps({"code": 300012, "msg": "下载额度不足",
                                              "data": None}), "bytes": 60,
           "headers": {}, "error": "", "raw": b""}
    probe = FakeProbe([("/mofoun/agent/download", bad)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "t", "page_date": "2026-11-16"}])
    code = fx.main(["fetch", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"])
    assert code == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert rec["code"] == 300012 and rec["msg"] == "下载额度不足"
    assert "取 PDF 地址失败" in rec["reason"]


def test_fetch_no_pdf_url_is_flagged_as_quota_suspect(tmp_path, token_file, monkeypatch):
    empty = {"status": 200, "body": json.dumps({"code": 200, "msg": "ok", "data": {}}),
             "bytes": 50, "headers": {}, "error": "", "raw": b""}
    probe = FakeProbe([("/mofoun/agent/download", empty)])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "t", "page_date": "2026-11-16"}])
    code = fx.main(["fetch", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"])
    assert code == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert "没有 PDF 地址" in rec["reason"]


def test_fetch_non_pdf_response_keeps_body_head(tmp_path, token_file, monkeypatch):
    routes = [("/mofoun/agent/download", download_ok()),
              ("dr.fxbaogao.com", {"status": 200, "body": "", "bytes": 20, "headers": {},
                                   "error": "", "raw": b"<html>login</html>"})]
    probe = FakeProbe(routes)
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "t", "page_date": "2026-11-16"}])
    code = fx.main(["fetch", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"])
    assert code == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert "PDF 拉取失败" in rec["reason"] and "<html>login" in rec["reason"]


def test_fetch_pdf_error_keeps_server_code_and_download_note(tmp_path, token_file, monkeypatch):
    err = {"status": 200,
           "body": json.dumps({"code": 300021, "msg": "下载额度不足，请充值后重试"}),
           "bytes": 60, "headers": {}, "error": "", "raw": b""}
    pdf_err = json.dumps({"code": 300021, "msg": "下载额度不足，请充值后重试"}).encode()
    routes = [("/mofoun/agent/download", download_ok()),
              ("dr.fxbaogao.com", {"status": 200, "body": "", "bytes": len(pdf_err),
                                   "headers": {}, "error": "", "raw": pdf_err})]
    probe = FakeProbe(routes)
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "5884540", "title": "t", "page_date": "2026-11-16"}])
    code = fx.main(["fetch", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"])
    assert code == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert rec["code"] == 300021 and "下载额度不足" in rec["msg"]
    assert "下载额度不足" in rec["reason"]
    notes = list((tmp_path / "root").glob("*/5884540-*.download.json"))
    assert len(notes) == 1
    note = json.loads(notes[0].read_text(encoding="utf-8"))
    assert note["pdf_error"]["code"] == 300021 and "下载额度不足" in note["pdf_error"]["msg"]


def test_fetch_rejects_non_numeric_report_id(tmp_path, token_file, monkeypatch):
    probe = FakeProbe([])
    monkeypatch.setattr(fx, "prepare_probe", lambda root, max_requests: probe)
    cand = tmp_path / "cand.jsonl"
    write_candidates(cand, [{"report_id": "abc", "title": "t"}])
    assert fx.main(["fetch", "--root", str(tmp_path / "root"), "--token-file", str(token_file),
                    "--input", str(cand), "--limit", "1", "--gap", "0"]) == 1
    rec = json.loads((tmp_path / "root" / "failed.jsonl").read_text(encoding="utf-8"))
    assert "report_id 非数字" in rec["reason"]


def test_verify_compares_detail_title(tmp_path, monkeypatch):
    root = tmp_path / "root"
    date_dir = root / "2026-11-16"
    date_dir.mkdir(parents=True)
    root = date_dir.parent
    pdf = date_dir / "5884540-x.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    meta = {"report_id": 5884540, "title": "储能行业深度：市场化拐点",
            "pages": 1, "sha256": fx.sha256_file(pdf)}
    meta_path = pdf.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    detail_html = '<meta property="og:title" content="储能行业深度：市场化拐点-发现报告">'
    probe = FakeProbe([("/detail/5884540", {"status": 200, "body": detail_html, "bytes": 100,
                                            "headers": {}, "error": "", "raw": b""})])
    monkeypatch.setattr(fx, "prepare_probe", lambda r, m: probe)
    assert fx.main(["verify", "--root", str(root), "--token-file", str(tmp_path / "nope"),
                    "--meta", str(meta_path), "--gap", "0"]) == 0
    probe.robots = {"rules": [("/detail", False)], "robots_status": 200}
    assert fx.main(["verify", "--root", str(root), "--token-file", str(tmp_path / "nope"),
                    "--meta", str(meta_path), "--gap", "0"]) == 1
