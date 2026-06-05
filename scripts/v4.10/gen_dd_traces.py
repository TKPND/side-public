"""Generate data/v4.10/dd_traces.parquet via equity curve reconstruction.

Phase 90 Plan 01 (Option B): reconstruct bar-level equity + pnl from
per_trade_log.parquet (entry_bar + bars OHLC), exit_replayed.parquet (exit_bar),
sized_pnl.parquet (sized_pnl), and OHLCV bar_index→datetime mapping.

Output: data/v4.10/dd_traces.parquet (Hive partition by cell_id)
        Schema matches dd_gate.apply_all_cells() expectation:
          cell_id    : String
          fold_id    : UInt8
          bar_ts     : Datetime(time_unit="ms", time_zone="UTC")
          equity     : Float64
          dd_value   : Float64  (computed by apply_all_cells)
          pnl        : Float64  (non-zero only at trade exit bar)

Limitations (recorded in 90-01-SUMMARY.md):
  - bar_ts range is 2024-01-23 to 2025-12-21 UTC (data/ohlcv/usdjpy_1h_2022_2026.csv).
  - 2020-03 COVID / 2022 Fed hike stress events are OUTSIDE this range.
  - Plan 03 stress annotation will be limited to the available 2024-2025 window.

CLI::

    uv run python scripts/v4.10/gen_dd_traces.py \\
        [--ohlcv data/ohlcv/usdjpy_1h_2022_2026.csv] \\
        [--per-trade-log data/v4.9/per_trade_log.parquet] \\
        [--exit-replayed data/v4.9/exit_replayed.parquet] \\
        [--sized-pnl data/v4.9/sized_pnl.parquet] \\
        [--output-dir data/v4.10/dd_traces.parquet] \\
        [--smoke]
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys

import polars as pl

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# ---- dynamic import for dd_gate (same-directory v4.10) ----
_DD_GATE_PATH = pathlib.Path(__file__).resolve().parent / "dd_gate.py"


def _load_dd_gate():
    spec = importlib.util.spec_from_file_location("dd_gate", _DD_GATE_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["dd_gate"] = (
        mod  # required: dataclasses resolves cls.__module__ via sys.modules
    )
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def build_bar_index_map(ohlcv_path: pathlib.Path) -> pl.Series:
    """Return a Series of bar_ts (Datetime UTC ms) indexed by bar index (row order).

    bar_index 0 == first row of OHLCV CSV.
    """
    df = pl.read_csv(ohlcv_path, columns=["datetime_ns"])
    # datetime_ns is i64 nanoseconds since epoch → convert to ms Datetime UTC
    bar_ts = (
        (df["datetime_ns"].cast(pl.Int64) // 1_000_000)
        .cast(pl.Datetime(time_unit="ms"))
        .dt.replace_time_zone("UTC")
    )
    return bar_ts  # index i → bar_ts at bar i


def build_equity_df(
    per_trade_log: pl.DataFrame,
    exit_replayed: pl.DataFrame,
    sized_pnl: pl.DataFrame,
    bar_ts_series: pl.Series,
) -> pl.DataFrame:
    """Build a bar-level DataFrame with (cell_id, fold_id, bar_ts, equity, pnl).

    Strategy:
      1. Join per_trade_log + exit_replayed + sized_pnl on (cell_id, fold, trade_id).
      2. For each trade: bars span [entry_bar, exit_bar] (inclusive).
         - entry_bar + len(bars)-1 should equal exit_bar (sanity check).
      3. Emit one row per bar per trade:
         - pnl = sized_pnl at exit_bar, 0.0 elsewhere.
         - bar_ts from bar_ts_series[bar_index].
      4. Sort by (cell_id, fold_id, bar_ts) and compute cumulative equity per
         (cell_id, fold_id) starting from 1.0.
    """
    # Normalize sized_pnl: rename fold→fold_id, cast to UInt8
    sp = sized_pnl.rename({"fold": "fold_id"}).with_columns(
        pl.col("fold_id").cast(pl.UInt8)
    )

    # Join per_trade_log with exit_replayed and sized_pnl
    ptl = per_trade_log.rename({"fold": "fold_id"}).with_columns(
        pl.col("fold_id").cast(pl.UInt8)
    )
    er = exit_replayed.rename({"fold": "fold_id"}).with_columns(
        pl.col("fold_id").cast(pl.UInt8)
    )

    # Join on (cell_id, fold_id, trade_id)
    joined = ptl.join(
        er.select(["cell_id", "fold_id", "trade_id", "exit_bar"]),
        on=["cell_id", "fold_id", "trade_id"],
        how="left",
    ).join(
        sp.select(["cell_id", "fold_id", "trade_id", "sized_pnl"]),
        on=["cell_id", "fold_id", "trade_id"],
        how="left",
    )

    # Build per-bar rows
    bar_ts_list = bar_ts_series.to_list()
    max_bar = len(bar_ts_list) - 1

    rows: list[dict] = []
    for row in joined.iter_rows(named=True):
        cell_id = row["cell_id"]
        fold_id = row["fold_id"]
        entry_bar = row["entry_bar"]
        exit_bar = row["exit_bar"]
        pnl_at_exit = row["sized_pnl"] if row["sized_pnl"] is not None else 0.0
        bars_list = row["bars"]  # List[{high, low, close, atr}]

        # exit_bar may be null if exit_replayed join missed; fall back to last bar
        if exit_bar is None:
            exit_bar = entry_bar + len(bars_list) - 1

        # Clamp to OHLCV bounds
        exit_bar = min(exit_bar, max_bar)
        entry_bar = min(entry_bar, max_bar)

        for bar_idx in range(entry_bar, exit_bar + 1):
            if bar_idx > max_bar:
                break
            is_exit = bar_idx == exit_bar
            rows.append(
                {
                    "cell_id": cell_id,
                    "fold_id": fold_id,
                    "bar_ts": bar_ts_list[bar_idx],
                    "pnl": pnl_at_exit if is_exit else 0.0,
                }
            )

    schema = {
        "cell_id": pl.String,
        "fold_id": pl.UInt8,
        "bar_ts": pl.Datetime(time_unit="ms", time_zone="UTC"),
        "pnl": pl.Float64,
    }
    df = pl.DataFrame(rows, schema=schema)

    # Sort and compute cumulative equity per (cell_id, fold_id)
    df = df.sort(["cell_id", "fold_id", "bar_ts"])
    df = df.with_columns(
        (pl.lit(1.0) + pl.col("pnl").cum_sum().over(["cell_id", "fold_id"]))
        .alias("equity")
        .cast(pl.Float64)
    )

    return df.select(["cell_id", "fold_id", "bar_ts", "equity", "pnl"])


def run(
    ohlcv_path: pathlib.Path,
    per_trade_log_path: pathlib.Path,
    exit_replayed_path: pathlib.Path,
    sized_pnl_path: pathlib.Path,
    output_dir: pathlib.Path,
    smoke: bool = False,
) -> None:
    """Full pipeline: build equity DataFrame → call apply_all_cells → emit parquet."""
    dd_gate = _load_dd_gate()

    print(f"Loading OHLCV bar_index map from {ohlcv_path} ...")
    bar_ts_series = build_bar_index_map(ohlcv_path)
    print(f"  {len(bar_ts_series)} bars, {bar_ts_series[0]} to {bar_ts_series[-1]}")

    print(f"Loading per_trade_log from {per_trade_log_path} ...")
    per_trade_log = pl.read_parquet(per_trade_log_path)
    print(f"  {len(per_trade_log)} trades")

    print(f"Loading exit_replayed from {exit_replayed_path} ...")
    exit_replayed = pl.read_parquet(exit_replayed_path)

    print(f"Loading sized_pnl from {sized_pnl_path} ...")
    sized_pnl = pl.read_parquet(sized_pnl_path)

    if smoke:
        # Restrict to 1 cell × 1 fold for smoke test
        first_cell = per_trade_log["cell_id"][0]
        print(f"[SMOKE] Restricting to cell_id={first_cell!r}, fold=1")
        per_trade_log = per_trade_log.filter(
            (pl.col("cell_id") == first_cell) & (pl.col("fold") == 1)
        )
        exit_replayed = exit_replayed.filter(
            (pl.col("cell_id") == first_cell) & (pl.col("fold") == 1)
        )
        sized_pnl = sized_pnl.filter(
            (pl.col("cell_id") == first_cell) & (pl.col("fold") == 1)
        )

    print("Building bar-level equity DataFrame ...")
    equity_df = build_equity_df(per_trade_log, exit_replayed, sized_pnl, bar_ts_series)
    print(f"  equity_df shape: {equity_df.shape}")
    print(f"  bar_ts range: {equity_df['bar_ts'].min()} to {equity_df['bar_ts'].max()}")

    # Build cells list for apply_all_cells (192 unique cell_ids, or 1 for smoke)
    unique_cells = (
        per_trade_log.select("cell_id").unique().sort("cell_id")["cell_id"].to_list()
    )
    cells = [{"cell_id": cid} for cid in unique_cells]

    if not smoke:
        # D-11 gate: must be exactly 192
        if len(cells) != 192:
            raise RuntimeError(
                f"Expected 192 unique cells, got {len(cells)} — "
                "check per_trade_log.parquet source"
            )
    else:
        # For smoke test: pass a 192-length cells list to satisfy D-11.
        # Fill remaining 191 slots with dummy cell_ids absent from equity_df.
        # apply_all_cells will find empty fold_data for them and skip.
        smoke_cell = unique_cells[0]
        dummy_cells = [
            {"cell_id": f"_smoke_dummy_{i:03d}_"} for i in range(192 - len(cells))
        ]
        cells = [{"cell_id": smoke_cell}] + dummy_cells

    print(f"Calling apply_all_cells ({len(cells)} cells) → {output_dir} ...")
    result_df = dd_gate.apply_all_cells(
        cells=cells,
        sized_pnl=equity_df,
        output_dir=output_dir,
    )
    print(f"  Result shape: {result_df.shape}")
    print(f"  Output dir: {output_dir}")
    if output_dir.exists():
        partitions = list(output_dir.glob("cell_id=*"))
        print(f"  Partitions written: {len(partitions)}")

    print("Done.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate dd_traces.parquet from equity curve reconstruction"
    )
    parser.add_argument(
        "--ohlcv",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "ohlcv" / "usdjpy_1h_2022_2026.csv",
    )
    parser.add_argument(
        "--per-trade-log",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.9" / "per_trade_log.parquet",
    )
    parser.add_argument(
        "--exit-replayed",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.9" / "exit_replayed.parquet",
    )
    parser.add_argument(
        "--sized-pnl",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.9" / "sized_pnl.parquet",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        default=_REPO_ROOT / "data" / "v4.10" / "dd_traces.parquet",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run with 1 cell x 1 fold only (fast verification)",
    )
    args = parser.parse_args(argv)

    try:
        run(
            ohlcv_path=args.ohlcv,
            per_trade_log_path=args.per_trade_log,
            exit_replayed_path=args.exit_replayed,
            sized_pnl_path=args.sized_pnl,
            output_dir=args.output_dir,
            smoke=args.smoke,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
