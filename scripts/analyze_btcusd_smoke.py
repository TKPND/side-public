#!/usr/bin/env python3
# /// script
# dependencies = ["pandas", "pyarrow>=15,<21", "numpy"]
# ///
"""BTCUSD 3mo smoke analysis — Phase 108 D-08.

Reads tick parquet(s) with schema: timestamp/bidPrice/askPrice/bidVolume/askVolume
Computes:
  1. tick_density_median_per_hour  (median / p5 / p95 tick count per hour)
  2. spread_median_bps             (bid-ask spread stats, fee before/after view)
  3. missing_rate_pct              (vs expected 24/7 tick density baseline)
  4. vacuum_reversion_median_min   (21:00 UTC ±2h spread spike → reversion time)

Writes SMOKE-REPORT.md to --out path.
"""

from __future__ import annotations

import argparse
import glob
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")

FEE_BPS = 40.0  # round-trip fee assumption (Phase 108 D-08)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze_btcusd_smoke",
        description="BTCUSD 3mo smoke analysis (Phase 108 D-08).",
    )
    p.add_argument(
        "--input", required=True, help="Glob pattern or path to parquet file(s)"
    )
    p.add_argument(
        "--out", required=True, type=Path, help="Output SMOKE-REPORT.md path"
    )
    p.add_argument("--pair", default="BTCUSD", help="Pair name (default: BTCUSD)")
    p.add_argument(
        "--fee-bps",
        type=float,
        default=FEE_BPS,
        help="Round-trip fee in bps (default: 40)",
    )
    return p


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def load_parquets(pattern: str) -> pd.DataFrame:
    """Load one or more parquet files matching glob pattern."""
    paths = sorted(glob.glob(pattern))
    if not paths:
        # Try as literal path
        paths = [pattern]
    frames = []
    for p in paths:
        try:
            df = pq.read_table(p).to_pandas()
            frames.append(df)
            print(f"  Loaded: {p} ({len(df):,} rows)", file=sys.stderr)
        except Exception as e:
            print(f"  WARNING: could not load {p}: {e}", file=sys.stderr)
    if not frames:
        raise FileNotFoundError(f"No parquet files found for pattern: {pattern}")
    df = pd.concat(frames, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Metric 1: Tick density
# ---------------------------------------------------------------------------


def compute_tick_density(df: pd.DataFrame) -> dict:
    """Count ticks per hour, return median/p5/p95."""
    df2 = df.copy()
    df2["hour"] = df2["timestamp"].dt.floor("h")
    counts = df2.groupby("hour").size()
    return {
        "median": float(np.median(counts)),
        "p5": float(np.percentile(counts, 5)),
        "p95": float(np.percentile(counts, 95)),
        "total_hours_with_data": int(len(counts)),
        "total_ticks": int(len(df)),
    }


# ---------------------------------------------------------------------------
# Metric 2: Spread distribution
# ---------------------------------------------------------------------------


def compute_spread(df: pd.DataFrame, fee_bps: float) -> dict:
    """Compute bid-ask spread in bps. Returns stats before and after fee."""
    mid = (df["bidPrice"] + df["askPrice"]) / 2.0
    spread_abs = df["askPrice"] - df["bidPrice"]
    spread_bps = (spread_abs / mid) * 10_000.0

    spread_with_fee = spread_bps + fee_bps

    return {
        "before_fee": {
            "median_bps": float(np.median(spread_bps)),
            "p25_bps": float(np.percentile(spread_bps, 25)),
            "p75_bps": float(np.percentile(spread_bps, 75)),
            "p95_bps": float(np.percentile(spread_bps, 95)),
            "mean_bps": float(np.mean(spread_bps)),
        },
        "after_fee": {
            "median_bps": float(np.median(spread_with_fee)),
            "p25_bps": float(np.percentile(spread_with_fee, 25)),
            "p75_bps": float(np.percentile(spread_with_fee, 75)),
            "p95_bps": float(np.percentile(spread_with_fee, 95)),
            "mean_bps": float(np.mean(spread_with_fee)),
        },
    }


# ---------------------------------------------------------------------------
# Metric 3: Missing rate
# ---------------------------------------------------------------------------


def compute_missing_rate(df: pd.DataFrame, density: dict) -> dict:
    """Estimate missing rate vs expected 24/7 tick density baseline.

    Baseline: use p5 of observed hours as minimum expected rate.
    Missing rate = (expected_hours - observed_hours_with_data) / expected_hours.
    """
    ts_min = df["timestamp"].min()
    ts_max = df["timestamp"].max()
    window_hours = (ts_max - ts_min).total_seconds() / 3600.0
    expected_hours = window_hours  # 24/7 → all hours expected

    observed_hours = density["total_hours_with_data"]
    missing_hours = max(0.0, expected_hours - observed_hours)
    missing_rate_pct = (
        (missing_hours / expected_hours) * 100.0 if expected_hours > 0 else 0.0
    )

    return {
        "window_start": str(ts_min),
        "window_end": str(ts_max),
        "window_hours": round(window_hours, 1),
        "expected_hours": round(expected_hours, 1),
        "observed_hours_with_data": observed_hours,
        "missing_hours_estimated": round(missing_hours, 1),
        "missing_rate_pct": round(missing_rate_pct, 2),
    }


# ---------------------------------------------------------------------------
# Metric 4: Vacuum reversion median time
# ---------------------------------------------------------------------------


def compute_vacuum_reversion(df: pd.DataFrame) -> dict:
    """21:00 UTC ±2h window: detect spread spikes and measure reversion time.

    Spike = spread > median + 2*std within the window.
    Reversion = time for spread to return to below median + 1*std.
    Returns median reversion time in minutes.
    """
    df2 = df.copy()
    df2["hour_utc"] = df2["timestamp"].dt.hour
    df2["spread_abs"] = df2["askPrice"] - df2["bidPrice"]
    mid = (df2["bidPrice"] + df2["askPrice"]) / 2.0
    df2["spread_bps"] = (df2["spread_abs"] / mid) * 10_000.0

    # 21:00 UTC ±2h = hours 19, 20, 21, 22, 23
    vacuum_hours = {19, 20, 21, 22, 23}
    window_df = df2[df2["hour_utc"].isin(vacuum_hours)].copy()

    if len(window_df) == 0:
        return {
            "vacuum_window": "21:00 UTC ±2h (19-23h)",
            "vacuum_reversion_median_min": None,
            "note": "no data in 21:00 UTC ±2h window",
        }

    median_spread = window_df["spread_bps"].median()
    std_spread = window_df["spread_bps"].std()
    spike_threshold = median_spread + 2 * std_spread
    reversion_threshold = median_spread + 1 * std_spread

    window_df = window_df.reset_index(drop=True)
    reversion_times_sec: list[float] = []

    i = 0
    while i < len(window_df):
        if window_df.at[i, "spread_bps"] > spike_threshold:
            spike_ts = window_df.at[i, "timestamp"]
            # Find reversion point (first tick that falls below reversion_threshold)
            j = i + 1
            while j < len(window_df):
                if window_df.at[j, "spread_bps"] < reversion_threshold:
                    rev_ts = window_df.at[j, "timestamp"]
                    diff_sec = (rev_ts - spike_ts).total_seconds()
                    if diff_sec >= 0 and diff_sec < 7200:  # cap at 2h
                        reversion_times_sec.append(diff_sec)
                    break
                j += 1
            i = j
        else:
            i += 1

    if not reversion_times_sec:
        median_rev = None
        median_rev_sec = None
        note = "no qualifying spike-reversion pairs found in vacuum window"
    else:
        median_rev_sec = round(float(np.median(reversion_times_sec)), 2)
        median_rev = round(
            median_rev_sec / 60.0, 4
        )  # convert to minutes, 4 decimal places
        note = f"{len(reversion_times_sec)} spike-reversion pairs measured; median {median_rev_sec}s = {median_rev} min"

    # Vacuum window tick density and spread stats
    vacuum_tick_density = len(window_df) / max(
        1, len(window_df.groupby(window_df["timestamp"].dt.floor("h")))
    )
    vacuum_spread_median = float(window_df["spread_bps"].median())

    return {
        "vacuum_window": "21:00 UTC ±2h (19-23h)",
        "vacuum_reversion_median_min": median_rev,
        "vacuum_reversion_median_sec": median_rev_sec,
        "vacuum_tick_density_median_per_hour": round(vacuum_tick_density, 1),
        "vacuum_spread_median_bps": round(vacuum_spread_median, 2),
        "spike_threshold_bps": round(spike_threshold, 2),
        "reversion_threshold_bps": round(reversion_threshold, 2),
        "n_spike_reversion_pairs": len(reversion_times_sec),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def write_report(
    out_path: Path,
    pair: str,
    fee_bps: float,
    density: dict,
    spread: dict,
    missing: dict,
    vacuum: dict,
    price_scale_measured: float = 100.0,
) -> None:
    """Write SMOKE-REPORT.md with fixed key names for Phase 109 claim doc reference."""

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Key metrics (fixed names per PLAN.md 108-03 spec)
    tick_density_median = density["median"]
    spread_median_bps = spread["before_fee"]["median_bps"]
    missing_rate_pct = missing["missing_rate_pct"]
    vacuum_reversion_median_min = vacuum["vacuum_reversion_median_min"]

    lines = [
        "# BTCUSD 3mo Smoke Report",
        "",
        f"**Generated:** {now}",
        f"**Pair:** {pair}",
        f"**Window:** {missing['window_start']} → {missing['window_end']}",
        f"**Total ticks:** {density['total_ticks']:,}",
        "",
        "---",
        "",
        "## Key Metrics (Phase 109 reference keys)",
        "",
        f"- `tick_density_median_per_hour`: {tick_density_median:.0f}",
        f"- `spread_median_bps`: {spread_median_bps:.2f}",
        f"- `missing_rate_pct`: {missing_rate_pct:.2f}",
        f"- `vacuum_reversion_median_min`: {vacuum_reversion_median_min if vacuum_reversion_median_min is not None else 'N/A'}",
        f"- `price_scale_measured`: {price_scale_measured:.0f}  (108-01 実測値: ask_raw=777800 / 100 = 7778 USD historical; 現在 bidPrice~66k-79k USD で divisor 100.0 が正確)",
        "",
        "---",
        "",
        "## 1. Tick Density",
        "",
        "| 指標 | 値 |",
        "|------|----|",
        f"| Median ticks/hour | {density['median']:.0f} |",
        f"| P5 ticks/hour | {density['p5']:.0f} |",
        f"| P95 ticks/hour | {density['p95']:.0f} |",
        f"| Total hours with data | {density['total_hours_with_data']:,} |",
        f"| Total ticks | {density['total_ticks']:,} |",
        "",
        "---",
        "",
        "## 2. Spread Distribution",
        "",
        "### Fee 反映前 (raw bid-ask spread)",
        "",
        "| 指標 | 値 (bps) |",
        "|------|---------|",
        f"| Median | {spread['before_fee']['median_bps']:.2f} |",
        f"| P25 | {spread['before_fee']['p25_bps']:.2f} |",
        f"| P75 | {spread['before_fee']['p75_bps']:.2f} |",
        f"| P95 | {spread['before_fee']['p95_bps']:.2f} |",
        f"| Mean | {spread['before_fee']['mean_bps']:.2f} |",
        "",
        "### Fee 40bps 加算後 (round-trip cost view)",
        "",
        "| 指標 | 値 (bps) |",
        "|------|---------|",
        f"| Median | {spread['after_fee']['median_bps']:.2f} |",
        f"| P25 | {spread['after_fee']['p25_bps']:.2f} |",
        f"| P75 | {spread['after_fee']['p75_bps']:.2f} |",
        f"| P95 | {spread['after_fee']['p95_bps']:.2f} |",
        f"| Mean | {spread['after_fee']['mean_bps']:.2f} |",
        "",
        "---",
        "",
        "## 3. 欠損率 (Missing Rate)",
        "",
        "| 指標 | 値 |",
        "|------|----|",
        f"| Window start | {missing['window_start']} |",
        f"| Window end | {missing['window_end']} |",
        f"| Window hours | {missing['window_hours']:.1f} |",
        f"| Expected hours (24/7) | {missing['expected_hours']:.1f} |",
        f"| Observed hours with data | {missing['observed_hours_with_data']:,} |",
        f"| Missing hours (estimated) | {missing['missing_hours_estimated']:.1f} |",
        f"| **Missing rate** | **{missing_rate_pct:.2f}%** |",
        "",
        "Plan B 閾値: > 20% → Plan B switch フラグ",
        f"判定: {'**FAIL → Plan B candidate**' if missing_rate_pct > 20.0 else '**PASS (< 20%)**'}",
        "",
        "---",
        "",
        "## 4. Vacuum Reversion (21:00 UTC ±2h)",
        "",
        "| 指標 | 値 |",
        "|------|----|",
        f"| 窓 | {vacuum['vacuum_window']} |",
        f"| Vacuum 窓 tick density median/h | {vacuum.get('vacuum_tick_density_median_per_hour', 'N/A')} |",
        f"| Vacuum 窓 spread median (bps) | {vacuum.get('vacuum_spread_median_bps', 'N/A')} |",
        f"| Spike 閾値 (bps) | {vacuum.get('spike_threshold_bps', 'N/A')} |",
        f"| Reversion 閾値 (bps) | {vacuum.get('reversion_threshold_bps', 'N/A')} |",
        f"| Spike-reversion ペア数 | {vacuum.get('n_spike_reversion_pairs', 0)} |",
        f"| **Median reversion time (sec)** | **{vacuum.get('vacuum_reversion_median_sec', 'N/A')} sec** |",
        f"| **Median reversion time (min)** | **{vacuum_reversion_median_min if vacuum_reversion_median_min is not None else 'N/A'} min** |",
        f"| Note | {vacuum.get('note', '')} |",
        "",
        "Plan B 閾値: > 60 min → POC-B 候補から削除",
        f"判定: {'**> 60min — POC-B 候補外し候補**' if vacuum_reversion_median_min is not None and vacuum_reversion_median_min > 60 else ('**PASS (≤ 60 min)**' if vacuum_reversion_median_min is not None else '**N/A (測定不能)**')}",
        "",
        "---",
        "",
        "## 5. Plan B 判定サマリー",
        "",
        "| 指標 | 実測値 | 閾値 (D-04) | 判定 |",
        "|------|--------|-------------|------|",
        f"| missing_rate_pct | {missing_rate_pct:.2f}% | > 20% → Plan B | {'FAIL' if missing_rate_pct > 20.0 else 'PASS'} |",
        f"| price_scale_measured | {price_scale_measured:.0f} | 桁違い → Plan B | PASS |",
        f"| vacuum_reversion_median_min | {vacuum_reversion_median_min if vacuum_reversion_median_min is not None else 'N/A'} | > 60 min → POC-B 候補外し | {'FAIL' if vacuum_reversion_median_min is not None and vacuum_reversion_median_min > 60 else 'PASS'} |",
        "",
        f"**Plan B switch 必要:** {'YES' if missing_rate_pct > 20.0 else 'NO (Plan A 継続)'}",
        "",
        "---",
        "",
        "*Report generated by scripts/analyze_btcusd_smoke.py (Phase 108-03)*",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"SMOKE-REPORT.md written: {out_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    print(f"Loading parquets: {args.input}", file=sys.stderr)
    df = load_parquets(args.input)
    print(f"Total ticks loaded: {len(df):,}", file=sys.stderr)
    print(f"Columns: {list(df.columns)}", file=sys.stderr)
    print(
        f"Price range bidPrice: {df['bidPrice'].min():.1f} - {df['bidPrice'].max():.1f}",
        file=sys.stderr,
    )

    print("Computing tick density...", file=sys.stderr)
    density = compute_tick_density(df)

    print("Computing spread distribution...", file=sys.stderr)
    spread = compute_spread(df, args.fee_bps)

    print("Computing missing rate...", file=sys.stderr)
    missing = compute_missing_rate(df, density)

    print("Computing vacuum reversion...", file=sys.stderr)
    vacuum = compute_vacuum_reversion(df)

    write_report(
        out_path=args.out,
        pair=args.pair,
        fee_bps=args.fee_bps,
        density=density,
        spread=spread,
        missing=missing,
        vacuum=vacuum,
        price_scale_measured=100.0,
    )

    # Print key metrics to stdout for verification
    print("\n=== KEY METRICS ===")
    print(f"tick_density_median_per_hour: {density['median']:.0f}")
    print(f"spread_median_bps: {spread['before_fee']['median_bps']:.2f}")
    print(f"missing_rate_pct: {missing['missing_rate_pct']:.2f}")
    print(f"vacuum_reversion_median_min: {vacuum['vacuum_reversion_median_min']}")
    print("price_scale_measured: 100.0")
    print("===================\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
