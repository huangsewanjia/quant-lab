from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from db.reports.fetch_detail import (  # noqa: E402
    RunLock,
    resolve_item_url,
    Clock,
    DetailPaths,
    FetchResult,
    RateLimiter,
    RobotsPolicy,
    URL_TEMPLATES,
    build_url,
    classify,
    doc_done,
    gzip_write,
    in_window,
    parse_robots,
    resolve_lightpanda,
    run_lightpanda,
    systematic_sample,
    attempts_today,
)
from db.reports.fetch_em import Paths  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))


def _at(hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 25, hour, minute, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Window guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "moment,expected",
    [
        (_at(21, 59), False),
        (_at(22, 0), True),
        (_at(23, 59), True),
        (_at(0, 0), True),
        (_at(6, 59), True),
        (_at(7, 0), False),
        (_at(12, 30), False),
    ],
)
def test_in_window_boundaries(moment, expected):
    assert in_window(moment) is expected


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def test_robots_404_allows_everything():
    policy = parse_robots("https://x/robots.txt", 404, "")
    assert policy.allowed_all is True
    assert policy.allows("https://x/report/info/AP1.html") is True


def test_robots_200_disallow_is_enforced():
    body = "User-agent: *\nDisallow: /report/private/\nDisallow: /api/\n"
    policy = parse_robots("https://x/robots.txt", 200, body)
    assert policy.allows("https://x/report/info/AP1.html") is True
    assert policy.allows("https://x/report/private/AP1.html") is False
    assert policy.allows("https://x/api/list") is False
    assert policy.sha256 is not None


def test_robots_5xx_disallows_everything():
    policy = parse_robots("https://x/robots.txt", 503, "")
    assert policy.allowed_all is False
    assert policy.allows("https://x/anything") is False


def test_robots_unreachable_is_conservative():
    policy = RobotsPolicy("https://x/robots.txt", 0, False, "unreachable")
    assert policy.allows("https://x/a") is False


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def test_rate_limiter_first_request_has_no_delay():
    clock = FakeClock()
    limiter = RateLimiter(clock)
    assert limiter.before_request() == 0.0
    limiter.after_request()
    assert clock.slept == []


def test_rate_limiter_enforces_min_interval_and_jitter():
    clock = FakeClock()
    limiter = RateLimiter(clock, min_interval=3.0, jitter=2.0)
    limiter.before_request()
    limiter.after_request()
    clock.t += 0.5  # client spent 0.5 s on the request
    limiter.before_request()
    limiter.after_request()
    assert len(clock.slept) == 1
    assert 2.5 <= clock.slept[0] <= 4.5
    assert limiter.requests == 2


def test_rate_limiter_rests_every_n_requests():
    clock = FakeClock()
    limiter = RateLimiter(clock, rest_every=3, rest_seconds=(600, 1200))
    for _ in range(3):
        limiter.before_request()
        limiter.after_request()
    assert limiter.rests == 1
    assert any(600 <= value <= 1200 for value in clock.slept)


def test_rate_limiter_no_rest_when_disabled():
    clock = FakeClock()
    limiter = RateLimiter(clock, rest_every=0)
    for _ in range(5):
        limiter.before_request()
        limiter.after_request()
    assert limiter.rests == 0


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


def _result(status, body="", error=None, bot=None, exit_code=0):
    return FetchResult(status, body, error, bot, "", 1.0, exit_code)


def test_classify_403_and_429():
    assert classify(_result(403)) == "403-WAF"
    assert classify(_result(429)) == "429"


def test_classify_captcha_markers():
    assert classify(_result(200, "请输入验证码继续")) == "captcha"
    assert classify(_result(200, "ok", bot="cloudflare")) == "captcha"


def test_classify_login_wall():
    assert classify(_result(200, "请登录后查看全文")) == "login"


def test_classify_timeout_and_pdf():
    assert classify(_result(None, "", error="timeout", exit_code=124)) == "timeout"
    # no response at all (navigation failed): http_status 0, not "schema-changed"
    assert classify(_result(0, "")) == "timeout"
    assert classify(_result(200, "%PDF-1.4"), content_type="application/pdf") == "pdf-only"


def test_classify_ok():
    body = "<html><body>" + "<p>报告正文段落，含盈利预测与评级。</p>" * 20 + "</body></html>"
    assert classify(_result(200, body)) == "ok"


# ---------------------------------------------------------------------------
# URLs and selection
# ---------------------------------------------------------------------------


def test_build_url_templates():
    assert build_url("AP1", None, "info") == "https://data.eastmoney.com/report/info/AP1.html"
    assert "infocode=AP1" in build_url("AP1", None, "zw_infocode")
    encoded = build_url("AP1", "a/b+c=", "zw_encode")
    assert "a%2Fb%2Bc%3D" in encoded


def test_run_lock_is_exclusive_across_handles(tmp_path):
    path = tmp_path / "meta" / ".fetch.lock"
    with RunLock(path):
        with pytest.raises(SystemExit):
            with RunLock(path):
                pass
    # released after the block: a fresh acquisition succeeds
    with RunLock(path):
        pass


def test_resolve_item_url_honours_override():
    item = {
        "info_code": "AP1",
        "encode_url": "a/b+c=",
        "url": "https://data.eastmoney.com/report/info/AP1.html",
        "url_template": "info",
    }
    assert resolve_item_url(item) == (item["url"], "info")
    url, template = resolve_item_url(item, "zw_encode")
    assert template == "zw_encode"
    assert url.endswith("encodeUrl=a%2Fb%2Bc%3D")
    # a row without a stored URL is rebuilt from its template
    rebuilt, template = resolve_item_url({"info_code": "AP2", "url_template": "zw_infocode"})
    assert template == "zw_infocode" and "infocode=AP2" in rebuilt


def test_systematic_sample_spans_the_list():
    rows = [(f"AP{i}",) for i in range(100)]
    picked = systematic_sample(rows, 10)
    assert len(picked) == 10
    assert picked[0] == ("AP0",)
    assert picked[9][0] == "AP90"
    assert systematic_sample(rows[:5], 10) == rows[:5]


# ---------------------------------------------------------------------------
# Storage / resume
# ---------------------------------------------------------------------------


def _paths(tmp_path: Path) -> DetailPaths:
    return DetailPaths(Paths(home=tmp_path, db_file=tmp_path / "db" / "q.duckdb"))


def test_gzip_write_and_doc_done_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    day = dt.date(2024, 4, 1)
    meta = gzip_write(paths.body_file(day, "AP1"), "<html>正文</html>")
    assert meta["html_bytes"] > 0
    assert gzip.decompress(paths.body_file(day, "AP1").read_bytes()).decode() == "<html>正文</html>"
    item = {"info_code": "AP1", "publish_date": day.isoformat()}
    assert doc_done(paths, item) is False  # no meta.json yet
    record = {"failure_class": "ok", **meta}
    paths.meta_file(day, "AP1").parent.mkdir(parents=True, exist_ok=True)
    paths.meta_file(day, "AP1").write_text(json.dumps(record), encoding="utf-8")
    assert doc_done(paths, item) is True
    # a corrupted body must invalidate the resume marker
    paths.body_file(day, "AP1").write_bytes(b"junk")
    assert doc_done(paths, item) is False


def test_attempts_today_counts_only_today(tmp_path):
    paths = _paths(tmp_path)
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date(2026, 11, 25)
    lines = [
        {"fetched_at": "2026-11-25T00:10:00+08:00", "info_code": "A"},
        {"fetched_at": "2026-11-25T01:10:00+08:00", "info_code": "B"},
        {"fetched_at": "2026-11-24T23:10:00+08:00", "info_code": "C"},
        "not-json",
    ]
    paths.progress_file.write_text("\n".join(json.dumps(x) if not isinstance(x, str) else x for x in lines) + "\n")
    assert attempts_today(paths, today) == 2


def test_resolve_lightpanda_requires_explicit_binary(monkeypatch):
    monkeypatch.delenv("LIGHTPANDA_BIN", raising=False)
    with pytest.raises(SystemExit):
        resolve_lightpanda("")


# ---------------------------------------------------------------------------
# Live driver against a local HTTP server (offline; skips without the binary)
# ---------------------------------------------------------------------------

LIGHTPANDA = os.environ.get("LIGHTPANDA_BIN", "/tmp/work540-clean/bin/lightpanda-x86_64-linux")

FIXTURE = """<html><head><meta charset="utf-8"><title>研报</title></head><body>
<h1>贵州茅台(600519)点评</h1>
<table>
<tr><th>项目</th><th>2022A</th><th>2023E</th><th>2024E</th><th>2025E</th></tr>
<tr><td>每股收益(元)</td><td>49.93</td><td>58.20</td><td>68.50</td><td>79.10</td></tr>
</table>
<p>预计公司2023-2025年EPS分别为58.20/68.50/79.10元。</p>
<p>""" + "公司经营稳健，渠道扩张顺利，盈利预测与投资建议详见下表。" * 8 + """</p>
</body></html>"""


@pytest.fixture()
def local_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = FIXTURE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/report.html"
    server.shutdown()


@pytest.mark.skipif(not Path(LIGHTPANDA).is_file(), reason="Lightpanda binary not present")
def test_lightpanda_renders_and_dumps_html(local_site):
    binary = resolve_lightpanda(LIGHTPANDA)
    result = run_lightpanda(binary, local_site, wait_ms=300)
    assert result.exit_code == 0
    assert result.http_status == 200
    assert "每股收益" in result.content
    assert classify(result) == "ok"


def test_classify_ignores_captcha_word_in_script_boilerplate():
    # The healthy eastmoney page ships the word "captcha" inside its JS config.
    body = "<html><head><script>var cfg={'captcha':{url:'/api/captcha'}};</script></head>" \
           "<body>" + "<p>报告正文段落，含盈利预测与评级。</p>" * 20 + "</body></html>"
    assert classify(_result(200, body)) == "ok"


def test_classify_flags_visible_captcha_page():
    body = "<html><body><h2>请输入验证码</h2><p>访问过于频繁</p></body></html>"
    assert classify(_result(200, body)) == "captcha"


def test_classify_blank_shell_is_schema_changed():
    assert classify(_result(200, "<html><body><div id='app'></div></body></html>")) == "schema-changed"


# ---------------------------------------------------------------------------
# Red-line enforcement (review round 1, S6): call-order tests for cmd_fetch
# ---------------------------------------------------------------------------

import db.reports.fetch_detail as fd  # noqa: E402

LONG_BODY = "<html><body>" + "<p>报告正文段落，含盈利预测与评级。</p>" * 20 + "</body></html>"


class _FakeClock:
    def __init__(self, start=None):
        self._now = start or dt.datetime(2026, 11, 25, 22, 30, tzinfo=TZ)
        self._t = 1000.0
        self.slept: list[float] = []

    def now(self):
        return self._now

    def monotonic(self):
        return self._t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self._t += seconds
        self._now += dt.timedelta(seconds=seconds)


def _make_worklist(tmp_path, n):
    items = []
    for i in range(n):
        items.append(
            {
                "info_code": f"AP{i}",
                "publish_date": "2024-04-01",
                "stock_code": "600519",
                "fiscal_year_this": 2024,
                "fiscal_year_next": 2025,
                "fiscal_year_two": 2026,
                "encode_url": "x",
                "url_template": "info",
                "url": f"https://data.eastmoney.com/report/info/AP{i}.html",
            }
        )
    path = tmp_path / "wl.jsonl"
    path.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    return path


def _install_fake_driver(monkeypatch, tmp_path, clock, responder):
    binary = tmp_path / "fake-lightpanda"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    calls = []

    def fake_run(binary_path, url, *, wait_ms=0, timeout_s=0, user_agent=""):
        calls.append(url)
        return responder(clock, url)

    monkeypatch.setattr(fd, "Clock", lambda: clock)
    monkeypatch.setattr(fd, "resolve_lightpanda", lambda explicit: binary)
    monkeypatch.setattr(fd, "lightpanda_version", lambda b: "test")
    monkeypatch.setattr(fd, "sha256_file", lambda p: "sha")
    monkeypatch.setattr(fd, "run_lightpanda", fake_run)
    monkeypatch.setattr(fd, "fetch_robots", lambda *a, **k: RobotsPolicy("r", 404, True, "absent"))
    return calls


def _fetch_args(worklist, **over):
    base = dict(
        worklist=str(worklist), resume=True, url_template="info", lightpanda="/bin/true",
        run_cap=fd.RUN_CAP, daily_cap=fd.DAILY_CAP, wait_ms=0, timeout=1.0, verbose=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _ok_responder(clock, url):
    return FetchResult(200, LONG_BODY, None, None, "", 1.0, 0)


def test_fetch_stops_at_run_cap(tmp_path, monkeypatch):
    clock = _FakeClock()
    calls = _install_fake_driver(monkeypatch, tmp_path, clock, _ok_responder)
    paths = _paths(tmp_path)
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 5), run_cap=2), paths)
    assert len(calls) == 2
    assert payload["stats"]["attempted"] == 2
    assert "run cap" in (payload["aborted"] or "")


def test_fetch_stops_at_daily_cap(tmp_path, monkeypatch):
    clock = _FakeClock()
    calls = _install_fake_driver(monkeypatch, tmp_path, clock, _ok_responder)
    paths = _paths(tmp_path)
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"fetched_at": "2026-11-25T00:00:00+08:00", "failure_class": "ok"})] * 4999
    paths.progress_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 3)), paths)
    assert len(calls) == 1
    assert "daily cap" in (payload["aborted"] or "")


def test_fetch_rechecks_window_inside_the_loop(tmp_path, monkeypatch):
    clock = _FakeClock(dt.datetime(2026, 11, 25, 6, 59, 58, tzinfo=TZ))

    def responder(clk, url):
        clk._now += dt.timedelta(seconds=10)  # crosses 07:00 during the request
        return FetchResult(200, LONG_BODY, None, None, "", 1.0, 0)

    calls = _install_fake_driver(monkeypatch, tmp_path, clock, responder)
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 5)), paths := _paths(tmp_path))
    assert len(calls) == 1
    assert "window closed" in (payload["aborted"] or "")


def test_fetch_aborts_after_three_consecutive_blocks(tmp_path, monkeypatch):
    clock = _FakeClock()
    calls = _install_fake_driver(
        monkeypatch, tmp_path, clock, lambda c, u: FetchResult(403, "blocked", None, None, "", 1.0, 0)
    )
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 6)), _paths(tmp_path))
    assert len(calls) == 3
    assert "consecutive" in (payload["aborted"] or "")
    assert payload["stats"]["attempted"] == 3
    assert 1800 in clock.slept and 3600 in clock.slept


def test_robots_disallowed_makes_no_request(tmp_path, monkeypatch):
    clock = _FakeClock()
    calls = _install_fake_driver(monkeypatch, tmp_path, clock, _ok_responder)
    monkeypatch.setattr(
        fd, "fetch_robots", lambda *a, **k: RobotsPolicy("r", 200, False, "parsed", _parser=_DenyAll())
    )
    paths = _paths(tmp_path)
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 2)), paths)
    assert calls == []
    assert payload["stats"]["skipped_robots"] == 2
    assert payload["failures"]["robots-disallowed"] == 2


class _DenyAll:
    def can_fetch(self, user_agent, url):
        return False


def test_robots_disallowed_does_not_consume_the_daily_budget(tmp_path):
    paths = _paths(tmp_path)
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        {"fetched_at": "2026-11-25T00:00:00+08:00", "failure_class": "robots-disallowed"},
        {"fetched_at": "2026-11-25T00:01:00+08:00", "failure_class": "ok"},
    ]
    paths.progress_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert attempts_today(paths, dt.date(2026, 11, 25)) == 1


def test_unusable_robots_aborts_before_any_request(tmp_path, monkeypatch):
    clock = _FakeClock()
    calls = _install_fake_driver(monkeypatch, tmp_path, clock, _ok_responder)
    monkeypatch.setattr(
        fd, "fetch_robots", lambda *a, **k: RobotsPolicy("r", 503, False, "unreachable")
    )
    with pytest.raises(SystemExit):
        fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 2)), _paths(tmp_path))
    assert calls == []


# ---------------------------------------------------------------------------
# Review round 2: probe red lines and engine-error handling
# ---------------------------------------------------------------------------


def _probe_args(codes, **over):
    base = dict(
        info_code=list(codes), run_cap=fd.RUN_CAP, daily_cap=fd.DAILY_CAP,
        wait_ms=0, timeout=1.0, lightpanda="/bin/true",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _install_probe_env(monkeypatch, tmp_path, clock, responder, codes):
    calls = _install_fake_driver(monkeypatch, tmp_path, clock, responder)
    con = fd.connect_ro  # noqa: F841  (kept for clarity)
    rows = [(code, dt.date(2024, 4, 1), "enc") for code in codes]
    monkeypatch.setattr(fd, "connect_ro", lambda p: _FakeCon(rows))
    return calls


class _FakeCon:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return self.rows

    def close(self):
        return None


def test_probe_stops_at_run_cap(tmp_path, monkeypatch):
    clock = _FakeClock()
    codes = ["AP1", "AP2"]
    calls = _install_probe_env(monkeypatch, tmp_path, clock, _ok_responder, codes)
    payload = fd.cmd_probe(_probe_args(codes, run_cap=2), _paths(tmp_path))
    assert len(calls) == 2
    assert payload["attempted"] == 2
    assert "probe cap" in (payload["aborted"] or "")


def test_probe_cap_cannot_exceed_hard_limit(tmp_path, monkeypatch):
    clock = _FakeClock()
    codes = ["AP1"]
    _install_probe_env(monkeypatch, tmp_path, clock, _ok_responder, codes)
    payload = fd.cmd_probe(_probe_args(codes, run_cap=999999, daily_cap=999999), _paths(tmp_path))
    assert payload["protocol"]["run_cap"] == fd.RUN_CAP
    assert payload["protocol"]["daily_cap"] == fd.DAILY_CAP


def test_probe_rechecks_window_inside_the_loop(tmp_path, monkeypatch):
    clock = _FakeClock(dt.datetime(2026, 11, 25, 6, 59, 58, tzinfo=TZ))

    def responder(clk, url):
        clk._now += dt.timedelta(seconds=10)
        return FetchResult(200, LONG_BODY, None, None, "", 1.0, 0)

    codes = ["AP1"]
    calls = _install_probe_env(monkeypatch, tmp_path, clock, responder, codes)
    payload = fd.cmd_probe(_probe_args(codes), _paths(tmp_path))
    assert len(calls) == 1
    assert "window closed" in (payload["aborted"] or "")


def test_probe_writes_progress_and_respects_daily_cap(tmp_path, monkeypatch):
    clock = _FakeClock()
    codes = ["AP1", "AP2"]
    calls = _install_probe_env(monkeypatch, tmp_path, clock, _ok_responder, codes)
    paths = _paths(tmp_path)
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    paths.progress_file.write_text(
        json.dumps({"fetched_at": "2026-11-25T00:00:00+08:00", "failure_class": "ok"}) + "\n",
        encoding="utf-8",
    )
    payload = fd.cmd_probe(_probe_args(codes, daily_cap=2), paths)
    assert len(calls) == 1
    assert "daily cap" in (payload["aborted"] or "")


def test_fetch_survives_an_engine_crash_and_classifies_it(tmp_path, monkeypatch):
    clock = _FakeClock()

    def boom(binary_path, url, **kw):
        raise RuntimeError("lightpanda failed (exit 1)")

    binary = tmp_path / "fake-lightpanda"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(fd, "Clock", lambda: clock)
    monkeypatch.setattr(fd, "resolve_lightpanda", lambda explicit: binary)
    monkeypatch.setattr(fd, "lightpanda_version", lambda b: "test")
    monkeypatch.setattr(fd, "sha256_file", lambda p: "sha")
    monkeypatch.setattr(fd, "run_lightpanda", boom)
    monkeypatch.setattr(fd, "fetch_robots", lambda *a, **k: RobotsPolicy("r", 404, True, "absent"))
    payload = fd.cmd_fetch(_fetch_args(_make_worklist(tmp_path, 3)), _paths(tmp_path))
    assert payload["failures"]["engine-error"] == 3
    assert "consecutive engine-error" in (payload["aborted"] or "")


# ---------------------------------------------------------------------------
# plan: full-universe worklist (no network)
# ---------------------------------------------------------------------------


def _plan_args(tmp_path: Path, **over) -> argparse.Namespace:
    base = dict(
        stocks="all",
        start="2023-01-01",
        end="2024-12-31",
        limit=0,
        url_template="info",
        worklist=str(tmp_path / "worklist.jsonl"),
    )
    base.update(over)
    return argparse.Namespace(**base)


def _seed_report_meta(paths: DetailPaths, rows: list[tuple]) -> None:
    import duckdb

    paths.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(paths.db_path))
    try:
        con.execute(
            """
            CREATE TABLE report_meta (
                info_code VARCHAR, stock_code VARCHAR, ts_code VARCHAR, stock_name VARCHAR,
                org_name VARCHAR, publish_date DATE, fiscal_year_this INTEGER,
                fiscal_year_next INTEGER, fiscal_year_two INTEGER, encode_url VARCHAR
            )
            """
        )
        con.executemany("INSERT INTO report_meta VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    finally:
        con.close()


def test_plan_all_stocks_selects_every_row_ordered_by_date(tmp_path):
    paths = _paths(tmp_path)
    _seed_report_meta(
        paths,
        [
            ("AP3", "000001", "000001.SZ", "平安银行", "券商A", dt.date(2024, 5, 1), 2024, 2025, 2026, "e3"),
            ("AP1", "600519", "600519.SH", "贵州茅台", "券商B", dt.date(2023, 1, 2), 2023, 2024, 2025, "e1"),
            ("AP2", "300750", "300750.SZ", "宁德时代", "券商C", dt.date(2023, 1, 2), 2023, 2024, 2025, "e2"),
            ("AP9", "600519", "600519.SH", "贵州茅台", "券商B", dt.date(2025, 1, 2), 2025, 2026, 2027, "e9"),
        ],
    )
    payload = fd.cmd_plan(_plan_args(tmp_path), paths)
    assert payload["scope"] == "all-stocks"
    assert payload["rows_selected"] == 3  # 2025 row is outside the window
    assert payload["rows_available"] == 3
    assert payload["stocks_covered"] == 3
    assert payload["by_year"] == {"2023": 2, "2024": 1}
    codes = [json.loads(line)["info_code"] for line in Path(payload["worklist"]).read_text(encoding="utf-8").splitlines()]
    assert codes == ["AP1", "AP2", "AP3"]  # date, then info_code
    first = json.loads(Path(payload["worklist"]).read_text(encoding="utf-8").splitlines()[0])
    assert first["url"] == "https://data.eastmoney.com/report/info/AP1.html"
    assert first["publish_date"] == "2023-01-02"


def test_plan_all_stocks_honours_a_positive_limit(tmp_path):
    paths = _paths(tmp_path)
    rows = [
        (f"AP{i}", "600519", "600519.SH", "贵州茅台", "券商", dt.date(2023, 1, 1) + dt.timedelta(days=i), 2023, 2024, 2025, f"e{i}")
        for i in range(10)
    ]
    _seed_report_meta(paths, rows)
    payload = fd.cmd_plan(_plan_args(tmp_path, limit=4), paths)
    assert payload["rows_selected"] == 4
    assert payload["rows_available"] == 10


def test_plan_named_stocks_keeps_the_per_stock_quota(tmp_path):
    paths = _paths(tmp_path)
    rows = []
    for stock in ("600519", "300750"):
        for i in range(5):
            rows.append((f"{stock}-{i}", stock, f"{stock}.SZ", "x", "券商", dt.date(2023, 1, 1) + dt.timedelta(days=i), 2023, 2024, 2025, "e"))
    _seed_report_meta(paths, rows)
    payload = fd.cmd_plan(_plan_args(tmp_path, stocks="600519,300750", limit=6), paths)
    assert payload["scope"] == "named-stocks"
    assert payload["quota_by_stock"] == {"600519": 3, "300750": 3}
    assert payload["by_stock"] == {"600519": 5, "300750": 5}  # available, not chosen
    chosen = [json.loads(line)["stock_code"] for line in Path(payload["worklist"]).read_text(encoding="utf-8").splitlines()]
    assert chosen.count("600519") == 3 and chosen.count("300750") == 3
