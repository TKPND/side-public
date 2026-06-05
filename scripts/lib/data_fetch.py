# /// script
# dependencies = ["dukascopy-python", "pandas", "pyarrow>=15,<17", "yfinance"]
# ///
"""Shared library for FX/Gold tick & OHLCV fetching.

Consolidated from 7 pre-Phase-96 fetch_*.py scripts (see PATTERNS.md §1).
Output format: Parquet (Phase 97 D-01, D-03, D-04; replaces Phase 96 D-07 CSV lock).

All writers use pyarrow with deterministic options (snappy + version 2.6
+ use_dictionary=False + data_page_size=1048576) per Phase 97 SEAL
(97-SEAL/parquet_schema.json).
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Canonical pair → Dukascopy instrument mapping
# ---------------------------------------------------------------------------
INSTRUMENT_MAP: dict[str, str] = {
    "EURUSD": "EUR/USD",
    "GBPJPY": "GBP/JPY",
    "AUDUSD": "AUD/USD",
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPUSD": "GBP/USD",
    "XAUUSD": "XAU/USD",
    "BTCUSD": "BTC/USD",  # Phase 108: crypto smoke (24/7)
    "ETHUSD": "ETH/USD",  # Phase 109: crypto bulk ingest (24/7)
}

# Per-month timeout (seconds). tick は LZMA decode が重いため長め。
TICK_TIMEOUT_SEC: int = 1200  # 20 min
OHLCV_1H_TIMEOUT_SEC: int = 120  # 2 min


# ---------------------------------------------------------------------------
# Timeout infrastructure (SIGALRM — Linux-only, not available on Windows)
# ---------------------------------------------------------------------------


class MonthTimeoutError(Exception):
    """Raised by SIGALRM handler when dk.fetch exceeds per-month budget."""


def _alarm_handler(signum, frame) -> None:
    """Internal SIGALRM handler. signum=SIGALRM, frame=caller frame (unused)."""
    raise MonthTimeoutError("month fetch exceeded timeout")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging(pair: str, log_dir: Path = Path("data")) -> logging.Logger:
    """Configure file + stderr logger for a given pair. Returns logger instance.

    Idempotent: if the named logger already has handlers, skip setup.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"bq_{pair.lower()}_tick_fetch.log"

    logger = logging.getLogger(f"data_fetch.{pair}")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Month range helpers
# ---------------------------------------------------------------------------


def build_month_ranges(start: date, end: date) -> list[tuple[datetime, datetime]]:
    """Generate (month_start, next_month_start) tuples covering [start, end).

    start is inclusive (normalized to 1st of month, naive datetime at 00:00 UTC).
    end is exclusive: build_month_ranges(date(2024,1,1), date(2024,3,1)) → 2 tuples.
    Returns [] when start >= end at the month boundary.
    """
    result: list[tuple[datetime, datetime]] = []
    cur = datetime(start.year, start.month, 1)
    # Normalize end to first-of-month boundary
    end_dt = datetime(end.year, end.month, 1)

    while cur < end_dt:
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1)
        result.append((cur, nxt))
        cur = nxt

    return result


def build_months(start_year: int, end_year: int) -> list[tuple[datetime, datetime]]:
    """Year-based helper: build (start, end) list covering start_year-01 through end_year-12.

    Kept for backward compatibility with existing scripts.
    """
    result: list[tuple[datetime, datetime]] = []
    for year in range(start_year, end_year + 1):
        for m in range(1, 13):
            start_dt = datetime(year, m, 1)
            if m < 12:
                end_dt = datetime(year, m + 1, 1)
            else:
                end_dt = datetime(year + 1, 1, 1)
            result.append((start_dt, end_dt))
    return result


# ---------------------------------------------------------------------------
# Fetch wrappers
# ---------------------------------------------------------------------------


def fetch_month_dukascopy(
    start: datetime,
    end: datetime,
    instrument: str,
    interval: str,  # "tick" | "1h"
    timeout_sec: int,
    log: logging.Logger,
) -> Optional[pd.DataFrame]:
    """Fetch 1 month from Dukascopy. Returns DataFrame or None on timeout.

    Linux-only (SIGALRM not available on Windows).
    interval="tick" uses dk.INTERVAL_TICK; interval="1h" uses dk.INTERVAL_HOUR_1.
    """
    import dukascopy_python as dk  # lazy import — optional at module level for unit tests

    label = start.strftime("%Y-%m")
    log.info(
        f"[START] dk.fetch {instrument} {label}: {start.isoformat()} -> {end.isoformat()}"
    )
    t0 = time.time()

    if interval == "tick":
        dk_interval = dk.INTERVAL_TICK
    elif interval == "1h":
        dk_interval = dk.INTERVAL_HOUR_1
    else:
        raise ValueError(
            f"Unsupported interval: {interval!r}. Expected 'tick' or '1h'."
        )

    signal.signal(
        signal.SIGALRM, _alarm_handler
    )  # Linux-only (SIGALRM not available on Windows)
    signal.alarm(timeout_sec)

    try:
        df = dk.fetch(
            instrument=instrument,
            interval=dk_interval,
            offer_side=dk.OFFER_SIDE_ASK,
            start=start,
            end=end,
        )
    except MonthTimeoutError:
        log.error(f"[TIMEOUT] {instrument} {label}: exceeded {timeout_sec}s, skipping")
        return None
    finally:
        signal.alarm(0)

    elapsed = time.time() - t0
    rows = len(df)
    log.info(f"[FETCHED] {instrument} {label}: {rows:,} rows in {elapsed:.1f}s")

    if rows == 0:
        log.warning(f"[EMPTY] {instrument} {label}: no data returned")
        return None

    return df


def fetch_month_yfinance(
    start: datetime,
    end: datetime,
    pair: str,
    interval: str,  # "tick" | "1h"  (tick は NotImplemented)
    log: logging.Logger,
) -> Optional[pd.DataFrame]:
    """Fetch 1 month from yfinance. tick interval は NotImplementedError。

    Uses yfinance.download(pair + '=X', ...) for FX pairs.
    Returns normalized DataFrame with columns [open, high, low, close, volume] or None.
    """
    if interval == "tick":
        raise NotImplementedError("yfinance does not provide tick data")

    import yfinance as yf  # lazy import — optional dependency

    label = start.strftime("%Y-%m")
    log.info(
        f"[START] yfinance {pair} {label}: {start.isoformat()} -> {end.isoformat()}"
    )
    t0 = time.time()

    ticker = pair if pair.endswith("=X") else pair + "=X"

    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval="1h",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        log.error(f"[ERROR] yfinance {pair} {label}: {e}")
        return None

    elapsed = time.time() - t0
    rows = len(df)
    log.info(f"[FETCHED] yfinance {pair} {label}: {rows} bars in {elapsed:.1f}s")

    if rows == 0:
        log.warning(f"[EMPTY] yfinance {pair} {label}: no data returned")
        return None

    df = normalize_ohlcv_columns(df, pair, log)
    return df


# ---------------------------------------------------------------------------
# Parquet writers (Phase 97 D-01/D-03/D-04, deterministic sha256-stable)
# ---------------------------------------------------------------------------

_PARQUET_OPTS = dict(
    compression="snappy",
    use_dictionary=False,
    data_page_size=1048576,
    version="2.6",
)

_TICK_SCHEMA = pa.schema(
    [
        pa.field("timestamp", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("bidPrice", pa.float64(), nullable=False),
        pa.field("askPrice", pa.float64(), nullable=False),
        pa.field("bidVolume", pa.float64(), nullable=False),
        pa.field("askVolume", pa.float64(), nullable=False),
    ]
)

_OHLCV_1H_SCHEMA = pa.schema(
    [
        pa.field("datetime", pa.timestamp("ms", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=False),
        pa.field("high", pa.float64(), nullable=False),
        pa.field("low", pa.float64(), nullable=False),
        pa.field("close", pa.float64(), nullable=False),
        pa.field("volume", pa.float64(), nullable=False),
    ]
)


def write_tick_parquet_bq(
    df: pd.DataFrame,
    out_dir: Path,
    pair: str,
    month_label: str,  # "YYYY-MM"
    log: logging.Logger,
) -> int:
    """Write BQ-compatible tick Parquet. SKIP-if-exists. Returns row count.

    Output file: {out_dir}/{pair.lower()}_ticks_{month_label}.parquet
    Schema: D-03 (timestamp:TIMESTAMP(ms,UTC) + bidPrice/askPrice/bidVolume/askVolume:FLOAT64).
    Writer options: _PARQUET_OPTS (deterministic, sha256-stable per SEAL/parquet_schema.json).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{pair.lower()}_ticks_{month_label}.parquet"

    if out_path.exists():
        existing = pq.read_metadata(out_path).num_rows
        log.info(f"[SKIP] {pair} {month_label} already exists ({existing} rows)")
        return existing

    # Normalize: timestamp column (dukascopy DataFrame は DatetimeIndex 前提だが、
    # reference Parquet から reload した場合 timestamp は既に column)
    df = df.copy()
    if "timestamp" not in df.columns:
        df.index.name = "timestamp"
        df = df.reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df[["timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume"]]

    table = pa.Table.from_pandas(df, schema=_TICK_SCHEMA, preserve_index=False)
    pq.write_table(table, str(out_path), **_PARQUET_OPTS)

    size_mb = out_path.stat().st_size / 1024 / 1024
    log.info(f"[SAVED] {pair} {month_label}: {out_path} ({size_mb:.1f} MB)")
    return len(df)


def write_ohlcv_parquet_bq(
    df: pd.DataFrame,
    out_path: Path,
    pair: str,
    log: logging.Logger,
) -> None:
    """Write 1h OHLCV Parquet with datetime TIMESTAMP(ms,UTC) column.

    Single file (no sharding). D-04: legacy INT64 epoch nanoseconds column は廃棄、
    schema は datetime:TIMESTAMP(ms,UTC) + OHLCV FLOAT64 x5 に統一。
    """
    df = df.copy()
    # df.index は DatetimeIndex (yfinance / dukascopy 1h output)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "datetime"
    df = df.reset_index()
    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("datetime").reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=_OHLCV_1H_SCHEMA, preserve_index=False)
    pq.write_table(table, str(out_path), **_PARQUET_OPTS)

    rows = len(df)
    size_mb = out_path.stat().st_size / 1024 / 1024
    log.info(f"[SAVED] {pair}: {out_path} ({rows:,} rows, {size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Column normalization
# ---------------------------------------------------------------------------


def normalize_ohlcv_columns(
    df: pd.DataFrame, pair: str, log: logging.Logger
) -> pd.DataFrame:
    """Normalize dukascopy OHLCV columns to canonical {open,high,low,close,volume}.

    Handles case-insensitive exact matches and suffix patterns (bid_open → open, etc.).
    Raises ValueError if required columns cannot be resolved.
    """
    cols = list(df.columns)
    log.info(f"[COLUMNS] raw columns from source: {cols}")

    rename_map: dict[str, str] = {}
    col_lower = {c.lower(): c for c in cols}

    for target in ["open", "high", "low", "close", "volume"]:
        # Exact match (case-insensitive)
        if target in col_lower:
            if col_lower[target] != target:
                rename_map[col_lower[target]] = target
        else:
            # Suffix match: bid_open → open, ask_close → close, etc.
            for c in cols:
                if c.lower().endswith(target):
                    rename_map[c] = target
                    break

    if rename_map:
        log.info(f"[RENAME] {rename_map}")
        df = df.rename(columns=rename_map)

    # Ensure only required columns remain
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"[{pair}] Missing columns after normalization: {missing}. "
            f"Available: {list(df.columns)}"
        )

    return df[required]
