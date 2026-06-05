# /// script
# dependencies = ["dukascopy-python", "pandas", "pyarrow>=15,<17", "yfinance"]
# ///
"""Unified CLI for Dukascopy/yfinance tick & OHLCV fetch.

Consolidates 7 pre-Phase-96 fetch_*.py scripts into a single entrypoint.
Output format: Parquet (Phase 97 D-01; replaces Phase 96 D-07 CSV lock).

All writes go through scripts.lib.data_fetch.write_{tick,ohlcv}_parquet_bq
(pyarrow + snappy + version 2.6, sha256-stable per 97-SEAL/parquet_schema.json).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import data_fetch  # noqa: E402


# ---------------------------------------------------------------------------
# Argument parser (D-01 CLI contract)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fetch_ticks",
        description="Unified FX/Gold tick & OHLCV fetcher (Parquet output, Phase 97 D-01).",
    )
    p.add_argument(
        "--pair",
        required=True,
        choices=list(data_fetch.INSTRUMENT_MAP.keys()),
        help="Currency pair: EURUSD/GBPJPY/AUDUSD/USDJPY/EURJPY/GBPUSD/XAUUSD/BTCUSD/ETHUSD",
    )
    p.add_argument(
        "--start",
        required=True,
        type=date.fromisoformat,
        help="Start date YYYY-MM-DD (inclusive, normalized to month boundary internally)",
    )
    p.add_argument(
        "--end",
        required=True,
        type=date.fromisoformat,
        help="End date YYYY-MM-DD (exclusive at month boundary)",
    )
    p.add_argument(
        "--source",
        required=True,
        choices=["dukascopy", "yfinance"],
        help="Data source",
    )
    p.add_argument(
        "--interval",
        required=True,
        choices=["tick", "1h"],
        help="Interval: tick or 1h OHLCV",
    )
    p.add_argument(
        "--for-bq",
        action="store_true",
        help=(
            "BQ-compatible schema: tick = timestamp column, "
            "1h = datetime column (both TIMESTAMP(ms, UTC), per Phase 97 SEAL)."
        ),
    )
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="tick: output directory (monthly .parquet written here); 1h: output file path (.parquet)",
    )
    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate date ordering (argparse type= only validates format, not semantics)
    if args.end <= args.start:
        parser.error(f"--end ({args.end}) must be after --start ({args.start})")

    log = data_fetch.setup_logging(args.pair)

    log.info("=" * 60)
    log.info(
        f"fetch_ticks: pair={args.pair} source={args.source} interval={args.interval}"
    )
    log.info(f"range: {args.start} -> {args.end}")
    log.info(f"out: {args.out}")
    log.info("=" * 60)

    months = data_fetch.build_month_ranges(args.start, args.end)
    if not months:
        log.warning("empty date range — nothing to fetch")
        return 0

    if args.interval == "tick":
        return _dispatch_tick(args, months, log)
    else:  # 1h
        return _dispatch_1h(args, months, log)


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def _dispatch_tick(args, months, log) -> int:
    """Fetch tick data month-by-month, write one Parquet per month into --out dir."""
    args.out.mkdir(parents=True, exist_ok=True)
    instrument = data_fetch.INSTRUMENT_MAP[args.pair]
    total = 0

    for m_start, m_end in months:
        label = m_start.strftime("%Y-%m")
        if args.source == "dukascopy":
            df = data_fetch.fetch_month_dukascopy(
                m_start,
                m_end,
                instrument,
                "tick",
                data_fetch.TICK_TIMEOUT_SEC,
                log,
            )
        else:
            log.error("yfinance does not support tick interval")
            return 2

        if df is None or len(df) == 0:
            log.warning(f"[EMPTY] {args.pair} {label}")
            continue

        n = data_fetch.write_tick_parquet_bq(df, args.out, args.pair, label, log)
        total += n

    log.info(f"DONE: {total:,} total {args.pair} ticks across {len(months)} month(s)")
    return 0


def _dispatch_1h(args, months, log) -> int:
    """Fetch 1h OHLCV month-by-month, concat, write single Parquet to --out file."""
    args.out.parent.mkdir(parents=True, exist_ok=True)
    instrument = data_fetch.INSTRUMENT_MAP[args.pair]
    frames = []

    for m_start, m_end in months:
        if args.source == "dukascopy":
            df = data_fetch.fetch_month_dukascopy(
                m_start,
                m_end,
                instrument,
                "1h",
                data_fetch.OHLCV_1H_TIMEOUT_SEC,
                log,
            )
        else:
            df = data_fetch.fetch_month_yfinance(m_start, m_end, args.pair, "1h", log)

        if df is None or len(df) == 0:
            continue
        frames.append(df)

    if not frames:
        log.warning("[EMPTY] no 1h data fetched")
        return 0

    full = pd.concat(frames).sort_index()
    full = full[~full.index.duplicated(keep="first")]
    full = data_fetch.normalize_ohlcv_columns(full, args.pair, log)
    data_fetch.write_ohlcv_parquet_bq(full, args.out, args.pair, log)
    log.info(f"DONE: {len(full):,} 1h bars -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
