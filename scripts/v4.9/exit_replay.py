"""Exit rule replay: first-to-fire logic for ATR / technical / trailing / time stops.

Phase 86 EXIT-02 implementation. Consumes per_trade_log.parquet (Rust engine
additive field), replays all 4 exit rules in SEAL-03 priority order per trade,
and writes exit_replayed.parquet with custom_metadata provenance stamp (D-16).

References:
  - SEAL exit_commit.json (read-only, immutable for v4.9)
  - .planning/phases/86-.../86-CONTEXT.md D-05..D-19
  - .planning/phases/86-.../86-PATTERNS.md § exit_replay.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

import polars as pl

# SEAL-derived constants (must match .planning/phases/85-.../85-SEAL/exit_commit.json)
EXIT_PRIORITY = ["atr_stop", "technical_stop", "trailing_stop", "time_stop"]
SIZING_EXIT_COMMIT = "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab"
ATR_K_PRIMARY = 2.0  # SEAL exit_commit.json
ATR_WINDOW_BARS = 14  # SEAL exit_commit.json
TECHNICAL_STOP_LOOKBACK = 3  # D-19


def compute_technical_stop(
    bars: list[dict],
    current_idx: int,
    direction: int,
    lookback: int = TECHNICAL_STOP_LOOKBACK,
) -> float | None:
    """Swing-based technical stop (D-19).

    Long: min(low) over the most recent `lookback` bars (excluding current).
    Short: max(high) over the most recent `lookback` bars.
    Returns None when `current_idx < lookback` (stop not yet activated).
    """
    if current_idx < lookback:
        return None
    window = bars[max(0, current_idx - lookback) : current_idx]
    if direction == 1:
        return min(b["low"] for b in window)
    return max(b["high"] for b in window)


def replay_trade(
    trade: dict,
    atr_k: float = ATR_K_PRIMARY,
    max_hold_bars: int | None = None,
    tech_lookback: int = TECHNICAL_STOP_LOOKBACK,
) -> dict[str, Any]:
    """First-to-fire exit replay for a single trade.

    Priority order (SEAL-03): atr_stop -> technical_stop -> trailing_stop -> time_stop.
    Returns {trade_id, exit_rule, exit_bar, pnl}.
    exit_rule is one of {atr_stop, technical_stop, trailing_stop, time_stop, fold_end} (D-17 sentinel).
    """
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    atr_at_entry = trade["atr_at_entry"]

    # ATR stop level (fixed at entry, D-06)
    atr_stop_level = entry_price - direction * atr_k * atr_at_entry

    # Chandelier-style trailing tracker (highest/lowest CLOSE — Pattern 4)
    trailing_high = entry_price
    trailing_low = entry_price

    for i, bar in enumerate(trade["bars"]):
        fired: dict[str, int] = {}

        # 1. atr_stop
        if direction == 1 and bar["low"] <= atr_stop_level:
            fired["atr_stop"] = i
        elif direction == -1 and bar["high"] >= atr_stop_level:
            fired["atr_stop"] = i

        # 2. technical_stop (D-19 lookback=3, swing-based)
        tech_level = compute_technical_stop(trade["bars"], i, direction, tech_lookback)
        if tech_level is not None:
            if direction == 1 and bar["low"] <= tech_level:
                fired["technical_stop"] = i
            elif direction == -1 and bar["high"] >= tech_level:
                fired["technical_stop"] = i

        # 3. trailing_stop (D-18: k = atr_k_primary = 2.0)
        trailing_high = max(trailing_high, bar["close"])
        trailing_low = min(trailing_low, bar["close"])
        if direction == 1:
            trail_level = trailing_high - atr_k * bar["atr"]
            if bar["low"] <= trail_level:
                fired["trailing_stop"] = i
        else:
            trail_level = trailing_low + atr_k * bar["atr"]
            if bar["high"] >= trail_level:
                fired["trailing_stop"] = i

        # 4. time_stop (D-07: no-op if max_hold_bars is None)
        if max_hold_bars is not None and i >= max_hold_bars - 1:
            fired["time_stop"] = i

        # first-to-fire: priority order tie-breaker within same bar
        for rule in EXIT_PRIORITY:
            if rule in fired:
                exit_bar_offset = fired[rule]
                # Exit at the bar's close (simple convention; intra-bar pricing
                # unknown because BarSnapshot has no open field per D-01)
                exit_price = bar["close"]
                pnl = direction * (exit_price - entry_price)
                return {
                    "trade_id": trade["trade_id"],
                    "exit_rule": rule,
                    "exit_bar": trade["entry_bar"] + exit_bar_offset,
                    "pnl": pnl,
                }

    # D-17: fold_end sentinel (bars exhausted, max_hold_bars=null => no time_stop)
    last_bar = trade["bars"][-1]
    pnl = direction * (last_bar["close"] - entry_price)
    return {
        "trade_id": trade["trade_id"],
        "exit_rule": "fold_end",
        "exit_bar": trade["entry_bar"] + len(trade["bars"]) - 1,
        "pnl": pnl,
    }


def replay_all_trades(trades: list[dict], fold_id: int, **kw) -> list[dict]:
    """Apply replay_trade to each trade; attach fold_id."""
    out = []
    for t in trades:
        r = replay_trade(t, **kw)
        r["fold"] = fold_id
        out.append(r)
    return out


def write_exit_replayed_parquet(rows: list[dict], output_path: pathlib.Path) -> None:
    """Write exit_replayed.parquet with D-16 custom_metadata provenance stamp.

    Note: polars 1.40 write_parquet(metadata=dict) embeds key/value pairs into
    parquet file-level key-value metadata. Read back via pl.read_parquet_metadata()
    which returns a plain dict in polars 1.40+ (not an object with .custom_metadata).
    """
    df = pl.DataFrame(
        rows,
        schema={
            "cell_id": pl.Utf8,
            "fold": pl.Int64,
            "trade_id": pl.UInt64,
            "exit_rule": pl.Utf8,
            "exit_bar": pl.Int64,
            "pnl": pl.Float64,
        },
    )
    df.write_parquet(
        output_path,
        metadata={
            "sizing_exit_commit": SIZING_EXIT_COMMIT,
            "atr_k_primary": str(ATR_K_PRIMARY),
            "atr_window_bars": str(ATR_WINDOW_BARS),
            "exit_priority_order": ",".join(EXIT_PRIORITY),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay exit rules (first-to-fire).")
    parser.add_argument(
        "--input", required=True, type=pathlib.Path, help="per_trade_log.parquet path"
    )
    parser.add_argument(
        "--output", required=True, type=pathlib.Path, help="exit_replayed.parquet path"
    )
    parser.add_argument("--atr-k", type=float, default=ATR_K_PRIMARY)
    parser.add_argument("--max-hold-bars", type=int, default=None)
    parser.add_argument(
        "--fragility",
        action="store_true",
        help="Run EXIT-03 fragility grid and write atr_fragility_report.json",
    )
    parser.add_argument(
        "--fragility-output",
        type=pathlib.Path,
        default=pathlib.Path("data/v4.9/atr_fragility_report.json"),
    )
    args = parser.parse_args(argv)

    # Load per_trade_log.parquet (bars column holds nested struct list)
    log_df = pl.read_parquet(args.input)

    if args.fragility:
        trades = []
        for row in log_df.iter_rows(named=True):
            trades.append(
                {
                    "cell_id": row.get("cell_id", ""),
                    "trade_id": row["trade_id"],
                    "entry_bar": row["entry_bar"],
                    "entry_price": row["entry_price"],
                    "direction": row["direction"],
                    "atr_at_entry": row["atr_at_entry"],
                    "bars": row["bars"],
                }
            )
        results = run_fragility_grid(
            trades, atr_k=args.atr_k, max_hold_bars=args.max_hold_bars
        )
        write_atr_fragility_report(results, args.fragility_output)
        print(f"Wrote atr_fragility_report to {args.fragility_output}")
        return 0

    rows: list[dict] = []
    for row in log_df.iter_rows(named=True):
        fold_id = row.get("fold", 0)
        cell_id = row.get("cell_id", "")
        trade = {
            "trade_id": row["trade_id"],
            "entry_bar": row["entry_bar"],
            "entry_price": row["entry_price"],
            "direction": row["direction"],
            "atr_at_entry": row["atr_at_entry"],
            "bars": row["bars"],  # list[dict] from nested parquet struct
        }
        result = replay_trade(trade, atr_k=args.atr_k, max_hold_bars=args.max_hold_bars)
        result["fold"] = fold_id
        result["cell_id"] = cell_id
        rows.append(result)

    write_exit_replayed_parquet(rows, args.output)
    print(f"Wrote {len(rows)} trade exits to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ------------------------------------------------------------------
# Phase 86 EXIT-03: ATR compliance + fragility grid
# ------------------------------------------------------------------

FRAGILITY_WINDOWS = [7, 14, 21]  # ROADMAP Phase 86 EXIT-03
FRAGILITY_K_GRID = [1.5, 2.0, 2.5, 3.0]  # SEAL exit_commit.json atr_k_fragility_grid


class AtrComplianceError(AssertionError):
    """Raised when atr_window_bars deviates from SEAL value 14."""


def assert_atr_window_compliance(atr_window_bars: int) -> None:
    """EXIT-03: Production runs must use atr_window_bars == 14 (SEAL-03).

    Fragility grid runs call compute_atr_python with custom windows,
    but exit_replay.py as a primary pipeline entry refuses to run
    with a non-sealed window.
    """
    if atr_window_bars != ATR_WINDOW_BARS:  # ATR_WINDOW_BARS == 14 (Plan 03)
        raise AtrComplianceError(
            f"atr_window_bars={atr_window_bars} violates SEAL-03 "
            f"(atr_window_bars=14). fragility grid MUST NOT overwrite "
            f"the primary production value."
        )


def compute_atr_python(bars: list[dict], window: int) -> list[float]:
    """Wilder ATR re-computation in Python (D-03 / D-20).

    Used for EXIT-03 fragility grid when window != 14 (BarSnapshot.atr
    is Rust-computed at window=14). When window==14 the result must
    match BarSnapshot.atr within 1e-6 as a sanity cross-check.

    TR_i = max(high_i - low_i,
               |high_i - close_{i-1}|,
               |low_i  - close_{i-1}|)
    ATR_window = mean(TR_1..TR_window)       # seed
    ATR_i = (ATR_{i-1} * (window - 1) + TR_i) / window   # Wilder EMA
    """
    if len(bars) == 0:
        return []
    trs: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            tr = b["high"] - b["low"]  # TR_1: only high-low available
        else:
            prev_close = bars[i - 1]["close"]
            tr = max(
                b["high"] - b["low"],
                abs(b["high"] - prev_close),
                abs(b["low"] - prev_close),
            )
        trs.append(tr)

    atrs: list[float] = [float("nan")] * len(bars)
    if len(bars) < window:
        return atrs
    seed = sum(trs[:window]) / window
    atrs[window - 1] = seed
    for i in range(window, len(bars)):
        atrs[i] = (atrs[i - 1] * (window - 1) + trs[i]) / window
    return atrs


def _canonical_json_bytes(obj: dict) -> bytes:
    """D-15 canonicalization protocol (same as compute_seal_hash.py)."""
    import json as _json

    return _json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _aggregate_replay_results(replayed_rows: list[dict]) -> dict:
    """Compute n_trades / pf / mean_pnl / exit_rule_dist from replay output."""
    n = len(replayed_rows)
    if n == 0:
        return {"n_trades": 0, "pf": None, "mean_pnl": None, "exit_rule_dist": {}}
    gains = sum(r["pnl"] for r in replayed_rows if r["pnl"] > 0)
    losses = -sum(r["pnl"] for r in replayed_rows if r["pnl"] < 0)
    pf = (gains / losses) if losses > 0 else None
    mean_pnl = sum(r["pnl"] for r in replayed_rows) / n

    dist: dict[str, int] = {}
    for r in replayed_rows:
        dist[r["exit_rule"]] = dist.get(r["exit_rule"], 0) + 1
    # Normalize to fraction
    exit_rule_dist = {k: v / n for k, v in dist.items()}
    return {
        "n_trades": n,
        "pf": pf,
        "mean_pnl": mean_pnl,
        "exit_rule_dist": exit_rule_dist,
    }


def run_fragility_grid(
    trades: list[dict],
    atr_windows: list[int] | None = None,
    atr_k: float = ATR_K_PRIMARY,
    max_hold_bars: int | None = None,
) -> dict[str, dict]:
    """EXIT-03: Replay all trades with each atr_window in FRAGILITY_WINDOWS.

    For atr_window != 14, bar['atr'] is replaced with compute_atr_python(bars, window)
    before replay. For atr_window == 14, the Rust-computed BarSnapshot.atr is used
    as-is (sanity path; cross-check test covers equality).

    Returns dict keyed by "atr_window={N}" -> aggregated metrics.
    """
    import math as _math

    if atr_windows is None:
        atr_windows = list(FRAGILITY_WINDOWS)

    results: dict[str, dict] = {}
    for window in atr_windows:
        per_window_rows: list[dict] = []
        for trade in trades:
            if window == ATR_WINDOW_BARS:
                trade_for_replay = trade  # Use Rust-computed ATR (no re-compute)
            else:
                # Re-compute ATR in Python and override bar['atr']
                recomputed_atr = compute_atr_python(trade["bars"], window)
                new_bars = []
                for i, b in enumerate(trade["bars"]):
                    nb = dict(b)
                    # NaN fallback: keep Rust atr when ATR not yet seeded
                    if not _math.isnan(recomputed_atr[i]):
                        nb["atr"] = recomputed_atr[i]
                    new_bars.append(nb)
                trade_for_replay = {**trade, "bars": new_bars}
            result = replay_trade(
                trade_for_replay, atr_k=atr_k, max_hold_bars=max_hold_bars
            )
            per_window_rows.append(result)
        results[f"atr_window={window}"] = _aggregate_replay_results(per_window_rows)
    return results


def write_atr_fragility_report(
    results: dict[str, dict],
    output_path: pathlib.Path,
    canonical: bool = True,
) -> None:
    """EXIT-03 D-14 / D-15: emit atr_fragility_report.json with provenance stamp."""
    import json as _json

    report = {
        "sizing_exit_commit": SIZING_EXIT_COMMIT,
        "atr_window_primary": ATR_WINDOW_BARS,
        "atr_k_primary": ATR_K_PRIMARY,
        "fragility_grid": list(FRAGILITY_WINDOWS),
        "atr_k_fragility_grid": list(FRAGILITY_K_GRID),
        "results": results,
        "compliance_check": {
            "primary_window_in_grid": ATR_WINDOW_BARS in FRAGILITY_WINDOWS,
            "atr_window_bars_sealed": ATR_WINDOW_BARS,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if canonical:
        output_path.write_bytes(_canonical_json_bytes(report))
    else:
        output_path.write_text(_json.dumps(report, indent=2))
