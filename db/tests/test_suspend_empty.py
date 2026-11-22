from __future__ import annotations

import pandas as pd

from db.etl import common, fetch


class EmptyFetcher:
    def call(self, method, dataset, *, trade_date=None, ts_code=None, **kwargs):
        return pd.DataFrame()


def test_empty_suspend_day_is_a_valid_partition(qdb):
    done, failures = fetch.fetch_by_trade_date(
        EmptyFetcher(), "suspend_d", "suspend_d", ["20200102"], concurrency=1
    )
    assert failures == []
    assert done == 1
    meta = common.read_manifest("suspend_d", "20200102")
    assert meta is not None
    assert meta["rows"] == 0
    assert common.manifest_ok("suspend_d", "20200102")
