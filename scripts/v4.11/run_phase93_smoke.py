"""Phase 93 integration smoke runner (Wave 3, Plan 04).

Provides a single E2E CLI entry-point that exercises vol_estimator +
nyquist_audit_v411 with synthetic slot_labels (3-mode) to confirm
kill-switch fires/no-fires as expected.

D-35 flat imports: scripts/v4.11 is dot-in-dir -> no package import.
D-36 n_min = post-JOIN cell_id.n_unique().
D-33 JOIN on (pair, bar_time) == (pair, event_ts).
D-34 bucket keys VOL_LOW / VOL_MID / VOL_HIGH.
D-17 _N_MIN_THR=20 / _N_EFF_THR=4 untouched.

slot-labels-mode choices:
  synthetic-pass  : VOL_HIGH gets 24 distinct cell_id -> n_min>=20 -> no-kill
  synthetic-kill  : VOL_HIGH gets 6 distinct cell_id  -> n_min<20  -> kill fires
  real            : loads data/slot_labels.parquet (debug path; may fail if
                    schema lacks event_ts column)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl

# D-35 flat imports (scripts/v4.11 cannot be a Python package).
from seal_drift_check import SEAL_DIR_DEFAULT, verify_seal_or_raise  # type: ignore[import-not-found]
from vol_estimator import (  # type: ignore[import-not-found]
    assign_buckets,
    build_pooled_vol_frame,
    emit_vol_per_slot_parquet,
)
from nyquist_audit_v411 import (  # type: ignore[import-not-found]
    _load_filter_spec,
    run_nyquist_audit,
    emit_validation_md,
    _DEFAULT_VALIDATION_MD,
)

# D-17/D-22/D-32 import-time fail-close (also called inside vol_estimator/nyquist_audit).
verify_seal_or_raise()

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_VOL_PARQUET = _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet"
_DEFAULT_SLOT_LABELS_PARQUET = _REPO_ROOT / "data" / "slot_labels.parquet"


# ---------------------------------------------------------------------------
# Synthetic OHLC builder
# ---------------------------------------------------------------------------


def build_synthetic_ohlc(
    pairs: list[str] | None = None,
    n_bars: int = 120,
    start: date | None = None,
    seed: int = 42,
) -> pl.DataFrame:
    """Build deterministic synthetic OHLC data (D-30 spec).

    Parameters
    ----------
    pairs:
        Pair symbols. Default: ["EURUSD", "USDJPY"].
    n_bars:
        Number of daily bars per pair. Default 120 (>14 warmup + enough for quantile).
    start:
        Start date. Default 2024-01-01.
    seed:
        RNG seed. Default 42 (matches conftest fixture).

    Returns
    -------
    pl.DataFrame with columns [pair, bar_time, open, high, low, close].
    """
    if pairs is None:
        pairs = ["EURUSD", "USDJPY"]
    if start is None:
        start = date(2024, 1, 1)

    rng = np.random.default_rng(seed)
    dates = [start + timedelta(days=i) for i in range(n_bars)]
    frames: list[pl.DataFrame] = []
    for pair in pairs:
        close = np.cumprod(1 + rng.normal(0, 0.001, n_bars))
        spread = rng.uniform(0.0005, 0.003, n_bars)
        high = close * (1 + spread / 2)
        low = close * (1 - spread / 2)
        open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
        frames.append(
            pl.DataFrame(
                {
                    "pair": [pair] * n_bars,
                    "bar_time": dates,
                    "open": open_.tolist(),
                    "high": high.tolist(),
                    "low": low.tolist(),
                    "close": close.tolist(),
                }
            )
        )
    return pl.concat(frames)


# ---------------------------------------------------------------------------
# Synthetic slot_labels builder
# ---------------------------------------------------------------------------


def build_synthetic_slot_labels_df(
    ohlc_df: pl.DataFrame,
    n_distinct_high: int = 24,
    seed: int = 0,
) -> pl.DataFrame:
    """Build synthetic slot_labels_df aligned to ohlc_df bar_time range.

    D-36: slot_labels_df must have columns [pair, event_ts, cell_id].
    The JOIN is vol.bar_time == slot_labels.event_ts.

    Strategy:
      - Take every bar_time from ohlc_df that has a valid (non-warmup) atr_14.
      - Assign cell_id cycling through n_distinct_high distinct values.

    Parameters
    ----------
    ohlc_df:
        Synthetic OHLC; used to extract bar_times for alignment.
    n_distinct_high:
        Number of distinct cell_id values. 24 -> n_min>=20 (pass).
        6 -> n_min=6<20 (kill fires).
    seed:
        RNG seed for reproducibility.

    Returns
    -------
    pl.DataFrame with columns [pair, event_ts, cell_id] (Datetime["us"]).
    """
    rng = np.random.default_rng(seed)
    pairs = ohlc_df["pair"].unique().sort().to_list()
    n_bars = ohlc_df.filter(pl.col("pair") == pairs[0]).height

    # warmup = 14 bars (ATR-14 requires 14 bars); take bars from index 14 onward.
    warmup = 14
    dates_per_pair = (
        ohlc_df.filter(pl.col("pair") == pairs[0])
        .sort("bar_time")["bar_time"]
        .to_list()
    )
    valid_dates = dates_per_pair[warmup:]

    frames: list[pl.DataFrame] = []
    for pair in pairs:
        n = len(valid_dates)
        # Cycle through cell_id values (deterministic, not random).
        cell_ids = [f"cell_{i % n_distinct_high:03d}" for i in range(n)]
        frames.append(
            pl.DataFrame(
                {
                    "pair": [pair] * n,
                    "event_ts": valid_dates,
                    "cell_id": cell_ids,
                }
            )
        )
    result = pl.concat(frames)
    # Ensure event_ts dtype matches bar_time in vol DataFrame (Date).
    # ohlc bar_time is Date; vol_estimator keeps bar_time as Date in-memory
    # (emit_vol_per_slot_parquet casts to Datetime["ns"] only for the parquet file).
    # The JOIN uses in-memory vol, so event_ts must match Date dtype.
    result = result.with_columns(
        pl.col("event_ts").cast(pl.Date)
    )
    return result


# ---------------------------------------------------------------------------
# Slot-labels resolver (3-mode)
# ---------------------------------------------------------------------------


def _resolve_slot_labels(
    mode: str,
    ohlc_df: pl.DataFrame,
    real_path: pathlib.Path,
) -> pl.DataFrame | None:
    """Return slot_labels_df for the given mode, or None on known failure.

    Parameters
    ----------
    mode:
        "synthetic-pass", "synthetic-kill", or "real".
    ohlc_df:
        Synthetic OHLC (used for synthetic modes to align bar_times).
    real_path:
        Path to real slot_labels.parquet (used in "real" mode only).

    Returns
    -------
    pl.DataFrame or None
        Returns None if mode=="real" and schema is incompatible (missing
        event_ts column). Caller logs the issue as a known deviation.
    """
    if mode == "synthetic-pass":
        return build_synthetic_slot_labels_df(ohlc_df, n_distinct_high=24)
    elif mode == "synthetic-kill":
        return build_synthetic_slot_labels_df(ohlc_df, n_distinct_high=6)
    elif mode == "real":
        if not real_path.exists():
            print(
                f"[WARN] real slot_labels not found at {real_path}; skipping real mode.",
                file=sys.stderr,
            )
            return None
        df = pl.read_parquet(real_path)
        if "event_ts" not in df.columns:
            print(
                "[WARN] real slot_labels.parquet lacks 'event_ts' column "
                f"(schema: {df.columns}). "
                "Real mode is a debug path; this dataset is a static lookup grid "
                "without timestamps. kill_switch_fired cannot be evaluated. "
                "Logging as known deviation: real-data-no-event-ts.",
                file=sys.stderr,
            )
            return None
        return df
    else:
        raise ValueError(f"Unknown slot-labels-mode: {mode!r}")


# ---------------------------------------------------------------------------
# Main smoke runner
# ---------------------------------------------------------------------------


def run_smoke(
    mode: str = "synthetic-pass",
    vol_out: pathlib.Path = _DEFAULT_VOL_PARQUET,
    validation_md: pathlib.Path = _DEFAULT_VALIDATION_MD,
    real_slot_labels: pathlib.Path = _DEFAULT_SLOT_LABELS_PARQUET,
    seal_dir: pathlib.Path = SEAL_DIR_DEFAULT,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute E2E smoke run for the given slot_labels mode.

    Returns the audit result dict (same shape as run_nyquist_audit output).
    Raises SystemExit(0) (no-kill) or SystemExit(1) (kill fires) when
    called from __main__. When called as library function, returns dict.

    Parameters
    ----------
    mode:
        "synthetic-pass" | "synthetic-kill" | "real"
    vol_out:
        Destination path for vol_per_slot.parquet.
    validation_md:
        Destination path for 93-VALIDATION.md update.
    real_slot_labels:
        Path to real slot_labels.parquet (used only in "real" mode).
    seal_dir:
        SEAL directory (read-only).
    verbose:
        Print progress to stdout.

    Returns
    -------
    dict with keys: kill_switch_fired, kill_switch_reason, per_bucket,
    nyquist_compliant, spike_001_diagnostic_warning, signal_commit_v411,
    engine_commit, timestamp_utc, vol_input_ts_range.
    Also includes synthetic key: mode, slot_labels_mode_used.
    """

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    _log(f"[smoke] mode={mode}")

    # 1. Build synthetic OHLC.
    ohlc = build_synthetic_ohlc()
    _log(f"[smoke] ohlc shape: {ohlc.shape}")

    # 2. Compute vol.
    vol = build_pooled_vol_frame(ohlc)
    vol = assign_buckets(vol)
    _log(f"[smoke] vol shape: {vol.shape}, buckets: {vol['bucket'].value_counts().sort('bucket').to_dict(as_series=False)}")

    # 3. Emit parquet (to vol_out path).
    emit_vol_per_slot_parquet(vol, vol_out)
    _log(f"[smoke] vol parquet emitted -> {vol_out}")

    # 4. Resolve slot_labels.
    slot_labels = _resolve_slot_labels(mode, ohlc, real_slot_labels)
    if slot_labels is None:
        # Known deviation: real mode with incompatible schema.
        result: dict[str, Any] = {
            "mode": mode,
            "slot_labels_mode_used": mode,
            "kill_switch_fired": None,
            "kill_switch_reason": "DEVIATION: real slot_labels lacks event_ts column; cannot evaluate",
            "per_bucket": {},
            "nyquist_compliant": None,
            "spike_001_diagnostic_warning": False,
            "signal_commit_v411": "N/A",
            "engine_commit": "N/A",
            "timestamp_utc": "N/A",
            "vol_input_ts_range": {"min": "N/A", "max": "N/A"},
        }
        _log(f"[smoke] real mode deviation: {result['kill_switch_reason']}")
        return result

    _log(f"[smoke] slot_labels shape: {slot_labels.shape}")

    # 5. Load filter_spec (read-only, D-32).
    filter_spec = _load_filter_spec(seal_dir)

    # 6. Run Nyquist audit (D-36 post-JOIN).
    result = run_nyquist_audit(vol, slot_labels, filter_spec)
    result["mode"] = mode
    result["slot_labels_mode_used"] = mode

    _log(
        f"[smoke] kill_switch_fired={result['kill_switch_fired']} "
        f"reason={result.get('kill_switch_reason')}"
    )
    _log(
        f"[smoke] per_bucket={result['per_bucket']}"
    )

    # 7. Emit validation MD (only for the default validation_md path; tests pass tmp path).
    if validation_md.exists():
        emit_validation_md(result, validation_md)
        _log(f"[smoke] 93-VALIDATION.md updated -> {validation_md}")
    else:
        _log(f"[smoke] validation_md not found at {validation_md}; skipping emit.")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Phase 93 integration smoke runner (Wave 3)"
    )
    p.add_argument(
        "--slot-labels-mode",
        choices=["synthetic-pass", "synthetic-kill", "real"],
        default="synthetic-pass",
        help="slot_labels mode (default: synthetic-pass)",
    )
    p.add_argument(
        "--vol-out",
        type=pathlib.Path,
        default=_DEFAULT_VOL_PARQUET,
        help="Output path for vol_per_slot.parquet",
    )
    p.add_argument(
        "--validation-md",
        type=pathlib.Path,
        default=_DEFAULT_VALIDATION_MD,
        help="Path to 93-VALIDATION.md for JSON block update",
    )
    p.add_argument(
        "--real-slot-labels",
        type=pathlib.Path,
        default=_DEFAULT_SLOT_LABELS_PARQUET,
        help="Path to real slot_labels.parquet (used only in real mode)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    return p


if __name__ == "__main__":  # pragma: no cover
    args = _build_cli().parse_args()
    result = run_smoke(
        mode=args.slot_labels_mode,
        vol_out=args.vol_out,
        validation_md=args.validation_md,
        real_slot_labels=args.real_slot_labels,
        verbose=args.verbose,
    )
    fired = result.get("kill_switch_fired")
    if fired is None:
        print(f"[smoke] DEVIATION mode={result['mode']}: {result['kill_switch_reason']}")
        sys.exit(2)
    elif fired:
        print(
            f"[smoke] KILL-SWITCH FIRED mode={result['mode']}: "
            f"{result.get('kill_switch_reason')}"
        )
        sys.exit(1)
    else:
        print(f"[smoke] PASS (no-kill) mode={result['mode']}")
        sys.exit(0)
