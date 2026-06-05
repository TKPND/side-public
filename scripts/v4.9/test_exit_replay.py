"""Tests for scripts/v4.9/exit_replay.py (EXIT-02 + EXIT-03).

Covers VALIDATION.md task IDs 86-03-01 through 86-03-06.

Note: polars 1.40 pl.read_parquet_metadata() returns a plain dict directly
(not an object with .custom_metadata attribute). Assertions use dict access.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import polars as pl
import pytest

# Load exit_replay.py as module (absolute path to scripts/v4.9/exit_replay.py)
_MODULE_PATH = pathlib.Path(__file__).parent / "exit_replay.py"
_spec = importlib.util.spec_from_file_location("exit_replay", _MODULE_PATH)
exit_replay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exit_replay)

SIZING_EXIT_COMMIT = "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab"


@pytest.fixture
def long_trade_atr_stop_trigger() -> dict[str, Any]:
    """Long trade where bar[0].low crosses atr_stop (entry - 2*atr_at_entry).

    entry=100, atr_at_entry=0.5, k=2 => atr_stop = 99.0
    bar[0].low = 98.9 < 99.0 => fires atr_stop at bar 0.
    """
    return {
        "trade_id": 1,
        "entry_bar": 10,
        "entry_price": 100.0,
        "direction": 1,
        "atr_at_entry": 0.5,
        "bars": [
            {
                "high": 100.2,
                "low": 98.9,
                "close": 99.5,
                "atr": 0.5,
            },  # triggers atr_stop
            {"high": 100.0, "low": 99.0, "close": 99.5, "atr": 0.5},
        ],
    }


@pytest.fixture
def short_trade_atr_stop_trigger() -> dict[str, Any]:
    """Short trade where bar[0].high crosses atr_stop (entry + 2*atr_at_entry).

    entry=100, atr_at_entry=0.5, k=2 => atr_stop = 101.0
    bar[0].high = 101.1 > 101.0 => fires atr_stop at bar 0.
    """
    return {
        "trade_id": 2,
        "entry_bar": 0,
        "entry_price": 100.0,
        "direction": -1,
        "atr_at_entry": 0.5,
        "bars": [
            {
                "high": 101.1,
                "low": 99.8,
                "close": 100.5,
                "atr": 0.5,
            },  # triggers atr_stop
            {"high": 100.0, "low": 99.5, "close": 99.8, "atr": 0.5},
        ],
    }


@pytest.fixture
def long_trade_no_trigger_runs_to_fold_end() -> dict[str, Any]:
    """Long trade where no rule fires; reaches D-17 fold_end sentinel.

    entry=100, atr_at_entry=0.5, k=2 => atr_stop = 99.0
    bars never go below 99.9 => no atr_stop, trailing: max_close=100.4 - 2*0.5=99.4, low=100.0 > 99.4 => no trailing.
    Only 2 bars so technical_stop (lookback=3) never activates.
    """
    return {
        "trade_id": 3,
        "entry_bar": 0,
        "entry_price": 100.0,
        "direction": 1,
        "atr_at_entry": 0.5,
        "bars": [
            {"high": 100.5, "low": 99.9, "close": 100.2, "atr": 0.5},
            {"high": 100.6, "low": 100.0, "close": 100.4, "atr": 0.5},
        ],
    }


# 86-03-02: priority order
def test_priority_order():
    """EXIT-02: EXIT_PRIORITY matches SEAL exit_commit.json literal."""
    assert exit_replay.EXIT_PRIORITY == [
        "atr_stop",
        "technical_stop",
        "trailing_stop",
        "time_stop",
    ]


# 86-03-03: time_stop disabled
def test_time_stop_disabled(long_trade_no_trigger_runs_to_fold_end):
    """EXIT-02 D-07: max_hold_bars=None must not fire time_stop."""
    result = exit_replay.replay_trade(
        long_trade_no_trigger_runs_to_fold_end,
        atr_k=2.0,
        max_hold_bars=None,
    )
    assert result["exit_rule"] != "time_stop"
    # Falls through to fold_end sentinel (D-17)
    assert result["exit_rule"] == "fold_end"


# 86-03-04: atr_stop long
def test_atr_stop_long(long_trade_atr_stop_trigger):
    """EXIT-02 D-06: long trade fires atr_stop when bar.low <= entry - k*atr_at_entry."""
    result = exit_replay.replay_trade(long_trade_atr_stop_trigger, atr_k=2.0)
    assert result["exit_rule"] == "atr_stop"
    assert result["trade_id"] == 1
    assert result["exit_bar"] == 10  # entry_bar(10) + offset(0)


# 86-03-05: atr_stop short
def test_atr_stop_short(short_trade_atr_stop_trigger):
    """EXIT-02 D-06: short trade fires atr_stop when bar.high >= entry + k*atr_at_entry."""
    result = exit_replay.replay_trade(short_trade_atr_stop_trigger, atr_k=2.0)
    assert result["exit_rule"] == "atr_stop"
    assert result["trade_id"] == 2


# 86-03-01: smoke test 1 cell × 1 fold
def test_smoke_1cell_1fold(
    tmp_path, long_trade_atr_stop_trigger, short_trade_atr_stop_trigger
):
    """EXIT-02 smoke: replay 2 trades (1 cell × 1 fold) and write parquet."""
    rows = exit_replay.replay_all_trades(
        [long_trade_atr_stop_trigger, short_trade_atr_stop_trigger],
        fold_id=0,
        atr_k=2.0,
        max_hold_bars=None,
    )
    out = tmp_path / "exit_replayed.parquet"
    exit_replay.write_exit_replayed_parquet(rows, out)

    assert out.exists()
    df = pl.read_parquet(out)
    # Grain: fold / trade_id / exit_rule / exit_bar / pnl
    assert set(df.columns) == {"cell_id", "fold", "trade_id", "exit_rule", "exit_bar", "pnl"}
    assert df.height == 2
    assert df["exit_rule"].to_list() == ["atr_stop", "atr_stop"]


# ------------------------------------------------------------------
# EXIT-03 tests (VALIDATION.md 86-04-01..03 + D-20 consistency)
# ------------------------------------------------------------------

import json as _json
import math as _math


@pytest.fixture
def twenty_five_bar_long_trade():
    """Long trade with 25 bars so fragility grid window=21 can seed."""
    bars = []
    for i in range(25):
        bars.append(
            {
                "high": 101.0 + 0.01 * i,
                "low": 99.0 - 0.01 * i,
                "close": 100.0 + 0.005 * i,
                "atr": 0.5,
            }
        )
    return {
        "trade_id": 42,
        "entry_bar": 0,
        "entry_price": 100.0,
        "direction": 1,
        "atr_at_entry": 0.5,
        "bars": bars,
    }


# 86-04-03: ATR_WINDOW compliance assertion
def test_atr_window_compliance():
    """EXIT-03: compliance assert passes for 14 and raises for non-sealed windows."""
    exit_replay.assert_atr_window_compliance(14)  # must not raise

    for bad in (0, 7, 21, 100):
        with pytest.raises(exit_replay.AtrComplianceError):
            exit_replay.assert_atr_window_compliance(bad)


# 86-04-02: fragility grid keys
def test_fragility_grid(twenty_five_bar_long_trade):
    """EXIT-03: run_fragility_grid returns keys for [7, 14, 21]."""
    results = exit_replay.run_fragility_grid(
        [twenty_five_bar_long_trade],
        atr_k=2.0,
        max_hold_bars=None,
    )
    assert set(results.keys()) == {"atr_window=7", "atr_window=14", "atr_window=21"}
    for key, agg in results.items():
        assert "n_trades" in agg
        assert "exit_rule_dist" in agg


# 86-04-01: fragility report file + provenance stamp
def test_fragility_report(tmp_path, twenty_five_bar_long_trade):
    """EXIT-03 D-15: atr_fragility_report.json contains quad-pin provenance stamp."""
    results = exit_replay.run_fragility_grid(
        [twenty_five_bar_long_trade],
        atr_k=2.0,
        max_hold_bars=None,
    )
    out = tmp_path / "atr_fragility_report.json"
    exit_replay.write_atr_fragility_report(results, out)

    assert out.exists()
    report = _json.loads(out.read_text())
    assert report["sizing_exit_commit"] == (
        "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab"
    )
    assert report["atr_window_primary"] == 14
    assert report["atr_k_primary"] == 2.0
    assert report["fragility_grid"] == [7, 14, 21]
    assert report["atr_k_fragility_grid"] == [1.5, 2.0, 2.5, 3.0]
    assert report["compliance_check"]["primary_window_in_grid"] is True
    assert report["compliance_check"]["atr_window_bars_sealed"] == 14
    # Canonical serialization: sort_keys=True ensures sizing_exit_commit early
    raw = out.read_bytes()
    assert b'"sizing_exit_commit":"8a4e49d2' in raw


# D-20: ATR consistency cross-check
def test_atr_window_14_consistency(twenty_five_bar_long_trade):
    """D-20: compute_atr_python(bars, 14) at index >= 13 is finite and positive.

    We don't have a real Rust output here, but we assert that the Wilder seed
    and the EMA step produce finite values for bars of length >= window.
    """
    bars = twenty_five_bar_long_trade["bars"]
    atr_series = exit_replay.compute_atr_python(bars, 14)
    assert len(atr_series) == len(bars)
    # Before index 13 (0-based) values are NaN (seed not ready)
    for i in range(13):
        assert _math.isnan(atr_series[i])
    # Seed + EMA region is finite and positive
    for i in range(13, len(bars)):
        assert _math.isfinite(atr_series[i])
        assert atr_series[i] > 0.0


# 86-03-06: provenance stamp
def test_provenance_stamp(tmp_path, long_trade_atr_stop_trigger):
    """EXIT-02 D-16: exit_replayed.parquet custom_metadata carries sizing_exit_commit.

    polars 1.40: pl.read_parquet_metadata() returns plain dict (not object with
    .custom_metadata attribute). Access keys directly on the returned dict.
    """
    rows = exit_replay.replay_all_trades(
        [long_trade_atr_stop_trigger],
        fold_id=0,
        atr_k=2.0,
    )
    out = tmp_path / "exit_replayed.parquet"
    exit_replay.write_exit_replayed_parquet(rows, out)

    # polars 1.40: read_parquet_metadata returns dict directly
    meta = pl.read_parquet_metadata(out)
    assert meta["sizing_exit_commit"] == SIZING_EXIT_COMMIT
    assert meta["atr_k_primary"] == "2.0"
    assert (
        meta["exit_priority_order"] == "atr_stop,technical_stop,trailing_stop,time_stop"
    )
