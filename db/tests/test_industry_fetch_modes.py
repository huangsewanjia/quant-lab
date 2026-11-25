"""行业层抓取窗口契约（WORK-576）：默认必须是日常小窗口，回填只能显式。

抓取脚本本体不在本仓库（既有形态，位于 /srv/share/quantdb/code/industry_fetch/），
这里直接 import 它们做契约测试：目录不存在时整体 skip，避免在别处 checkout 时报错。
不联网、不写 raw：只验证 argparse 默认值、`resolve_mode` 解析与各模块窗口单调性。
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest

FETCH_DIR = Path("/srv/share/quantdb/code/industry_fetch")
pytestmark = pytest.mark.skipif(
    not (FETCH_DIR / "_common.py").exists(),
    reason="行业层抓取脚本不在本机（/srv/share/quantdb/code/industry_fetch）",
)

MODULES = [
    "fetch_gfex_lithium",
    "fetch_cninfo_ann",
    "fetch_caam_output",
    "fetch_cpcaauto_nev",
    "fetch_mof_fiscal",
    "fetch_nea_power",
    "fetch_pbc_shrzgm",
    "fetch_stats_gov",
]


@pytest.fixture(scope="module")
def common():
    sys.path.insert(0, str(FETCH_DIR))
    yield importlib.import_module("_common")


def test_mode_default_is_incremental(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    assert parser.parse_args([]).mode == common.INCREMENTAL
    assert parser.parse_args(["--mode", "backfill"]).mode == common.BACKFILL
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "deep"])


def test_resolve_mode_fills_small_window_and_keeps_explicit(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--flag", dest="flag", action="store_true", default=None)
    windows = {"incremental": {"days": 30, "flag": True},
               "backfill": {"days": 1825, "flag": False}}

    args = parser.parse_args([])
    common.resolve_mode(args, windows)
    assert (args.days, args.flag) == (30, True)

    args = parser.parse_args(["--days", "7"])
    common.resolve_mode(args, windows)
    assert args.days == 7, "显式数值必须优先于 --mode"

    args = parser.parse_args(["--mode", "backfill", "--days", "7"])
    common.resolve_mode(args, windows)
    assert (args.days, args.flag) == (7, False)

    args = parser.parse_args(["--mode", "backfill"])
    common.resolve_mode(args, windows)
    assert args.days == 1825


def test_resolve_mode_calls_callables(common):
    parser = argparse.ArgumentParser()
    common.add_mode_arg(parser)
    parser.add_argument("--year", type=int, default=None)
    args = parser.parse_args([])
    common.resolve_mode(args, {"incremental": {"year": lambda: 2026}})
    assert args.year == 2026


# 日常档的旋钮上限（超过即代表默认窗口变深，验收判据「默认必须是小窗口」失守）。
# page_size 不是深度旋钮（页越大请求越少），不参与比较。
INCREMENTAL_BOUNDS = {
    "fetch_gfex_lithium": {"days": 30},
    "fetch_cninfo_ann": {"days": 90, "max_pages": 60},
    "fetch_caam_output": {"months": 3, "articles": 20, "scan": 400},
    "fetch_cpcaauto_nev": {"articles": 30, "pages": 3},
    "fetch_mof_fiscal": {"articles": 24, "pages": 4},
    "fetch_nea_power": {"pages": 3, "year_pages": 1, "articles": 20},
    "fetch_pbc_shrzgm": {"since_year": 2026},
    "fetch_stats_gov": {"pages": 6, "articles": 6},
}


@pytest.mark.parametrize("name", MODULES)
def test_incremental_window_within_bounds(common, name):
    mod = importlib.import_module(name)
    inc = mod.WINDOWS["incremental"]
    for key, bound in INCREMENTAL_BOUNDS[name].items():
        value = inc[key]
        if callable(value):
            value = value()
        assert value <= bound, f"{name}.{key}: 日常档 {value} 超过安全上限 {bound}"


@pytest.mark.parametrize("name", MODULES)
def test_backfill_window_at_least_incremental(common, name):
    mod = importlib.import_module(name)
    inc, back = mod.WINDOWS["incremental"], mod.WINDOWS["backfill"]
    checked = 0
    for key in INCREMENTAL_BOUNDS[name]:
        if key == "since_year":
            # 起始年份方向相反：越晚 = 窗口越窄，单调性由 test_pbc_backfill_starts_2021 单独断言。
            continue
        a, b = inc[key], back[key]
        if callable(a):
            a = a()
        if isinstance(b, (int, float)) and not isinstance(b, bool):
            assert a <= b, f"{name}.{key}: 回填档 {b} 小于日常档 {a}"
            checked += 1
    assert checked or name == "fetch_pbc_shrzgm", f"{name} 没有可比较的回填档旋钮"


def test_pbc_backfill_starts_2021(common):
    mod = importlib.import_module("fetch_pbc_shrzgm")
    assert mod.WINDOWS["backfill"]["since_year"] == 2021
    assert mod.WINDOWS["incremental"]["since_year"]() >= 2025


def test_gfex_incremental_has_resume(common):
    mod = importlib.import_module("fetch_gfex_lithium")
    assert mod.WINDOWS["incremental"]["resume"] is True
    assert mod.WINDOWS["incremental"]["days"] <= 30
