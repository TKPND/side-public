"""Phase 81 Plan 02 Task 2: regime_labeler unit tests.

Tests cover:
- duration_bucket boundary conditions (15m/30m/60m bar sizes)
- join_liquidity unmatched raises ValueError
- join_liquidity many_to_one validation
- cell_id format (_x_ separator, enum subset)
- sign_breakdown.json SHA256 byte-identity (read-only)
- AUDUSD excluded via inner join (no liquidity coverage)
- event_type column present (renamed from event_name)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

# -- import via importlib (scripts/v4.8 is not a valid Python package path) --
_SPEC = importlib.util.spec_from_file_location(
    "regime_labeler", Path(__file__).parent / "regime_labeler.py"
)
regime_labeler = importlib.util.module_from_spec(_SPEC)
sys.modules["regime_labeler"] = regime_labeler
_SPEC.loader.exec_module(regime_labeler)

duration_bucket = regime_labeler.duration_bucket
join_liquidity = regime_labeler.join_liquidity
label_slots = regime_labeler.label_slots
DURATION_BUCKETS = regime_labeler.DURATION_BUCKETS
LIQUIDITY_REGIMES = regime_labeler.LIQUIDITY_REGIMES
DURATION_BOUNDARY_MIN = regime_labeler.DURATION_BOUNDARY_MIN


# ---------------------------------------------------------------------------
# duration_bucket boundary tests
# ---------------------------------------------------------------------------


def test_duration_bucket_boundary_exactly_60() -> None:
    """4 bars * 15 min = 60 min — inclusive boundary -> '0-60m'."""
    assert duration_bucket(4, 15) == "0-60m"


def test_duration_bucket_just_over_60() -> None:
    """5 bars * 15 min = 75 min — just over -> '60-120m'."""
    assert duration_bucket(5, 15) == "60-120m"


def test_duration_bucket_1h_bar() -> None:
    """1 bar * 60 min = 60 min (inclusive) -> '0-60m'; 2 bars -> '60-120m'."""
    assert duration_bucket(1, 60) == "0-60m"
    assert duration_bucket(2, 60) == "60-120m"


def test_duration_bucket_30m_bar() -> None:
    """2 bars * 30 min = 60 min (exact boundary) -> '0-60m'; 3 -> '60-120m'."""
    assert duration_bucket(2, 30) == "0-60m"
    assert duration_bucket(3, 30) == "60-120m"


# ---------------------------------------------------------------------------
# join_liquidity tests
# ---------------------------------------------------------------------------


def _sample_slots() -> pd.DataFrame:
    """Minimal slots DataFrame matching post-flatten schema."""
    return pd.DataFrame(
        {
            "event_type": ["FOMC", "ECB", "NFP"],
            "pair": ["EURUSD", "EURUSD", "USDJPY"],
            "window_offset": [1, 1, 1],
            "hold_bars": [4, 5, 1],
            "bar_size_minutes": [15, 15, 60],
            "exit_type": ["none", "none", "none"],
            "long": [3, 2, 5],
            "neutral": [2, 2, 0],
            "short": [0, 1, 0],
        }
    )


def _sample_liquidity_full() -> pd.DataFrame:
    """Liquidity covering all three rows of _sample_slots."""
    return pd.DataFrame(
        {
            "event_type": ["FOMC", "ECB", "NFP"],
            "pair": ["EURUSD", "EURUSD", "USDJPY"],
            "liquidity_regime": ["LOW", "MID", "HIGH"],
        }
    )


def test_join_liquidity_unmatched_raises() -> None:
    """Missing liquidity match raises ValueError mentioning 'without liquidity_regime'."""
    slots = _sample_slots()
    # Liquidity only covers FOMC — ECB and NFP will be unmatched
    liquidity = pd.DataFrame(
        {
            "event_type": ["FOMC"],
            "pair": ["EURUSD"],
            "liquidity_regime": ["LOW"],
        }
    )
    with pytest.raises(ValueError, match="without liquidity_regime"):
        join_liquidity(slots, liquidity)


def test_join_liquidity_many_to_one_validated() -> None:
    """Duplicate liquidity key triggers pandas MergeError (validate='many_to_one')."""
    slots = _sample_slots()
    liquidity = pd.DataFrame(
        {
            # FOMC/EURUSD duplicated
            "event_type": ["FOMC", "FOMC", "ECB", "NFP"],
            "pair": ["EURUSD", "EURUSD", "EURUSD", "USDJPY"],
            "liquidity_regime": ["LOW", "HIGH", "MID", "HIGH"],
        }
    )
    with pytest.raises(Exception):
        join_liquidity(slots, liquidity)


# ---------------------------------------------------------------------------
# cell_id formatting
# ---------------------------------------------------------------------------


def _make_sign_breakdown_json(pairs_events: list[tuple[str, str]]) -> str:
    """Build a minimal per_pair_event_slot_tally JSON for given (pair, event) combos."""
    tally: dict = {}
    for pair, event in pairs_events:
        p = pair.lower()
        e = event.lower()
        if p not in tally:
            tally[p] = {}
        tally[p][e] = {
            "1/1/none": {"long": 3, "neutral": 2, "short": 0},
            "1/6/none": {"long": 1, "neutral": 3, "short": 1},
        }
    return json.dumps({"per_pair_event_slot_tally": tally})


def test_cell_id_format(tmp_path: Path) -> None:
    """cell_id uses '_x_' separator and values are within enum cross-product."""
    sb = tmp_path / "sign_breakdown.json"
    sb.write_text(
        _make_sign_breakdown_json([("EURUSD", "FOMC"), ("USDJPY", "ECB")]),
        encoding="utf-8",
    )
    liq = tmp_path / "liquidity.parquet"
    pd.DataFrame(
        {
            "event_name": ["FOMC", "ECB"],
            "pair": ["EURUSD", "USDJPY"],
            "liquidity_regime": ["LOW", "HIGH"],
        }
    ).to_parquet(liq, index=False)

    df = label_slots(sb, liq, bar_size_minutes=15)
    valid_cells = {f"{b}_x_{r}" for b in DURATION_BUCKETS for r in LIQUIDITY_REGIMES}
    assert set(df["cell_id"].unique()).issubset(valid_cells)
    # Spot check: hold_bars=1 * 15 = 15min -> "0-60m" + LOW -> "0-60m_x_LOW"
    fomc_row = df[(df["event_type"] == "FOMC") & (df["hold_bars"] == 1)].iloc[0]
    assert fomc_row["cell_id"] == "0-60m_x_LOW"
    # hold_bars=6 * 15 = 90min -> "60-120m"
    fomc_long = df[(df["event_type"] == "FOMC") & (df["hold_bars"] == 6)].iloc[0]
    assert fomc_long["cell_id"] == "60-120m_x_LOW"


# ---------------------------------------------------------------------------
# sign_breakdown.json byte-identity (REGIME-05)
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sign_breakdown_not_modified(tmp_path: Path) -> None:
    """label_slots must not alter sign_breakdown.json bytes."""
    sb = tmp_path / "sign_breakdown.json"
    sb.write_text(
        _make_sign_breakdown_json([("EURUSD", "FOMC")]),
        encoding="utf-8",
    )
    liq = tmp_path / "liquidity.parquet"
    pd.DataFrame(
        {
            "event_name": ["FOMC"],
            "pair": ["EURUSD"],
            "liquidity_regime": ["MID"],
        }
    ).to_parquet(liq, index=False)

    before = _sha256(sb)
    _ = label_slots(sb, liq, bar_size_minutes=15)
    after = _sha256(sb)
    assert before == after, "regime_labeler must not modify sign_breakdown.json bytes"


# ---------------------------------------------------------------------------
# AUDUSD exclusion (inner join — no liquidity coverage)
# ---------------------------------------------------------------------------


def test_join_excludes_audusd(tmp_path: Path) -> None:
    """AUDUSD rows are dropped because liquidity_per_slot has no AUDUSD coverage."""
    sb = tmp_path / "sign_breakdown.json"
    sb.write_text(
        _make_sign_breakdown_json([("EURUSD", "FOMC"), ("AUDUSD", "FOMC")]),
        encoding="utf-8",
    )
    liq = tmp_path / "liquidity.parquet"
    # Only EURUSD in liquidity — AUDUSD absent
    pd.DataFrame(
        {
            "event_name": ["FOMC"],
            "pair": ["EURUSD"],
            "liquidity_regime": ["LOW"],
        }
    ).to_parquet(liq, index=False)

    df = label_slots(sb, liq, bar_size_minutes=15)
    assert "AUDUSD" not in df["pair"].values
    assert "EURUSD" in df["pair"].values


# ---------------------------------------------------------------------------
# event_type column present (renamed from event_name)
# ---------------------------------------------------------------------------


def test_event_type_column_present(tmp_path: Path) -> None:
    """Output DataFrame has 'event_type' column (renamed from event_name in liquidity)."""
    sb = tmp_path / "sign_breakdown.json"
    sb.write_text(
        _make_sign_breakdown_json([("EURUSD", "FOMC")]),
        encoding="utf-8",
    )
    liq = tmp_path / "liquidity.parquet"
    pd.DataFrame(
        {
            "event_name": ["FOMC"],
            "pair": ["EURUSD"],
            "liquidity_regime": ["HIGH"],
        }
    ).to_parquet(liq, index=False)

    df = label_slots(sb, liq, bar_size_minutes=15)
    assert "event_type" in df.columns
    assert df["event_type"].iloc[0] == "FOMC"
