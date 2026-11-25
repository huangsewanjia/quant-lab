from __future__ import annotations

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
