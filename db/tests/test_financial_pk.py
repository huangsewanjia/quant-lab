"""Regression tests for the financial primary key.

The upstream returns the same report twice: the original filing and the
restatement (``update_flag`` 1 vs 0), and for a few codes the restatement
actually changed the reported numbers. ``fina_indicator`` returns two
computation passes of the same report that differ in the cash-flow-derived
fields. Dropping one of those rows silently discarded ~25-30% of the
financial data, so every upstream row is kept and numbered by ``dup_seq``.
"""

from __future__ import annotations

import pandas as pd

from db.etl import common


def _statement_frame() -> pd.DataFrame:
    """Two versions of the same 2023 annual report."""
    return pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20231231", "20231231"],
            "report_type": ["1", "1"],
            "ann_date": ["20240328", "20240328"],
            "update_flag": ["1", "0"],
            "ebit": [1234.0, 1200.0],
        }
    )


def test_assign_dup_seq_numbers_the_rows_of_a_group():
    frame = _statement_frame()
    out = common.assign_dup_seq(frame, list(common.BASE_PK["income"]))
    assert len(out) == 2
    assert sorted(out["dup_seq"].tolist()) == [0, 1]
    # every row is kept
    assert set(out["ebit"].tolist()) == {1234.0, 1200.0}


def test_assign_dup_seq_leaves_unique_rows_at_zero():
    frame = pd.DataFrame(
        {
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20231231", "20230930"],
            "report_type": ["1", "1"],
            "ann_date": ["20240328", "20231028"],
            "ebit": [1.0, 2.0],
        }
    )
    out = common.assign_dup_seq(frame, list(common.BASE_PK["income"]))
    assert out["dup_seq"].tolist() == [0, 0]


def test_assign_dup_seq_is_deterministic():
    """The order the API returned the rows in must not change the numbering."""
    frame = _statement_frame()
    forward = common.assign_dup_seq(frame, list(common.BASE_PK["income"]))
    backward = common.assign_dup_seq(
        frame.iloc[::-1].reset_index(drop=True), list(common.BASE_PK["income"])
    )
    key = ["ts_code", "end_date", "ann_date", "dup_seq"]
    assert (
        forward.sort_values(key).reset_index(drop=True)[key]
        .equals(backward.sort_values(key).reset_index(drop=True)[key])
    )


def test_write_partition_keeps_both_report_versions(qdb):
    """No upstream row may be dropped by the primary-key de-duplication."""
    frame = _statement_frame()
    qdb.write_partition(frame, "income", "600000.SH", "test")
    stored = pd.read_parquet(qdb.partition_file("income", "600000.SH"))
    assert len(stored) == 2
    assert sorted(stored["ebit"].tolist()) == [1200.0, 1234.0]
    manifest = qdb.read_manifest("income", "600000.SH")
    assert manifest["rows"] == 2


def test_the_dup_seq_column_is_part_of_the_primary_key():
    for dataset in ("income", "balancesheet", "cashflow", "fina_indicator", "dividend"):
        assert "dup_seq" in common.TABLE_PK[dataset]
        assert common.TABLE_PK[dataset][:-1] == common.BASE_PK[dataset]


def test_datasets_without_a_duplicate_key_are_untouched():
    for dataset in ("daily", "daily_basic", "adj_factor", "namechange"):
        assert "dup_seq" not in common.TABLE_PK[dataset]
        assert dataset not in common.DUP_SEQ_DATASETS
