"""Unit tests for scripts.lib.data_fetch (Phase 96 baseline + Phase 97 Parquet migration).

Tests the 9 common logic exports from scripts/lib/data_fetch.py.
Phase 97 (Wave 1A): write_tick_csv_bq → write_tick_parquet_bq (D-01).
"""

import logging
import signal
from datetime import date, datetime

import pandas as pd
import pyarrow.parquet as pq
import pytest

from scripts.lib.data_fetch import (
    INSTRUMENT_MAP,
    OHLCV_1H_TIMEOUT_SEC,
    TICK_TIMEOUT_SEC,
    MonthTimeoutError,
    _alarm_handler,
    build_month_ranges,
    build_months,
    normalize_ohlcv_columns,
    setup_logging,
    write_tick_parquet_bq,
)


def test_instrument_map_coverage():
    """All expected pairs are present in INSTRUMENT_MAP."""
    expected = {"EURUSD", "GBPJPY", "AUDUSD", "USDJPY", "EURJPY", "GBPUSD"}
    assert expected.issubset(set(INSTRUMENT_MAP.keys()))


def test_instrument_map_contains_xauusd():
    """XAUUSD must be in INSTRUMENT_MAP (added in Phase 96)."""
    assert INSTRUMENT_MAP["XAUUSD"] == "XAU/USD"


def test_instrument_map_contains_crypto_pairs():
    """BTCUSD/ETHUSD must be in INSTRUMENT_MAP for v5.0 crypto ingest."""
    assert INSTRUMENT_MAP["BTCUSD"] == "BTC/USD"
    assert INSTRUMENT_MAP["ETHUSD"] == "ETH/USD"


def test_instrument_map_usdjpy():
    """USDJPY maps to USD/JPY."""
    assert INSTRUMENT_MAP["USDJPY"] == "USD/JPY"


def test_timeout_constants():
    """TICK_TIMEOUT_SEC=1200, OHLCV_1H_TIMEOUT_SEC=120."""
    assert TICK_TIMEOUT_SEC == 1200
    assert OHLCV_1H_TIMEOUT_SEC == 120


def test_alarm_handler_raises_month_timeout_error():
    """_alarm_handler raises MonthTimeoutError."""
    with pytest.raises(MonthTimeoutError):
        _alarm_handler(signal.SIGALRM, None)


def test_build_months_range():
    """build_months returns correct (start, end) pairs for a year range."""
    months = build_months(2024, 2024)
    assert len(months) == 12
    starts = [s for s, _ in months]
    assert starts[0].month == 1 and starts[0].year == 2024
    assert starts[-1].month == 12 and starts[-1].year == 2024


def test_build_month_ranges_two_months():
    """build_month_ranges(2024-01-01, 2024-03-01) → 2 tuples."""
    r = build_month_ranges(date(2024, 1, 1), date(2024, 3, 1))
    assert len(r) == 2
    assert r[0] == (datetime(2024, 1, 1), datetime(2024, 2, 1))
    assert r[1] == (datetime(2024, 2, 1), datetime(2024, 3, 1))


def test_build_month_ranges_empty_when_start_eq_end():
    """build_month_ranges returns [] when start == end (same month boundary)."""
    assert build_month_ranges(date(2024, 1, 15), date(2024, 1, 15)) == []


def test_setup_logging_creates_log_file(tmp_path):
    """setup_logging creates a log file under log_dir and returns a Logger."""
    logger = setup_logging("USDJPY", log_dir=tmp_path)
    assert isinstance(logger, logging.Logger)
    log_files = list(tmp_path.glob("*.log"))
    assert len(log_files) == 1


def test_write_tick_parquet_bq_creates_file_and_returns_row_count(tmp_path):
    """write_tick_parquet_bq creates Parquet and returns row count (D-01, D-03)."""
    df = pd.DataFrame(
        {
            "bidPrice": [100.0, 100.1],
            "askPrice": [100.2, 100.3],
            "bidVolume": [1.0, 1.5],
            "askVolume": [2.0, 2.5],
        },
        index=pd.to_datetime(["2024-01-08T00:00:00Z", "2024-01-08T00:00:01Z"]),
    )
    log = logging.getLogger(__name__)
    n = write_tick_parquet_bq(df, tmp_path, "USDJPY", "2024-01", log)
    assert n == 2
    out_path = tmp_path / "usdjpy_ticks_2024-01.parquet"
    assert out_path.exists()
    # D-03 schema shape
    schema = pq.read_schema(out_path)
    assert [f.name for f in schema] == [
        "timestamp",
        "bidPrice",
        "askPrice",
        "bidVolume",
        "askVolume",
    ]


def test_write_tick_parquet_bq_skip_if_exists(tmp_path):
    """write_tick_parquet_bq skips write and returns existing row count via pq.read_metadata."""
    out = tmp_path / "usdjpy_ticks_2024-01.parquet"
    # Seed a Parquet file with a single row so SKIP branch reads num_rows=1
    seed_df = pd.DataFrame(
        {
            "bidPrice": [100.0],
            "askPrice": [100.2],
            "bidVolume": [1.0],
            "askVolume": [2.0],
        },
        index=pd.to_datetime(["2024-01-08T00:00:00Z"]),
    )
    log = logging.getLogger(__name__)
    write_tick_parquet_bq(seed_df, tmp_path, "USDJPY", "2024-01", log)
    mtime_before = out.stat().st_mtime

    new_df = pd.DataFrame(
        {
            "bidPrice": [200.0],
            "askPrice": [200.2],
            "bidVolume": [5.0],
            "askVolume": [6.0],
        },
        index=pd.to_datetime(["2024-01-08T00:00:05Z"]),
    )
    n = write_tick_parquet_bq(new_df, tmp_path, "USDJPY", "2024-01", log)
    assert out.stat().st_mtime == mtime_before  # SKIP — file not overwritten
    assert n == 1  # existing row count (from seed)


def test_normalize_ohlcv_columns_exact_match():
    """normalize_ohlcv_columns passes through exact-match columns."""
    df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [1.1],
            "low": [0.9],
            "close": [1.05],
            "volume": [1000],
            "extra_col": [99],
        }
    )
    log = logging.getLogger(__name__)
    result = normalize_ohlcv_columns(df, "USDJPY", log)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_fetch_month_returns_int():
    """Placeholder — fetch_month behavior verified by integration tests in Wave 2."""
    pass
