"""CLI driver: exit_replayed.parquet → sized_pnl.parquet (Phase 87 Layer 2).

Consumes sizer.py public API; fold × cell granularity for (p, b) estimation.
Join per_trade_log.parquet for atr_at_entry (not present in exit_replayed.parquet).

Deviation [Rule 3 - Blocking]: exit_replayed.parquet lacks atr_at_entry column.
Joined from per_trade_log.parquet on (cell_id, fold, trade_id).
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
from typing import Any

import polars as pl

# ---------------------------------------------------------------------------
# Load sizer.py via importlib (keeps scripts/ importable without package setup)
# ---------------------------------------------------------------------------
_SIZER_PATH = pathlib.Path(__file__).parent / "sizer.py"
_spec = importlib.util.spec_from_file_location("sizer", _SIZER_PATH)
assert _spec is not None and _spec.loader is not None
sizer = importlib.util.module_from_spec(_spec)
sys.modules["sizer"] = sizer
_spec.loader.exec_module(sizer)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------
BINDING_ENUM = pl.Enum(["kelly", "atr_norm", "fixed_cap", "zero"])

OUTPUT_SCHEMA: dict[str, Any] = {
    "fold": pl.Int64,
    "cell_id": pl.Utf8,
    "trade_id": pl.UInt64,
    "kelly_size": pl.Float64,
    "atr_size": pl.Float64,
    "cap_size": pl.Float64,
    "size": pl.Float64,
    "binding_reason": BINDING_ENUM,
    "m_t": pl.Float64,
    "sized_pnl": pl.Float64,
}

# Quad-pin provenance anchors (Phase 85 SEAL + Phase 86 commit)
_THRESHOLD_COMMIT = "6527cbc"
_REGIME_COMMIT = "90bf4b2"


# ---------------------------------------------------------------------------
# Per-cell processing
# ---------------------------------------------------------------------------


def _zero_row(fold: int, cell_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fold": fold,
        "cell_id": cell_id,
        "trade_id": int(row["trade_id"]),
        "kelly_size": 0.0,
        "atr_size": 0.0,
        "cap_size": 0.0,
        "size": 0.0,
        "binding_reason": "zero",
        "m_t": 1.0,
        "sized_pnl": 0.0,
    }


def process_cell(
    cell_df: pl.DataFrame,
    *,
    equity: float,
    dd_cap_abs: float,
    risk_pct: float = sizer.DEFAULT_RISK_PCT,
) -> list[dict[str, Any]]:
    """Process a single fold × cell slice; return list of row dicts."""
    fold = int(cell_df["fold"][0])
    cell_id = str(cell_df["cell_id"][0])
    rows: list[dict[str, Any]] = []

    # --- estimate Kelly inputs (InsufficientData -> zero rows) ---
    try:
        ki = sizer.estimate_kelly_inputs(cell_df)
    except sizer.InsufficientData:
        for row in cell_df.iter_rows(named=True):
            rows.append(_zero_row(fold, cell_id, row))
        return rows

    # --- compute f* and clip ---
    fraction = ki.p_lower - (1.0 - ki.p_lower) / ki.b_lower
    # Use skeleton clipping [0.0, 0.5] per plan skeleton (D-17 strict [0.25,0.5]
    # would mean kelly_size always > cap=2000; skeleton clip allows zero-valued f*)
    f_clipped = max(0.0, min(fraction, sizer.KELLY_FRACTION_MAX))

    try:
        kf = sizer.FractionalKelly.try_new(f_clipped)
    except sizer.KellyOverflow:
        for row in cell_df.iter_rows(named=True):
            rows.append(_zero_row(fold, cell_id, row))
        return rows

    # --- precompute cell-level sizes (per-trade ATR varies) ---
    cap_size_abs = equity * dd_cap_abs

    for row in cell_df.iter_rows(named=True):
        atr = float(row["atr_at_entry"])
        size, binding = sizer.compute_position_size(
            ki, atr, equity, kf, dd_cap_abs, risk_pct=risk_pct
        )
        kelly_size = equity * kf.value
        atr_size = (equity * risk_pct / (sizer.ATR_K_PRIMARY * atr)) if atr > 0 else 0.0
        scale = (size / equity) if equity > 0 else 0.0
        sized_pnl = scale * float(row["pnl"])
        rows.append(
            {
                "fold": fold,
                "cell_id": cell_id,
                "trade_id": int(row["trade_id"]),
                "kelly_size": float(kelly_size),
                "atr_size": float(atr_size),
                "cap_size": float(cap_size_abs),
                "size": float(size),
                "binding_reason": binding.value,
                "m_t": 1.0,
                "sized_pnl": float(sized_pnl),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate sized_pnl.parquet from exit_replayed.parquet."
    )
    parser.add_argument(
        "--input",
        required=True,
        type=pathlib.Path,
        help="Path to exit_replayed.parquet",
    )
    parser.add_argument(
        "--per-trade-log",
        type=pathlib.Path,
        default=None,
        help="Path to per_trade_log.parquet (for atr_at_entry). "
        "Defaults to <input-dir>/per_trade_log.parquet",
    )
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--equity", type=float, default=10000.0)
    parser.add_argument(
        "--dd-cap-abs",
        type=float,
        required=True,
        help="fraction of equity for fixed_cap (e.g. 0.20)",
    )
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=sizer.DEFAULT_RISK_PCT,
        help="ATR sizing risk fraction (default: DEFAULT_RISK_PCT from SEAL)",
    )
    args = parser.parse_args(argv)

    # --- load inputs ---
    df = pl.read_parquet(args.input)

    # --- join atr_at_entry from per_trade_log (Deviation: not in exit_replayed) ---
    ptl_path = args.per_trade_log or (args.input.parent / "per_trade_log.parquet")
    ptl = pl.read_parquet(ptl_path).select(
        ["cell_id", "fold", "trade_id", "atr_at_entry"]
    )
    df = df.join(ptl, on=["cell_id", "fold", "trade_id"], how="left")

    null_atr = df["atr_at_entry"].null_count()
    if null_atr > 0:
        print(
            f"WARNING: {null_atr} rows have null atr_at_entry after join",
            file=sys.stderr,
        )

    # --- fold × cell loop ---
    all_rows: list[dict[str, Any]] = []
    for fold_v in df["fold"].unique().sort():
        fold_df = df.filter(pl.col("fold") == fold_v)
        for cell_id in fold_df["cell_id"].unique().sort():
            cell_df = fold_df.filter(pl.col("cell_id") == cell_id)
            all_rows.extend(
                process_cell(
                    cell_df,
                    equity=args.equity,
                    dd_cap_abs=args.dd_cap_abs,
                    risk_pct=args.risk_pct,
                )
            )

    out_df = pl.DataFrame(all_rows, schema=OUTPUT_SCHEMA)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # --- write with quad-pin provenance metadata ---
    out_df.write_parquet(
        args.output,
        compression="zstd",
        statistics=True,
        metadata={
            "sizing_exit_commit": sizer.SIZING_EXIT_COMMIT,
            "threshold_commit": _THRESHOLD_COMMIT,
            "regime_commit": _REGIME_COMMIT,
        },
    )

    binding_counts = (
        out_df["binding_reason"].value_counts().sort("count", descending=True)
    )
    print(
        f"wrote {args.output}  rows={out_df.height}  cells={out_df['cell_id'].n_unique()}"
    )
    print("binding_reason distribution:")
    for row in binding_counts.iter_rows(named=True):
        print(f"  {row['binding_reason']}: {row['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
