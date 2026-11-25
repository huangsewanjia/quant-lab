#!/usr/bin/env python3
"""Fetch Eastmoney report detail pages (rendered HTML) with Lightpanda.

The metadata layer stores an opaque ``encodeUrl`` token, not a URL, so the
detail URL is rebuilt from ``info_code`` (templates below).  One report costs
exactly one navigation request: the rendered DOM is dumped once and every
derived artefact (text, tables) is produced offline by ``extract_eps.py``.

Anti-crawl contract, enforced here rather than documented as advice:

* concurrency 1, one honest User-Agent, no proxy pool, no UA rotation;
* >= 3 s between requests plus 0-2 s uniform jitter;
* 10-20 min rest after every 500 reports;
* requests only between 22:00 and 07:00 Asia/Shanghai;
* ``robots.txt`` is fetched and parsed first; disallowed paths are never
  requested and are recorded as ``robots-disallowed``;
* 403 / 429 / captcha / login => 30 min exponential backoff, abort after 3
  consecutive blocks (no captcha solving, no login, no IP rotation);
* <= 5000 reports per calendar day, <= 3000 per run, resumable by per-report
  ``meta.json``.

Raw layer::

    <QUANTDB_HOME>/raw/reports/detail/<publish_date>/<info_code>/body.html.gz
    <QUANTDB_HOME>/raw/reports/detail/<publish_date>/<info_code>/meta.json

Progress log (append-only, one line per attempt)::

    <QUANTDB_HOME>/meta/reports/detail/progress.jsonl

Commands::

    python db/reports/fetch_detail.py plan  --stocks 600519,002594,300750 --limit 200
    python db/reports/fetch_detail.py probe --info-code AP202301011581615358
    python db/reports/fetch_detail.py fetch --worklist <path> --resume
    python db/reports/fetch_detail.py stats
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import gzip
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

if __package__ in (None, ""):  # direct ``python db/reports/fetch_detail.py``
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db.reports.fetch_em import Paths, read_json, sha256_file, write_json_atomic  # noqa: E402

IMPL_VERSION = "2026-11-25-p1-detail"
DATASET = "reports/detail"

# --- anti-crawl contract (hard limits; CLI flags may only lower them) --------
WINDOW_START_HOUR = 22
WINDOW_END_HOUR = 7
MIN_INTERVAL_S = 3.0
JITTER_MAX_S = 2.0
REST_EVERY = 500
REST_MIN_S = 600.0
REST_MAX_S = 1200.0
DAILY_CAP = 5000
RUN_CAP = 3000
BLOCK_BACKOFF_S = (1800.0, 3600.0, 7200.0)
MAX_CONSECUTIVE_BLOCKS = 3

USER_AGENT = "quantdb-research/1.0 (internal research; low-rate; contact ops@localhost)"

URL_TEMPLATES: dict[str, str] = {
    # Modern detail page.  Tried first; ``probe`` decides which template the
    # upstream actually serves and the choice is frozen into the worklist.
    "info": "https://data.eastmoney.com/report/info/{info_code}.html",
    "zw_infocode": "https://data.eastmoney.com/report/zw_stock.jshtml?infocode={info_code}",
    "zw_encode": "https://data.eastmoney.com/report/zw_stock.jshtml?encodeUrl={encode_url}",
}
ROBOTS_URL = "https://data.eastmoney.com/robots.txt"

FAILURE_CLASSES = (
    "ok",
    "403-WAF",
    "429",
    "captcha",
    "login",
    "pdf-only",
    "timeout",
    "schema-changed",
    "robots-disallowed",
)
BLOCKING_CLASSES = frozenset({"403-WAF", "429", "captcha", "login"})
CAPTCHA_MARKERS = (
    "验证码",
    "人机验证",
    "安全验证",
    "访问过于频繁",
    "captcha",
    "verify you are human",
    "cf-challenge",
)
LOGIN_MARKERS = ("请登录后查看", "登录后查看", "passport.eastmoney.com")


# ---------------------------------------------------------------------------
# Time window and pacing
# ---------------------------------------------------------------------------


def shanghai_tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Shanghai")


class Clock:
    """Wall clock + sleeps, injectable so tests never really wait."""

    def __init__(self) -> None:
        self.tz = shanghai_tz()

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


def in_window(now: dt.datetime) -> bool:
    """True inside 22:00-07:00 Asia/Shanghai (07:00 exclusive)."""
    hour = now.hour
    return hour >= WINDOW_START_HOUR or hour < WINDOW_END_HOUR


def window_label(now: dt.datetime) -> str:
    return f"{now:%Y-%m-%d %H:%M:%S %Z} window={in_window(now)}"


class RateLimiter:
    """Sequential pacing: min interval + jitter, periodic long rest."""

    def __init__(
        self,
        clock: Clock,
        *,
        min_interval: float = MIN_INTERVAL_S,
        jitter: float = JITTER_MAX_S,
        rest_every: int = REST_EVERY,
        rest_seconds: tuple[float, float] = (REST_MIN_S, REST_MAX_S),
        rng: random.Random | None = None,
    ) -> None:
        self.clock = clock
        self.min_interval = min_interval
        self.jitter = jitter
        self.rest_every = rest_every
        self.rest_seconds = rest_seconds
        self.rng = rng or random.Random()
        self._last: float | None = None
        self.requests = 0
        self.rests = 0
        self.waits: list[float] = []

    def next_delay(self) -> float:
        if self._last is None:
            return 0.0
        target = self.min_interval + self.rng.uniform(0.0, self.jitter)
        return max(0.0, target - (self.clock.monotonic() - self._last))

    def before_request(self) -> float:
        delay = self.next_delay()
        if delay > 0:
            self.clock.sleep(delay)
        self.waits.append(round(delay, 3))
        return delay

    def after_request(self) -> float:
        self._last = self.clock.monotonic()
        self.requests += 1
        if self.rest_every and self.requests % self.rest_every == 0:
            rest = self.rng.uniform(*self.rest_seconds)
            self.clock.sleep(rest)
            self.rests += 1
            return rest
        return 0.0


class RunLock:
    """Cross-process exclusive lock: one fetcher at a time, by contract."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = ""
            try:
                self._fh.seek(0)
                holder = self._fh.read().strip()
            except Exception:
                pass
            self._fh.close()
            self._fh = None
            raise SystemExit(
                f"another fetcher holds {self.path} ({holder or 'unknown pid'}); "
                "concurrency must stay 1, refusing to start"
            )
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"pid={os.getpid()} started={dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        self._fh.flush()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


@dataclass
class RobotsPolicy:
    url: str
    status: int
    allowed_all: bool
    reason: str
    sha256: str | None = None
    body_bytes: int = 0
    _parser: Any = None

    def allows(self, target: str, user_agent: str = USER_AGENT) -> bool:
        if self._parser is None:
            return self.allowed_all
        return bool(self._parser.can_fetch(user_agent, target))


def parse_robots(url: str, status: int, body: str) -> RobotsPolicy:
    """RFC 9309: 404 => allow all, 5xx/unreachable => disallow all."""
    if status == 404:
        return RobotsPolicy(url, status, True, "robots.txt absent (404): allow all")
    if status != 200:
        return RobotsPolicy(url, status, False, f"robots.txt HTTP {status}: treat as disallow all")
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(body.splitlines())
    return RobotsPolicy(
        url=url,
        status=status,
        allowed_all=False,
        reason="robots.txt parsed",
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        body_bytes=len(body.encode("utf-8")),
        _parser=parser,
    )


def fetch_robots(session, clock: Clock, limiter: RateLimiter, url: str = ROBOTS_URL) -> RobotsPolicy:
    limiter.before_request()
    try:
        response = session.get(url, timeout=(10, 30), headers={"User-Agent": USER_AGENT})
        policy = parse_robots(url, response.status_code, response.text)
    except Exception as exc:  # unreachable => conservative
        policy = RobotsPolicy(url, 0, False, f"robots.txt unreachable: {exc}")
    finally:
        limiter.after_request()
    return policy


# ---------------------------------------------------------------------------
# Lightpanda driver
# ---------------------------------------------------------------------------


def resolve_lightpanda(explicit: str | None) -> Path:
    candidate = explicit or os.environ.get("LIGHTPANDA_BIN") or ""
    if not candidate:
        raise SystemExit(
            "Lightpanda binary not given: pass --lightpanda or set LIGHTPANDA_BIN "
            "(WORK-540 verified build: sha256 0ec21fa4...8bd7)"
        )
    path = Path(candidate).expanduser()
    if not path.is_file():
        raise SystemExit(f"Lightpanda binary not found: {path}")
    return path


def lightpanda_version(binary: Path) -> str:
    out = subprocess.run([str(binary), "version"], capture_output=True, text=True, timeout=30)
    lines = (out.stdout or out.stderr or "").strip().splitlines()
    return lines[0] if lines else "unknown"


@dataclass
class FetchResult:
    http_status: int | None
    content: str
    error: str | None
    bot_challenge: str | None
    stderr: str
    elapsed_s: float
    exit_code: int
    content_type: str = ""


def run_lightpanda(
    binary: Path,
    url: str,
    *,
    wait_ms: int = 5000,
    timeout_s: float = 90.0,
    user_agent: str = USER_AGENT,
) -> FetchResult:
    cmd = [
        str(binary),
        "fetch",
        "--dump",
        "html",
        "--json",
        "--wait-until",
        "load",
        "--wait-ms",
        str(wait_ms),
        "--terminate-ms",
        "30000",
        "--http-connect-timeout",
        "10000",
        "--strip-mode",
        "js",
        "--strip-mode",
        "css",
        "--user-agent",
        user_agent,
        url,
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        elapsed = round(time.monotonic() - started, 3)
        payload: dict[str, Any] = {}
        raw_out = proc.stdout.strip()
        if raw_out:
            try:
                payload = json.loads(raw_out)
            except json.JSONDecodeError:
                payload = {}
        if not payload and proc.returncode not in (0, 22):
            raise RuntimeError(
                f"lightpanda failed (exit {proc.returncode}): {(proc.stderr or '')[-400:]}"
            )
        headers = payload.get("headers") or []
        content_type = next(
            (str(h.get("value", "")) for h in headers if str(h.get("name", "")).lower() == "content-type"),
            "",
        )
        return FetchResult(
            http_status=payload.get("http_status"),
            content=payload.get("content") or "",
            error=payload.get("error"),
            bot_challenge=payload.get("bot_challenge"),
            stderr=(proc.stderr or "")[-4000:],
            elapsed_s=elapsed,
            exit_code=proc.returncode,
            content_type=content_type,
        )
    except subprocess.TimeoutExpired:
        return FetchResult(None, "", "timeout", None, "subprocess timeout", round(time.monotonic() - started, 3), 124)


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_MIN_BODY_CHARS = 200


def visible_text(html: str) -> str:
    """Cheap visible-text projection: drop scripts/styles and tags.

    Captcha / login markers must be judged on what a reader sees, not on
    script boilerplate: the normal report page ships the word ``captcha`` in
    its JS config, which made a raw-substring test fire on healthy pages.
    """
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify(result: FetchResult, content_type: str = "") -> str:
    status = result.http_status
    text = visible_text(result.content or "")
    lowered = text.lower()
    if result.error == "timeout" or status in (None, 0) or result.exit_code == 124:
        return "timeout"
    if result.error:
        return "schema-changed"
    if status == 403:
        return "403-WAF"
    if status == 429:
        return "429"
    if result.bot_challenge:
        return "captcha"
    if any(marker.lower() in lowered for marker in CAPTCHA_MARKERS):
        return "captcha"
    if any(marker.lower() in lowered for marker in LOGIN_MARKERS):
        return "login"
    if status is None or status >= 400:
        return "schema-changed"
    if "pdf" in (content_type or result.content_type or "").lower():
        return "pdf-only"
    if len(text) < _MIN_BODY_CHARS:
        # 200 with a blank shell: selector/format drift, not a usable body
        return "schema-changed"
    return "ok"


def resolve_item_url(item: dict[str, Any], override: str | None = None) -> tuple[str, str]:
    """Return (url, template) for a worklist row, honouring a CLI override."""
    template = override or item.get("url_template") or "info"
    if override or not item.get("url"):
        return build_url(item["info_code"], item.get("encode_url"), template), template
    return item["url"], template


def build_url(info_code: str, encode_url: str | None, template: str) -> str:
    pattern = URL_TEMPLATES[template]
    if template == "zw_encode":
        return pattern.format(info_code=info_code, encode_url=quote(encode_url or "", safe=""))
    return pattern.format(info_code=info_code)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetailPaths:
    home: Paths

    @property
    def root(self) -> Path:
        return self.home.home

    @property
    def detail_dir(self) -> Path:
        return self.root / "raw" / "reports" / "detail"

    @property
    def meta_dir(self) -> Path:
        return self.root / "meta" / "reports" / "detail"

    @property
    def run_dir(self) -> Path:
        return self.meta_dir / "runs"

    @property
    def progress_file(self) -> Path:
        return self.meta_dir / "progress.jsonl"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db_path(self) -> Path:
        return self.home.db_path

    def doc_dir(self, publish_date: dt.date, info_code: str) -> Path:
        return self.detail_dir / f"{publish_date:%Y%m%d}" / info_code

    def body_file(self, publish_date: dt.date, info_code: str) -> Path:
        return self.doc_dir(publish_date, info_code) / "body.html.gz"

    def meta_file(self, publish_date: dt.date, info_code: str) -> Path:
        return self.doc_dir(publish_date, info_code) / "meta.json"


def gzip_write(path: Path, text: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode("utf-8")
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(raw)
    os.replace(tmp, path)
    return {
        "html_bytes": len(raw),
        "html_sha256": hashlib.sha256(raw).hexdigest(),
        "gzip_bytes": path.stat().st_size,
        "gzip_sha256": sha256_file(path),
    }


def append_progress(paths: DetailPaths, payload: dict[str, Any]) -> None:
    paths.meta_dir.mkdir(parents=True, exist_ok=True)
    with paths.progress_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def attempts_today(paths: DetailPaths, today: dt.date) -> int:
    if not paths.progress_file.exists():
        return 0
    count = 0
    with paths.progress_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("failure_class") == "robots-disallowed":
                continue
            stamp = str(record.get("fetched_at") or "")[:10]
            if stamp == today.isoformat():
                count += 1
    return count


def doc_done(paths: DetailPaths, item: dict[str, Any]) -> bool:
    publish_date = dt.date.fromisoformat(item["publish_date"])
    meta_path = paths.meta_file(publish_date, item["info_code"])
    body_path = paths.body_file(publish_date, item["info_code"])
    if not meta_path.exists() or not body_path.exists():
        return False
    try:
        meta = read_json(meta_path)
    except Exception:
        return False
    return meta.get("failure_class") == "ok" and meta.get("gzip_sha256") == sha256_file(body_path)


# ---------------------------------------------------------------------------
# plan: select reports from report_meta (read-only)
# ---------------------------------------------------------------------------


def connect_ro(db_path: Path):
    import duckdb

    return duckdb.connect(str(db_path), read_only=True)


def systematic_sample(rows: list[tuple], limit: int) -> list[tuple]:
    if limit >= len(rows):
        return list(rows)
    if limit <= 0:
        return []
    step = len(rows) / float(limit)
    return [rows[min(int(i * step), len(rows) - 1)] for i in range(limit)]


def cmd_plan(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    if not stocks:
        raise SystemExit("--stocks must list at least one stock code")
    con = connect_ro(paths.db_path)
    try:
        rows = con.execute(
            """
            SELECT info_code, stock_code, ts_code, stock_name, org_name, publish_date,
                   fiscal_year_this, fiscal_year_next, fiscal_year_two, encode_url
            FROM report_meta
            WHERE stock_code IN (SELECT unnest(?))
              AND publish_date BETWEEN ? AND ?
            ORDER BY stock_code, publish_date, info_code
            """,
            [stocks, args.start, args.end],
        ).fetchall()
    finally:
        con.close()
    by_stock: dict[str, list[tuple]] = {}
    for row in rows:
        by_stock.setdefault(row[1], []).append(row)
    base = max(1, args.limit // len(stocks))
    remainder = max(0, args.limit - base * len(stocks))
    quotas = {stock: base + (1 if idx < remainder else 0) for idx, stock in enumerate(stocks)}
    chosen: list[tuple] = []
    for stock in stocks:
        chosen.extend(systematic_sample(by_stock.get(stock, []), quotas[stock]))
    out = Path(args.worklist) if args.worklist else paths.meta_dir / f"worklist-{args.start}-{args.end}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in chosen:
            item = {
                "info_code": row[0],
                "stock_code": row[1],
                "ts_code": row[2],
                "stock_name": row[3],
                "org_name": row[4],
                "publish_date": row[5].isoformat() if row[5] else None,
                "fiscal_year_this": row[6],
                "fiscal_year_next": row[7],
                "fiscal_year_two": row[8],
                "encode_url": row[9],
                "url_template": args.url_template,
                "url": build_url(row[0], row[9], args.url_template),
            }
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return {
        "worklist": str(out),
        "rows_selected": len(chosen),
        "rows_available": len(rows),
        "by_stock": {k: len(v) for k, v in by_stock.items()},
        "quota_by_stock": quotas,
        "url_template": args.url_template,
    }


def load_worklist(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# ---------------------------------------------------------------------------
# probe / fetch
# ---------------------------------------------------------------------------


def cmd_probe(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    with RunLock(paths.meta_dir / ".fetch.lock"):
        return _probe_impl(args, paths)


def _probe_impl(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    clock = Clock()
    if not in_window(clock.now()):
        raise SystemExit(f"outside the 22:00-07:00 Asia/Shanghai window ({window_label(clock.now())})")
    binary = resolve_lightpanda(args.lightpanda)
    limiter = RateLimiter(clock)
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = fetch_robots(session, clock, limiter)
    if robots.status not in (200, 404):
        raise SystemExit(f"robots.txt unusable ({robots.reason}); refusing to run")
    con = connect_ro(paths.db_path)
    try:
        rows = con.execute(
            "SELECT info_code, publish_date, encode_url FROM report_meta WHERE info_code IN (SELECT unnest(?))",
            [args.info_code],
        ).fetchall()
    finally:
        con.close()
    results: list[dict[str, Any]] = []
    probe_cap = min(getattr(args, "run_cap", RUN_CAP), RUN_CAP)
    protocol = {
        "run_cap": probe_cap,
        "min_interval_s": limiter.min_interval,
        "jitter_max_s": limiter.jitter,
        "window": "22:00-07:00 Asia/Shanghai",
    }
    aborted = None
    attempted = 0
    for info_code, publish_date, encode_url in rows:
        for template in URL_TEMPLATES:
            if attempted >= probe_cap:
                aborted = f"probe cap {probe_cap} reached"
                break
            if not in_window(clock.now()):
                aborted = f"window closed at {clock.now():%H:%M:%S}"
                break
            url = build_url(info_code, encode_url, template)
            allowed = robots.allows(url)
            record: dict[str, Any] = {
                "info_code": info_code,
                "template": template,
                "url": url,
                "robots_allowed": allowed,
                "robots_reason": robots.reason,
            }
            if not allowed:
                record["failure_class"] = "robots-disallowed"
                results.append(record)
                continue
            limiter.before_request()
            result = run_lightpanda(binary, url, wait_ms=args.wait_ms, timeout_s=args.timeout)
            limiter.after_request()
            attempted += 1
            append_progress(
                paths,
                {
                    "info_code": info_code,
                    "fetched_at": clock.now().isoformat(),
                    "url": url,
                    "failure_class": classify(result),
                    "stage": "probe",
                },
            )
            failure = classify(result)
            text_len = len(result.content or "")
            record.update(
                {
                    "http_status": result.http_status,
                    "error": result.error,
                    "failure_class": failure,
                    "html_bytes": text_len,
                    "elapsed_s": result.elapsed_s,
                    "has_eps_token": "每股收益" in (result.content or "") or "EPS" in (result.content or ""),
                    "has_pdf_ref": ".pdf" in (result.content or "").lower(),
                }
            )
            if failure == "ok":
                out = paths.meta_dir / "probe" / f"{info_code}.{template}.html.gz"
                record.update(gzip_write(out, result.content))
                record["stored"] = str(out)
            results.append(record)
    payload = {
        "probe_at": clock.now().isoformat(),
        "attempted": attempted,
        "aborted": aborted,
        "protocol": protocol,
        "engine": lightpanda_version(binary),
        "engine_sha256": sha256_file(binary),
        "robots": {"url": robots.url, "status": robots.status, "reason": robots.reason, "sha256": robots.sha256},
        "results": results,
    }
    out = paths.meta_dir / "probe" / f"probe-{dt.datetime.now():%Y%m%dT%H%M%S}.json"
    write_json_atomic(out, payload)
    payload["written"] = str(out)
    return payload


def cmd_fetch(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    with RunLock(paths.meta_dir / ".fetch.lock"):
        return _fetch_impl(args, paths)


def _fetch_impl(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    clock = Clock()
    if not in_window(clock.now()):
        raise SystemExit(
            f"refusing to fetch outside 22:00-07:00 Asia/Shanghai ({window_label(clock.now())}); "
            "the anti-crawl contract forbids daytime requests"
        )
    binary = resolve_lightpanda(args.lightpanda)
    engine_version = lightpanda_version(binary)
    engine_sha = sha256_file(binary)
    worklist = load_worklist(Path(args.worklist))
    run_cap = min(args.run_cap, RUN_CAP)
    daily_cap = min(args.daily_cap, DAILY_CAP)
    limiter = RateLimiter(clock)
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    robots = fetch_robots(session, clock, limiter)
    if robots.status not in (200, 404):
        raise SystemExit(f"robots.txt unusable ({robots.reason}); refusing to run")

    today = clock.now().date()
    used_today = attempts_today(paths, today)
    started_at = clock.now()
    run_id = f"{started_at:%Y%m%dT%H%M%S}"
    stats = {"ok": 0, "skipped_done": 0, "skipped_robots": 0, "failed": 0, "attempted": 0}
    failures: dict[str, int] = {}
    consecutive_blocks = 0
    aborted = None
    for item in worklist:
        if stats["attempted"] >= run_cap:
            aborted = f"run cap {run_cap} reached"
            break
        if used_today + stats["attempted"] >= daily_cap:
            aborted = f"daily cap {daily_cap} reached (already {used_today} today)"
            break
        now = clock.now()
        if not in_window(now):
            aborted = f"window closed at {now:%H:%M:%S} Asia/Shanghai"
            break
        publish_date = dt.date.fromisoformat(item["publish_date"])
        if args.resume and doc_done(paths, item):
            stats["skipped_done"] += 1
            continue
        url, template = resolve_item_url(item, args.url_template)
        if not robots.allows(url):
            stats["skipped_robots"] += 1
            failures["robots-disallowed"] = failures.get("robots-disallowed", 0) + 1
            append_progress(
                paths,
                {
                    "info_code": item["info_code"],
                    "fetched_at": now.isoformat(),
                    "url": url,
                    "failure_class": "robots-disallowed",
                    "robots_reason": robots.reason,
                },
            )
            continue
        limiter.before_request()
        result = run_lightpanda(binary, url, wait_ms=args.wait_ms, timeout_s=args.timeout)
        limiter.after_request()
        failure = classify(result)
        stats["attempted"] += 1
        failures[failure] = failures.get(failure, 0) + 1
        meta: dict[str, Any] = {
            "dataset": DATASET,
            "info_code": item["info_code"],
            "stock_code": item.get("stock_code"),
            "publish_date": item["publish_date"],
            "url": url,
            "url_template": template,
            "http_status": result.http_status,
            "error": result.error,
            "exit_code": result.exit_code,
            "failure_class": failure,
            "fetched_at": now.isoformat(),
            "elapsed_s": result.elapsed_s,
            "engine": f"lightpanda/{engine_version}",
            "engine_sha256": engine_sha,
            "user_agent": USER_AGENT,
            "robots": {"url": robots.url, "status": robots.status, "reason": robots.reason},
            "window_ok": True,
            "impl_version": IMPL_VERSION,
        }
        if failure == "ok" and result.content:
            meta.update(gzip_write(paths.body_file(publish_date, item["info_code"]), result.content))
            meta["html_chars"] = len(result.content)
            write_json_atomic(paths.meta_file(publish_date, item["info_code"]), meta)
            stats["ok"] += 1
            consecutive_blocks = 0
        else:
            meta["stderr_tail"] = result.stderr[-800:]
            write_json_atomic(paths.meta_file(publish_date, item["info_code"]), meta)
            stats["failed"] += 1
            if failure in BLOCKING_CLASSES:
                consecutive_blocks += 1
                if consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
                    aborted = f"{consecutive_blocks} consecutive {failure}; aborting (no bypass)"
                    append_progress(paths, meta)
                    break
                backoff = BLOCK_BACKOFF_S[min(consecutive_blocks - 1, len(BLOCK_BACKOFF_S) - 1)]
                clock.sleep(backoff)
                meta["backoff_s"] = backoff
            else:
                consecutive_blocks = 0
        append_progress(paths, meta)
        if args.verbose:
            print(f"[{stats['attempted']}/{run_cap}] {item['info_code']} {failure} {result.elapsed_s}s", file=sys.stderr)
    payload = {
        "run_id": run_id,
        "worklist": args.worklist,
        "rows_in_worklist": len(worklist),
        "stats": stats,
        "failures": failures,
        "aborted": aborted,
        "window": {"start": "22:00", "end": "07:00", "tz": "Asia/Shanghai"},
        "limits": {
            "min_interval_s": limiter.min_interval,
            "jitter_max_s": limiter.jitter,
            "rest_every": limiter.rest_every,
            "daily_cap": daily_cap,
            "run_cap": run_cap,
        },
        "pacing": {
            "requests": limiter.requests,
            "rests": limiter.rests,
            "waits": limiter.waits[:2000],
        },
        "robots": {"url": robots.url, "status": robots.status, "reason": robots.reason, "sha256": robots.sha256},
        "engine": {"version": engine_version, "sha256": engine_sha},
        "started_at": started_at.isoformat(),
        "finished_at": clock.now().isoformat(),
        "elapsed_s": round((clock.now() - started_at).total_seconds(), 1),
    }
    write_json_atomic(paths.run_dir / f"{run_id}.json", payload)
    return payload


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


def scan_detail(paths: DetailPaths) -> dict[str, Any]:
    ok = 0
    classes: dict[str, int] = {}
    dates: dict[str, int] = {}
    total_gzip = 0
    for meta_path in paths.detail_dir.glob("*/*/meta.json"):
        try:
            meta = read_json(meta_path)
        except Exception:
            continue
        failure = meta.get("failure_class", "unknown")
        classes[failure] = classes.get(failure, 0) + 1
        if failure == "ok":
            ok += 1
            dates[str(meta.get("publish_date"))[:7]] = dates.get(str(meta.get("publish_date"))[:7], 0) + 1
            total_gzip += int(meta.get("gzip_bytes") or 0)
    return {"ok_docs": ok, "by_failure": classes, "by_month": dict(sorted(dates.items())), "gzip_bytes": total_gzip}


def cmd_reclassify(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    """Recompute failure_class from the archived body; never touches the network."""
    changed = 0
    scanned = 0
    for meta_path in sorted(paths.detail_dir.glob("*/*/meta.json")):
        scanned += 1
        try:
            meta = read_json(meta_path)
        except Exception:
            continue
        body = meta_path.parent / "body.html.gz"
        if not body.exists():
            # failed attempts have no archived body: keep the original
            # classification (captcha / 429 / ...) as audit evidence
            continue
        with gzip.open(body, "rb") as fh:
            content = fh.read().decode("utf-8", errors="replace")
        result = FetchResult(
            http_status=meta.get("http_status"),
            content=content,
            error=meta.get("error"),
            bot_challenge=None,
            stderr=meta.get("stderr_tail") or "",
            elapsed_s=float(meta.get("elapsed_s") or 0.0),
            exit_code=int(meta.get("exit_code") or 0),
        )
        new_class = classify(result)
        if new_class != meta.get("failure_class"):
            meta["failure_class_previous"] = meta.get("failure_class")
            meta["failure_class"] = new_class
            meta["reclassified_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            write_json_atomic(meta_path, meta)
            changed += 1
    return {"scanned": scanned, "changed": changed}


def cmd_stats(args: argparse.Namespace, paths: DetailPaths) -> dict[str, Any]:
    con = connect_ro(paths.db_path)
    try:
        should = con.execute(
            "SELECT count(*) FROM report_meta WHERE publish_year IN (2023, 2024)"
        ).fetchone()[0]
        by_year = con.execute(
            "SELECT publish_year, count(*) FROM report_meta WHERE publish_year IN (2023,2024) GROUP BY 1 ORDER BY 1"
        ).fetchall()
    finally:
        con.close()
    scanned = scan_detail(paths)
    return {
        "should_extract_2023_2024": should,
        "should_by_year": {str(y): n for y, n in by_year},
        "fetched_ok": scanned["ok_docs"],
        "missing": should
        - sum(n for month, n in scanned["by_month"].items() if month.startswith(("2023-", "2024-"))),
        "by_failure": scanned["by_failure"],
        "by_month": scanned["by_month"],
        "gzip_bytes": scanned["gzip_bytes"],
        "progress_lines": sum(1 for _ in paths.progress_file.open(encoding="utf-8")) if paths.progress_file.exists() else 0,
        "stats_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-root", help="QUANTDB_HOME, default /srv/share/quantdb")
    p.add_argument("--db-path", help="DuckDB file; default <data-root>/db/quantdb.duckdb")
    p.add_argument("--lightpanda", help="Lightpanda binary (or LIGHTPANDA_BIN)")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="build a worklist from report_meta (no network)")
    plan.add_argument("--stocks", default="600519,002594,300750")
    plan.add_argument("--start", default="2023-01-01")
    plan.add_argument("--end", default="2024-12-31")
    plan.add_argument("--limit", type=int, default=200)
    plan.add_argument("--url-template", default="info", choices=sorted(URL_TEMPLATES))
    plan.add_argument("--worklist")

    probe = sub.add_parser("probe", help="try every URL template for a few reports")
    probe.add_argument("--info-code", action="append", required=True)
    probe.add_argument("--run-cap", type=int, default=RUN_CAP)
    probe.add_argument("--wait-ms", type=int, default=5000)
    probe.add_argument("--timeout", type=float, default=90.0)

    fetch = sub.add_parser("fetch", help="fetch a worklist inside the allowed window")
    fetch.add_argument("--worklist", required=True)
    fetch.add_argument("--resume", action="store_true", default=True)
    fetch.add_argument("--no-resume", dest="resume", action="store_false")
    fetch.add_argument("--run-cap", type=int, default=RUN_CAP)
    fetch.add_argument("--daily-cap", type=int, default=DAILY_CAP)
    fetch.add_argument("--url-template", choices=sorted(URL_TEMPLATES), help="override the worklist template")
    fetch.add_argument("--wait-ms", type=int, default=5000)
    fetch.add_argument("--timeout", type=float, default=90.0)
    fetch.add_argument("--verbose", action="store_true")

    sub.add_parser("reclassify", help="recompute failure_class from stored bodies (no network)")
    sub.add_parser("stats", help="coverage vs report_meta (no network)")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    paths = DetailPaths(Paths.from_args(args.data_root, args.db_path))
    try:
        if args.command == "plan":
            payload = cmd_plan(args, paths)
        elif args.command == "probe":
            payload = cmd_probe(args, paths)
        elif args.command == "fetch":
            payload = cmd_fetch(args, paths)
        elif args.command == "reclassify":
            payload = cmd_reclassify(args, paths)
        elif args.command == "stats":
            payload = cmd_stats(args, paths)
        else:
            raise SystemExit(f"unknown command {args.command}")
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        if os.environ.get("REPORTS_DEBUG"):
            traceback.print_exc()
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
