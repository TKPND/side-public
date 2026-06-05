"""Phase 94 volatility regime hard filter (Option A).

Drops cells whose bucket not in allowed_buckets BEFORE WFD verdict.

D-17: SEAL untouched. Runtime VOL_ prefix strip for comparison (D-39).
D-35: flat import (scripts/v4.11 has dot in name, not a Python package).
D-39: single-script CLI with --neutral-mode flag.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import polars as pl

# D-35 flat import (scripts/v4.11 cannot be a Python package due to the dot in the name).
from seal_drift_check import (  # type: ignore[import-not-found]
    verify_seal_or_raise,
)

# D-17/D-22/D-32 import-time fail-close (fires BEFORE any filter_cells call).
verify_seal_or_raise()

# ---- Module constants ----

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_VOL_PARQUET = _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet"
_DEFAULT_SLOT_LABELS = _REPO_ROOT / "data" / "slot_labels.parquet"
_DEFAULT_OUTPUT = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"
_SEAL_FILTER_SPEC = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "92-scope-lock-pre-registration-seal"
    / "SEAL"
    / "filter_spec.json"
)

# D-34: VOL_ prefix constants (4-char uniform length, Phase 93 lock).
_BUCKET_LOW = "VOL_LOW"
_BUCKET_MID = "VOL_MID"
_BUCKET_HIGH = "VOL_HIGH"
_BUCKET_NA = "VOL_NA"
_ALL_BUCKETS_NORMALIZED: tuple[str, ...] = ("HIGH", "MID", "LOW", "NA")

_NEUTRAL_ALLOWED: tuple[str, ...] = (
    "HIGH",
    "MID",
    "LOW",
    "NA",
)  # D-39 neutral override


def strip_vol_prefix(bucket_str: str) -> str:
    """Normalize VOL_HIGH -> HIGH for SEAL comparison (D-39). SEAL untouched.

    Idempotent: strip_vol_prefix("HIGH") == "HIGH".
    """
    return bucket_str.removeprefix("VOL_")


def load_seal_allowed_buckets() -> list[str]:
    """Read SEAL filter_spec.json .allowed_buckets (D-17 read-only)."""
    spec = json.loads(_SEAL_FILTER_SPEC.read_bytes())
    return list(spec["allowed_buckets"])


def filter_cells(
    cells_df: pl.DataFrame,
    vol_per_slot_df: pl.DataFrame,
    *,
    neutral_mode: bool = False,
) -> pl.DataFrame:
    """Apply Option A hard filter to cells.

    Parameters
    ----------
    cells_df
        DataFrame with at least columns (cell_id: str, pair: str, event_ts: datetime).
    vol_per_slot_df
        Phase 93 output with (pair: str, bar_time: datetime, bucket: str)
        where bucket ∈ {"VOL_LOW", "VOL_MID", "VOL_HIGH", "VOL_NA"} (D-34).
    neutral_mode
        If True, override allowed_buckets to full set [HIGH, MID, LOW, NA] (D-39).
        SEAL remains untouched — override is runtime-only.

    Returns
    -------
    pl.DataFrame with columns (cell_id: str, pass_flag: bool, bucket: str).
    Row count == cells_df row count. One row per input cell.

    Notes
    -----
    - JOIN semantics: LEFT join on (pair, event_ts == bar_time) per D-33 Addendum 2.
    - Cells without a matching vol_per_slot row → bucket filled with "VOL_NA"
      (D-34: missing-coverage sentinel; dropped in active mode, passes in neutral).
    - SEAL allowed_buckets is ["HIGH"] (D-05/D-06 pre-registered); runtime comparison
      uses strip_vol_prefix() to normalize "VOL_HIGH" → "HIGH" without touching SEAL.
    """
    if neutral_mode:
        allowed_normalized = set(_NEUTRAL_ALLOWED)
    else:
        allowed_normalized = set(load_seal_allowed_buckets())

    joined = cells_df.join(
        vol_per_slot_df.select(["pair", "bar_time", "bucket"]),
        left_on=["pair", "event_ts"],
        right_on=["pair", "bar_time"],
        how="left",
    ).with_columns(
        pl.col("bucket").fill_null(_BUCKET_NA).alias("bucket"),
    )

    result = joined.with_columns(
        pl.col("bucket")
        .map_elements(strip_vol_prefix, return_dtype=pl.Utf8)
        .is_in(list(allowed_normalized))
        .alias("pass_flag"),
    ).select(["cell_id", "pass_flag", "bucket"])

    return result


def _resolve_output_dir(arg_value: str) -> pathlib.Path:
    """Resolve --output-dir under repo root. Reject path traversal (T-94-01-04)."""
    resolved = pathlib.Path(arg_value).resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise SystemExit(
            f"--output-dir must resolve under repo root ({_REPO_ROOT}); got {resolved}"
        ) from exc
    return resolved


def main() -> None:
    """Phase 94 filter CLI (FILT-01). Run from repo root."""
    parser = argparse.ArgumentParser(
        description=(
            "Phase 94 vol regime hard filter (Option A). "
            "Run from repo root. --neutral-mode emits PARITY baseline artifact "
            "(allowed_buckets=[HIGH,MID,LOW,NA] runtime override, SEAL untouched). "
            "Default (no flag) = active mode, allowed_buckets=[HIGH] per SEAL filter_spec.json."
        ),
    )
    parser.add_argument(
        "--neutral-mode",
        action="store_true",
        help="Override allowed_buckets to full set [HIGH,MID,LOW,NA] (PARITY baseline).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_DEFAULT_OUTPUT.parent),
        help=f"Output directory under repo root (default: {_DEFAULT_OUTPUT.parent}).",
    )
    parser.add_argument(
        "--vol-parquet",
        type=str,
        default=str(_DEFAULT_VOL_PARQUET),
        help=f"vol_per_slot parquet path (default: {_DEFAULT_VOL_PARQUET}).",
    )
    parser.add_argument(
        "--slot-labels",
        type=str,
        default=str(_DEFAULT_SLOT_LABELS),
        help=f"slot_labels parquet path (default: {_DEFAULT_SLOT_LABELS}).",
    )
    args = parser.parse_args()

    output_dir = _resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "cells_post_filter.parquet"

    print(f"[vol_regime_filter] neutral_mode={args.neutral_mode}")
    if args.neutral_mode:
        print("[vol_regime_filter] NEUTRAL MODE: allowed_buckets=[HIGH,MID,LOW,NA]")
    else:
        print(
            f"[vol_regime_filter] ACTIVE MODE: allowed_buckets={load_seal_allowed_buckets()}"
        )

    vol_df = pl.read_parquet(args.vol_parquet)
    slot_labels = pl.read_parquet(args.slot_labels)
    if not {"cell_id", "pair", "event_ts"}.issubset(set(slot_labels.columns)):
        raise SystemExit(
            f"slot_labels parquet missing required columns; got {slot_labels.columns}"
        )

    result = filter_cells(
        slot_labels.select(["cell_id", "pair", "event_ts"]),
        vol_df,
        neutral_mode=args.neutral_mode,
    )
    result.write_parquet(output_path)

    n_pass = int(result.filter(pl.col("pass_flag")).height)
    n_total = int(result.height)
    print(
        f"[vol_regime_filter] wrote {output_path} ({n_pass}/{n_total} cells pass_flag=true)"
    )


if __name__ == "__main__":
    main()
