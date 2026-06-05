"""
n_per_cell_audit.py — Phase 100 D-C1: 192-cell + 4 marginal n-per-cell audit.

Usage:
    uv run python scripts/v4.12/n_per_cell_audit.py \
        --data-parquet data/v4.12/classifier_v412.parquet \
        --output n_per_cell_audit.json

    --check : smoke flag — emit 1 cell only (Wave 0/1 acceptance mode).

Exit codes:
    0  : success
    1  : unexpected error
    2  : data file not found (classifier_v412.parquet not yet generated — RUN AFTER PHASE 101)

Citations: D-C1 (192-cell raw + 4 marginals), 100-PLAN.md <interfaces>, PATTERNS.md lines 188-264.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Guard: polars is the standard stack (D-36, RESEARCH.md). No pandas.
try:
    import polars as pl
except ImportError:
    print(
        "ERROR: polars is not installed. Run: uv add polars",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Locked D-C1 grid constants (100-PLAN.md <interfaces> §n_per_cell grid) ──
PAIRS: list[str] = ["USDJPY", "EURUSD", "AUDUSD", "EURJPY"]
EVENTS: list[str] = [
    "FOMC_2024-01-31",
    "FOMC_2024-03-20",
    "ECB_2024-01-25",
    "ECB_2024-03-07",
]
TOD: list[str] = ["pre", "during", "post"]
VOL_STANCE: list[str] = ["HIGH_HAWK", "HIGH_DOV", "LOW_HAWK", "LOW_DOV"]

EXPECTED_CELLS: int = len(PAIRS) * len(EVENTS) * len(TOD) * len(VOL_STANCE)  # 192
NULL_SHIP_THRESHOLD_N_MIN: int = 20

# Default data path — does not exist until Phase 101 classifier lands
DEFAULT_DATA_PARQUET = Path("data/v4.12/classifier_v412.parquet")


def _load_parquet(path: Path) -> pl.DataFrame:
    """Load classifier parquet; exit 2 with helpful message if not found."""
    if not path.exists():
        print(
            f"ERROR: data file not found: {path}\n"
            "RUN AFTER PHASE 101 CLASSIFIER LANDS — "
            "classifier_v412.parquet is generated in Phase 101.",
            file=sys.stderr,
        )
        sys.exit(2)
    return pl.read_parquet(path)


def _build_cells_raw(df: pl.DataFrame, check_mode: bool) -> dict[str, int]:
    """
    Count observations per (pair, event, tod, vol_stance) cell.
    Returns dict keyed as 'pair|event|tod|vol_stance'.

    In --check mode: emit only the first cell (USDJPY|FOMC_2024-01-31|pre|HIGH_HAWK).
    Uses itertools.product — 'Don't Hand-Roll' principle from RESEARCH.md.
    """
    cells_raw: dict[str, int] = {}

    grid = list(itertools.product(PAIRS, EVENTS, TOD, VOL_STANCE))
    if check_mode:
        grid = grid[:1]

    for pair, event, tod, vol_stance in grid:
        key = f"{pair}|{event}|{tod}|{vol_stance}"
        n = df.filter(
            (pl.col("pair") == pair)
            & (pl.col("event") == event)
            & (pl.col("tod") == tod)
            & (pl.col("vol_stance") == vol_stance)
        ).height
        cells_raw[key] = n

    return cells_raw


def _build_marginals(df: pl.DataFrame) -> dict[str, dict[str, int]]:
    """
    Compute 4 marginal aggregates per D-C1:
      n_per_pair, n_per_event, n_per_tod, n_per_vol_stance
    """

    def _marginal(col: str, values: list[str]) -> dict[str, int]:
        return {v: df.filter(pl.col(col) == v).height for v in values}

    return {
        "n_per_pair": _marginal("pair", PAIRS),
        "n_per_event": _marginal("event", EVENTS),
        "n_per_tod": _marginal("tod", TOD),
        "n_per_vol_stance": _marginal("vol_stance", VOL_STANCE),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="n_per_cell_audit.py — D-C1 192-cell n audit"
    )
    parser.add_argument(
        "--data-parquet",
        type=Path,
        default=DEFAULT_DATA_PARQUET,
        help="Path to classifier_v412.parquet (default: data/v4.12/classifier_v412.parquet)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSON path",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Smoke mode: emit only 1 cell (Wave 0/1 acceptance gate)",
    )
    args = parser.parse_args(argv)

    df = _load_parquet(args.data_parquet)
    cells_raw = _build_cells_raw(df, check_mode=args.check)

    # In check mode, skip marginals (only 1 cell sampled — marginals would be misleading)
    if args.check:
        marginals = {
            "n_per_pair": {},
            "n_per_event": {},
            "n_per_tod": {},
            "n_per_vol_stance": {},
        }
    else:
        marginals = _build_marginals(df)

    result = {
        "cells_raw_192": cells_raw,
        "marginals": marginals,
        "expected_cells": EXPECTED_CELLS,
        "null_ship_threshold_n_min": NULL_SHIP_THRESHOLD_N_MIN,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(f"OK: wrote {args.output} ({len(cells_raw)} cells)")


if __name__ == "__main__":
    main()
