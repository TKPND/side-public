"""CLASS-01/02: Daily ATR-14 Wilder + pooled_global rolling quantile vol estimator.

D-18 pair_policy=pooled_global — per-pair ATR-14 Wilder -> vertical concat
-> sort_by(bar_time) -> rolling_quantile(0.33/0.67, window_size=90*n_pairs, min_samples=1)
-> per-row bucket (LOW/MID/HIGH) assignment + D-34 VOL_ prefix at column level.
D-19' polars 1.40.0: min_samples (NOT min_periods)
D-20 / D-34 warmup (<14 bars) -> vol_input_ts=NaT, bucket="VOL_NA" placeholder
     (Addendum 2 supersedes old "NA " str[3] — now 4-char VOL_NA for visual/naming parity)
D-21 embargo belt-and-suspenders: runtime assert max(vol_input_ts) < event_ts
D-33 output schema (Addendum 2):
     primary key = (pair:str, bar_time:datetime[ns])
     columns = [pair, bar_time, atr_14, rolling_quantile_low, rolling_quantile_high,
                bucket, vol_input_ts]
     cell_id column is DROPPED (D-31' supersede).
     Phase 94 JOIN: vol_per_slot.(pair, bar_time) == slot_labels.(pair, event_ts)
D-34 bucket column values: VOL_LOW / VOL_MID / VOL_HIGH / VOL_NA
     (SEAL vol_cuts.json regime_buckets = ["LOW","MID","HIGH"] is untouched;
      VOL_ prefix is applied at RUNTIME in assign_buckets.)
D-35 Import lock: scripts/v4.11 is dot-in-dir -> sibling import MUST be flat.
     Use `from seal_drift_check import ...` (dot-path package import is forbidden).
D-32 SEAL JSON read-only (no write)
"""

from __future__ import annotations

import argparse
import json
import pathlib
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl

# D-35 flat import (scripts/v4.11 cannot be a Python package due to the dot in the name).
from seal_drift_check import (  # type: ignore[import-not-found]
    SEAL_DIR_DEFAULT,
    verify_seal_or_raise,
)

# D-17/D-22/D-32 import-time fail-close
verify_seal_or_raise()

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_OUT = _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet"
_DEFAULT_SLOT_LABELS = _REPO_ROOT / "data" / "slot_labels.parquet"

# D-34: VOL_ prefix constants (4-char uniform length; supersedes old "NA " str[3]).
_BUCKET_LOW = "VOL_LOW"
_BUCKET_MID = "VOL_MID"
_BUCKET_HIGH = "VOL_HIGH"
_BUCKET_NA = "VOL_NA"  # warmup placeholder (D-34)
_BUCKET_ALLOWED: set[str] = {_BUCKET_LOW, _BUCKET_MID, _BUCKET_HIGH, _BUCKET_NA}

# D-33 output schema column order (primary key first)
_OUT_COLUMNS: list[str] = [
    "pair",
    "bar_time",
    "atr_14",
    "rolling_quantile_low",
    "rolling_quantile_high",
    "bucket",
    "vol_input_ts",
]


def _load_seal_spec(seal_dir: pathlib.Path = SEAL_DIR_DEFAULT) -> dict[str, Any]:
    """Read-only SEAL spec load (D-32). No mutation."""
    classifier = json.loads((seal_dir / "classifier_spec.json").read_text())
    vol_cuts = json.loads((seal_dir / "vol_cuts.json").read_text())
    return {"classifier": classifier, "vol_cuts": vol_cuts}


def compute_atr14_wilder(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """ATR-14 Wilder smoothing.

    seed = mean(TR[0:14]), then atr_i = atr_{i-1} + (TR_i - atr_{i-1}) / 14.
    Returns array of same length: index 0..12 are NaN, index 13+ converge.
    """
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = len(h)
    tr = np.empty(n)
    # TR[0]: no previous close, so TR = high - low only.
    tr[0] = h[0] - lo[0]
    # TR[i] = max(high-low, |high-prev_close|, |low-prev_close|)
    if n > 1:
        prev_c = c[:-1]
        tr[1:] = np.maximum(
            h[1:] - lo[1:],
            np.maximum(np.abs(h[1:] - prev_c), np.abs(lo[1:] - prev_c)),
        )
    atr = np.full(n, np.nan)
    if n >= 14:
        atr[13] = np.mean(tr[0:14])
        for i in range(14, n):
            atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) / 14.0
    return atr


def _atr_per_pair(df_pair: pl.DataFrame) -> pl.DataFrame:
    """Compute ATR-14 Wilder for a single pair DataFrame sorted by bar_time."""
    atr = compute_atr14_wilder(
        df_pair["high"].to_numpy(),
        df_pair["low"].to_numpy(),
        df_pair["close"].to_numpy(),
    )
    return df_pair.with_columns(pl.Series("atr_14", atr, nan_to_null=True))


def build_pooled_vol_frame(
    ohlc_all_pairs: pl.DataFrame,
    lookback_bars_per_pair: int = 90,
) -> pl.DataFrame:
    """D-18 pooled_global + D-19' min_samples.

    Per-pair ATR-14 Wilder -> vertical concat -> sort_by(bar_time) ->
    rolling_quantile(0.33/0.67, window_size=90*n_pairs, min_samples=1).

    Parameters
    ----------
    ohlc_all_pairs
        DataFrame with columns [pair, bar_time, open, high, low, close].
    lookback_bars_per_pair
        Calendar days per pair (default 90 per D-03 SEAL spec).
        window_size = lookback_bars_per_pair * n_pairs (D-19' Option A).
    """
    n_pairs = ohlc_all_pairs["pair"].n_unique()
    window_size = lookback_bars_per_pair * n_pairs

    per_pair_atr = (
        ohlc_all_pairs.sort(["pair", "bar_time"])
        .group_by("pair", maintain_order=True)
        .map_groups(_atr_per_pair)
    )
    pooled = per_pair_atr.sort("bar_time")

    pooled = pooled.with_columns(
        pl.col("atr_14")
        .rolling_quantile(quantile=0.33, window_size=window_size, min_samples=1)
        .alias("rolling_quantile_low"),
        pl.col("atr_14")
        .rolling_quantile(quantile=0.67, window_size=window_size, min_samples=1)
        .alias("rolling_quantile_high"),
    )
    return pooled


def assign_buckets(df: pl.DataFrame) -> pl.DataFrame:
    """D-20/D-34 bucket assignment with VOL_ prefix at runtime.

    warmup NaN -> bucket='VOL_NA', vol_input_ts=NaT.
    D-34 bucket_rule with VOL_ prefix applied at runtime (SEAL JSON untouched):
      LOW:  atr < q33       -> VOL_LOW
      MID:  q33 <= atr <q67 -> VOL_MID
      HIGH: atr >= q67      -> VOL_HIGH
      (warmup / NaN         -> VOL_NA)
    """
    return df.with_columns(
        pl.when(pl.col("atr_14").is_null())
        .then(pl.lit(_BUCKET_NA))
        .when(pl.col("atr_14") < pl.col("rolling_quantile_low"))
        .then(pl.lit(_BUCKET_LOW))
        .when(pl.col("atr_14") < pl.col("rolling_quantile_high"))
        .then(pl.lit(_BUCKET_MID))
        .otherwise(pl.lit(_BUCKET_HIGH))
        .alias("bucket"),
        pl.when(pl.col("atr_14").is_null())
        .then(None)
        .otherwise(pl.col("bar_time"))
        .cast(pl.Datetime("ns"))
        .alias("vol_input_ts"),
    )


def assert_embargo(vol_df: pl.DataFrame, event_ts: datetime) -> None:
    """D-21 runtime belt-and-suspenders embargo check.

    Raises RuntimeError with "Embargo violation" if max(vol_input_ts) >= event_ts.
    T-93-02 defensive guard against future-bar look-ahead bias.
    """
    non_null = vol_df.filter(pl.col("vol_input_ts").is_not_null())
    if len(non_null) == 0:
        return
    max_ts = non_null["vol_input_ts"].max()
    if max_ts is not None and max_ts >= event_ts:
        raise RuntimeError(
            f"Embargo violation (D-21): max(vol_input_ts)={max_ts} "
            f">= event_ts={event_ts}. Future data leakage detected."
        )


def emit_vol_per_slot_parquet(
    vol_df: pl.DataFrame,
    out_path: pathlib.Path = _DEFAULT_OUT,
) -> pathlib.Path:
    """D-33 output schema (Addendum 2): PK=(pair, bar_time); NO cell_id column.

    Emits exactly 7 columns in order: pair, bar_time, atr_14,
    rolling_quantile_low, rolling_quantile_high, bucket, vol_input_ts.

    Phase 94 is responsible for JOINing slot_labels.parquet on
    (pair, event_ts == bar_time).
    """
    out = vol_df.with_columns(
        pl.col("bar_time").cast(pl.Datetime("ns")),
    ).select(_OUT_COLUMNS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(out_path)
    return out_path


def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="v4.11 vol estimator (CLASS-01/02, Addendum 2)"
    )
    p.add_argument("--ohlc", type=pathlib.Path, required=False)
    p.add_argument("--out", type=pathlib.Path, default=_DEFAULT_OUT)
    p.add_argument("--lookback-bars", type=int, default=90)
    return p


if __name__ == "__main__":  # pragma: no cover
    args = _build_cli().parse_args()
    if args.ohlc is None or not args.ohlc.exists():
        raise SystemExit("--ohlc required (no default real-data fixture in v4.11)")
    ohlc = pl.read_parquet(args.ohlc)
    vol = build_pooled_vol_frame(ohlc, args.lookback_bars)
    vol = assign_buckets(vol)
    emit_vol_per_slot_parquet(vol, args.out)
