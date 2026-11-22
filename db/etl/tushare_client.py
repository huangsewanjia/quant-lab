"""Tushare HTTP client with cap-aware window splitting.

Why not the SDK: WORK-513 needs the raw upstream error code (40203 = no
permission for this interface) to verify the permission matrix, and it needs
the exact row count of every response to detect server-side truncation. Both
are visible in the raw JSON response and are lost behind the SDK's exceptions.
`tushare` is still installed and used by db/etl/introspect.py for a
field-level cross-check.

Measured facts (2026-11-22, token tier 2000, direct api.tushare.pro):
  * global rate limit ~200 calls/min (250 sequential calls took 75.0 s, no error);
  * single-call row caps: daily/daily_basic/adj_factor 6000, stk_limit 7800,
    suspend_d 5000, namechange 10000, dividend 2000;
  * income/balancesheet/cashflow/fina_indicator require ts_code
    (cross-sectional queries fail with code 50101);
  * income has no 100-row cap (longest history observed: 178 rows);
  * balancesheet/cashflow/fina_indicator are hard-capped at 100 rows.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pandas as pd
import requests

from . import common

API_URL = "https://api.tushare.pro"

# Truncation thresholds: a response that reaches the threshold may have been
# cut off by the server and must be re-fetched over a narrower window.
ROW_CAPS: dict[str, int] = {
    "daily": 6000,
    "daily_basic": 6000,
    "adj_factor": 6000,
    "stk_limit": 7800,
    "suspend_d": 5000,
    "index_daily": 20000,
    "namechange": 10000,
    "dividend": 2000,
    "income": 170,
    "balancesheet": 100,
    "cashflow": 100,
    "fina_indicator": 100,
}

# Interfaces that accept a start_date/end_date window.
RANGE_CAPABLE = {
    "daily",
    "daily_basic",
    "adj_factor",
    "stk_limit",
    "suspend_d",
    "income",
    "balancesheet",
    "cashflow",
    "fina_indicator",
}


class TushareError(RuntimeError):
    def __init__(self, code: int, message: str, api: str, params: dict[str, Any]) -> None:
        super().__init__(f"[{code}] {api}: {message}")
        self.code = code
        self.message = message
        self.api = api
        self.params = params


class TruncatedResponse(RuntimeError):
    """Raised when a response hits the row cap and cannot be split any further."""


def _mid_date(start: str, end: str) -> str:
    """Return the day after the midpoint of an inclusive YYYYMMDD range."""
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end)
    mid = lo + (hi - lo) / 2
    return (mid.normalize() + pd.Timedelta(days=1)).strftime("%Y%m%d")


class TushareClient:
    """Thread-safe, rate-limited Tushare client."""

    def __init__(
        self,
        *,
        rpm: float = 195.0,
        retries: int = 2,
        concurrency: int = 8,
        logger: common.CallLogger | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency 必须在 1..8")
        self.token = common.read_token(required=True)
        self.retries = retries
        self.concurrency = concurrency
        self.limiter = common.RateLimiter(rpm)
        self.log = logger or common.CallLogger()
        self._local = threading.local()
        self.calls = 0
        self._calls_lock = threading.Lock()
        # Global bound on requests in flight, shared by every worker thread.
        self._semaphore = threading.BoundedSemaphore(max(1, concurrency))

    @property
    def session(self) -> requests.Session:
        if getattr(self._local, "session", None) is None:
            session = requests.Session()
            session.headers.update({"Content-Type": "application/json"})
            self._local.session = session
        return self._local.session

    def call(
        self,
        api: str,
        params: dict[str, Any],
        *,
        dataset: str | None = None,
        partition: str | None = None,
    ) -> tuple[list[str], list[list[Any]]]:
        """One upstream call. Returns (fields, rows). Raises TushareError."""
        payload = {
            "api_name": api,
            "token": self.token,
            "params": {k: v for k, v in params.items() if v not in (None, "")},
            "fields": "",
        }
        last: Exception | None = None
        for attempt in range(1, self.retries + 2):
            self.limiter.wait()
            started = time.monotonic()
            with self._calls_lock:
                # Every attempt counts, including the ones that failed and were
                # retried: the call log is the evidence for the upstream spend.
                self.calls += 1
            try:
                with self._semaphore:
                    response = self.session.post(API_URL, json=payload, timeout=120)
                response.raise_for_status()
                body = response.json()
                elapsed = int((time.monotonic() - started) * 1000)
                code = int(body.get("code", -1))
                if code != 0:
                    message = str(body.get("msg", ""))
                    self.log.log(
                        dataset or api,
                        api=api,
                        partition=partition,
                        params=dict(payload["params"]),
                        duration_ms=elapsed,
                        rows=None,
                        status="error",
                        error=f"{code}: {message}",
                        attempt=attempt,
                    )
                    last = TushareError(code, message, api, payload["params"])
                    if code in (40203, 50101, 40001):
                        # Permission / parameter errors are deterministic: do not retry.
                        raise last
                    if attempt <= self.retries:
                        time.sleep(1.5 * (2 ** (attempt - 1)))
                        continue
                    raise last
                data = body.get("data") or {}
                fields = list(data.get("fields") or [])
                items = list(data.get("items") or [])
                self.log.log(
                    dataset or api,
                    api=api,
                    partition=partition,
                    params=dict(payload["params"]),
                    duration_ms=elapsed,
                    rows=len(items),
                    status="ok",
                    attempt=attempt,
                )
                return fields, items
            except requests.RequestException as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                self.log.log(
                    dataset or api,
                    api=api,
                    partition=partition,
                    params=dict(payload["params"]),
                    duration_ms=elapsed,
                    rows=None,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    attempt=attempt,
                )
                last = exc
                if attempt <= self.retries:
                    time.sleep(1.5 * (2 ** (attempt - 1)))
                    continue
                raise
        assert last is not None
        raise last

    def frame(self, api: str, params: dict[str, Any], **kwargs: Any) -> pd.DataFrame:
        fields, items = self.call(api, params, **kwargs)
        if not items:
            return pd.DataFrame(columns=fields)
        return pd.DataFrame(items, columns=fields)

    def fetch_window(
        self,
        api: str,
        params: dict[str, Any],
        *,
        dataset: str,
        partition: str | None = None,
        cap: int | None = None,
        max_depth: int = 12,
    ) -> pd.DataFrame:
        """Fetch a window, splitting it when the server truncates the response.

        The server truncates silently at a fixed row cap: a response whose row
        count equals the cap may be missing rows. Splitting the date window
        until the response is strictly below the cap guarantees completeness.
        """
        threshold = cap if cap is not None else ROW_CAPS.get(api)
        fields, items = self.call(api, params, dataset=dataset, partition=partition)
        if not items:
            return pd.DataFrame(columns=fields)
        if threshold is None or len(items) < threshold:
            return pd.DataFrame(items, columns=fields)
        if max_depth <= 0:
            raise TruncatedResponse(f"{api} {params} 截断且无法继续拆分")
        start, end = params.get("start_date"), params.get("end_date")
        if not start or not end or start >= end:
            raise TruncatedResponse(f"{api} {params} 行数触顶({len(items)})且无日期窗口可拆")
        mid = _mid_date(str(start), str(end))
        left_params = {**params, "start_date": start, "end_date": str(int(mid) - 1)}
        right_params = {**params, "start_date": mid, "end_date": end}
        left = self.fetch_window(
            api,
            left_params,
            dataset=dataset,
            partition=partition,
            cap=threshold,
            max_depth=max_depth - 1,
        )
        right = self.fetch_window(
            api,
            right_params,
            dataset=dataset,
            partition=partition,
            cap=threshold,
            max_depth=max_depth - 1,
        )
        frames = [frame for frame in (left, right) if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=fields)
        out = pd.concat(frames, ignore_index=True)
        return out
