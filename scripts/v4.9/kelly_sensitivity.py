"""Kelly sensitivity ±10% grid + power_budget_v49.json (Phase 87 SIZE-04)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from typing import Any

import polars as pl

_SIZER_PATH = pathlib.Path(__file__).parent / "sizer.py"
_spec = importlib.util.spec_from_file_location("sizer", _SIZER_PATH)
sizer = importlib.util.module_from_spec(_spec)
sys.modules["sizer"] = sizer
_spec.loader.exec_module(sizer)

GRID_DELTA = 0.10
GRID_POINTS = 5  # center + 4 corners


def kelly_sensitivity_grid(
    kelly_inputs: Any, *, delta: float = GRID_DELTA
) -> dict[str, float]:
    """Return 5-point sensitivity grid (center + 4 corners at ±delta).

    Args:
        kelly_inputs: sizer.KellyInputs with p_lower, b_lower
        delta: perturbation fraction (default 0.10 = ±10%)

    Returns:
        dict mapping point name to f* value (clamped to [0, KELLY_FRACTION_MAX])
    """
    p = kelly_inputs.p_lower
    b = kelly_inputs.b_lower
    points = {
        "center": (p, b),
        "ne": (p * (1 + delta), b * (1 + delta)),
        "se": (p * (1 + delta), b * (1 - delta)),
        "nw": (p * (1 - delta), b * (1 + delta)),
        "sw": (p * (1 - delta), b * (1 - delta)),
    }
    out: dict[str, float] = {}
    for key, (pv, bv) in points.items():
        if bv <= 0 or not (0 < pv < 1):
            out[key] = float("-inf")
            continue
        f = pv - (1 - pv) / bv
        out[key] = max(0.0, min(f, sizer.KELLY_FRACTION_MAX))
    return out


def generate_power_budget(
    sized_pnl_df: pl.DataFrame,
    exit_replayed_df: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """Walk all 192 cells in sized_pnl_df and compute robust region.

    Cell-level gate (D-19 initial): robust_pass = True iff ALL folds in the
    cell have ALL 5 grid points >= KELLY_FRACTION_MIN (0.25), AND no fold
    raised InsufficientData.

    Cells / folds with InsufficientData → skipped_reason = "InsufficientData".
    Cells with mixed skip/pass → skipped_reason = "PartialInsufficientData".

    Args:
        sized_pnl_df: Wave 2 output (contains fold, cell_id, trade_id, m_t, etc.)
        exit_replayed_df: exit_replayed.parquet with pnl column (required for
            estimate_kelly_inputs which needs raw pnl, not sized_pnl).
            If None, will try to load from data/v4.9/exit_replayed.parquet.
    """
    # Resolve exit_replayed for pnl column (estimate_kelly_inputs needs raw pnl)
    if exit_replayed_df is None:
        _er_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "data"
            / "v4.9"
            / "exit_replayed.parquet"
        )
        exit_replayed_df = pl.read_parquet(_er_path)

    # Join pnl from exit_replayed onto sized_pnl (join on fold + cell_id + trade_id)
    trades_df = sized_pnl_df.join(
        exit_replayed_df.select(["fold", "cell_id", "trade_id", "pnl"]),
        on=["fold", "cell_id", "trade_id"],
        how="left",
    )

    cells: dict[str, Any] = {}
    pass_count = 0
    unique_cells = trades_df["cell_id"].unique().sort().to_list()

    for cell_id in unique_cells:
        cell_df = trades_df.filter(pl.col("cell_id") == cell_id)
        cell_entry: dict[str, Any] = {
            "p_hat": None,
            "b_hat": None,
            "f_star_center": None,
            "f_star_min": None,
            "f_star_max": None,
            "robust_pass": False,
            "skipped_reason": None,
            "folds": {},
        }

        fold_pass: list[bool] = []
        fold_skipped = False
        cell_fmin_across = float("inf")
        cell_fmax_across = float("-inf")
        cell_p_hat = None
        cell_b_hat = None
        cell_fcenter = None

        for fold_v in cell_df["fold"].unique().sort():
            fold_df = cell_df.filter(pl.col("fold") == fold_v)
            fold_key = str(int(fold_v))

            try:
                ki = sizer.estimate_kelly_inputs(fold_df)
            except sizer.InsufficientData:
                cell_entry["folds"][fold_key] = {
                    "f_star_min": None,
                    "robust_pass": False,
                    "skipped_reason": "InsufficientData",
                }
                fold_skipped = True
                continue

            grid = kelly_sensitivity_grid(ki)
            fmin = min(grid.values())
            fmax = max(grid.values())
            passed = fmin >= sizer.KELLY_FRACTION_MIN  # 0.25

            cell_entry["folds"][fold_key] = {
                "f_star_min": float(fmin),
                "robust_pass": bool(passed),
                "skipped_reason": None,
            }
            fold_pass.append(passed)

            if fmin < cell_fmin_across:
                cell_fmin_across = fmin
            if fmax > cell_fmax_across:
                cell_fmax_across = fmax

            if cell_p_hat is None:
                cell_p_hat = ki.p_lower
                cell_b_hat = ki.b_lower
                cell_fcenter = grid["center"]

        if fold_skipped and not fold_pass:
            # All folds skipped — fully insufficient
            cell_entry["skipped_reason"] = "InsufficientData"
            cell_entry["robust_pass"] = False
        elif fold_skipped and fold_pass:
            # Mixed: some folds ok, some skipped
            cell_entry["skipped_reason"] = "PartialInsufficientData"
            cell_entry["robust_pass"] = False
            cell_entry["p_hat"] = cell_p_hat
            cell_entry["b_hat"] = cell_b_hat
            cell_entry["f_star_center"] = cell_fcenter
            cell_entry["f_star_min"] = float(cell_fmin_across) if fold_pass else None
            cell_entry["f_star_max"] = float(cell_fmax_across) if fold_pass else None
        else:
            # No skips — normal path
            cell_entry["p_hat"] = cell_p_hat
            cell_entry["b_hat"] = cell_b_hat
            cell_entry["f_star_center"] = cell_fcenter
            cell_entry["f_star_min"] = float(cell_fmin_across) if fold_pass else None
            cell_entry["f_star_max"] = float(cell_fmax_across) if fold_pass else None
            # Cell-level gate: ALL folds must pass (D-19 initial locked)
            cell_entry["robust_pass"] = bool(len(fold_pass) > 0 and all(fold_pass))

        cells[cell_id] = cell_entry

        # pass_count: robust_pass=True AND skipped_reason is None
        if cell_entry["robust_pass"] and cell_entry["skipped_reason"] is None:
            pass_count += 1

    return {
        "sizing_exit_commit": sizer.SIZING_EXIT_COMMIT,
        "seed": sizer.BCA_BOOTSTRAP_SEED,
        "grid_points": GRID_POINTS,
        "grid_delta": GRID_DELTA,
        "kelly_fraction_min": sizer.KELLY_FRACTION_MIN,
        "robust_region_pass_cells": int(pass_count),
        "total_cells": 192,
        "cells": cells,
    }


def _canonical_write(obj: dict[str, Any], output: pathlib.Path) -> None:
    """Write canonical JSON (sort_keys, no spaces, no trailing newline, UTF-8)."""
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    output.write_text(s, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate power_budget_v49.json from sized_pnl.parquet"
    )
    parser.add_argument("--input", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    df = pl.read_parquet(args.input)
    # exit_replayed.parquet is assumed to be in the same data/v4.9/ directory
    er_path = args.input.parent / "exit_replayed.parquet"
    er_df = pl.read_parquet(er_path) if er_path.exists() else None
    budget = generate_power_budget(df, exit_replayed_df=er_df)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _canonical_write(budget, args.output)
    print(
        f"wrote {args.output} "
        f"pass={budget['robust_region_pass_cells']}/{budget['total_cells']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
