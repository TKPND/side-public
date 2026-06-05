"""CLASS-04: Post-JOIN Nyquist audit + null-ship-v3 kill-switch.

D-25 n_eff = simple count (per-bucket raw row count after JOIN)
D-26 Independent script (not merged into vol_estimator.py)
D-27 VOL_HIGH kill-switch: n_min<20 OR n_eff<4 -> main() return 1.
     VOL_MID/VOL_LOW WARNING only (audit-trail, no sys.exit).
D-28/D-29 93-VALIDATION.md = Markdown + trailing ```json code block hybrid.
D-07 spike_001_diagnostic: VOL_HIGH pass_count in [10, 14] -> warning true.
D-32 SEAL read-only.
D-33 vol_per_slot.parquet schema = (pair, bar_time, atr_14, rolling_quantile_low,
     rolling_quantile_high, bucket, vol_input_ts) -- NO cell_id column.
D-34 bucket values + per_bucket JSON keys = VOL_LOW / VOL_MID / VOL_HIGH (prefix).
D-35 flat import: from seal_drift_check import ... (scripts/v4.11 is dot-in-dir).
D-36 n_min = post-JOIN joined.filter(bucket==b)["cell_id"].n_unique();
     vol_df x slot_labels_df inner JOIN on (pair, bar_time == pair, event_ts).
     Matches Phase 92 pre-reg calibration (864 rows / 6 cell) bit-exact.
D-17 SEAL threshold (n_min<20, n_eff<4) untouched -- module-level literals.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Any

import polars as pl

# D-35 flat import (scripts/v4.11 is dot-in-dir -> NOT a Python package).
from seal_drift_check import (  # type: ignore[import-not-found]
    SEAL_DIR_DEFAULT,
    SIGNAL_COMMIT_V411_EXPECTED,
    verify_seal_or_raise,
)

# D-17/D-22/D-32 import-time fail-close
verify_seal_or_raise()

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEFAULT_VOL_PARQUET = _REPO_ROOT / "data" / "v4.11" / "vol_per_slot.parquet"
_DEFAULT_SLOT_LABELS_PARQUET = _REPO_ROOT / "data" / "slot_labels.parquet"
_DEFAULT_VALIDATION_MD = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "93-vol-precompute-classifier-nyquist-audit"
    / "93-VALIDATION.md"
)
_ENGINE_COMMIT = "a5a1102"  # Sealed Anchor (STATE.md D-16 carry-over)

# D-34: VOL_ prefix keys for per_bucket JSON.
_BUCKET_LOW = "VOL_LOW"
_BUCKET_MID = "VOL_MID"
_BUCKET_HIGH = "VOL_HIGH"
_BUCKETS: list[str] = [_BUCKET_LOW, _BUCKET_MID, _BUCKET_HIGH]

# D-17 SEAL threshold untouched (module-level literal; filter_spec.json raw strings
# "HIGH_bucket_n_min < 20" are free-form -- these literals are the truth).
# post-JOIN cell_id.n_unique() matches Phase 92 pre-reg calibration (864rows/6cell)
# bit-exact -- D-17 non-infringement verified in CONTEXT.md D-36.
_N_MIN_THR: int = 20
_N_EFF_THR: int = 4

# D-07 spike_001_diagnostic trigger window
_SPIKE_001_LOW_BOUND: int = 10
_SPIKE_001_HIGH_BOUND: int = 14

# Marker pair for 93-VALIDATION.md trailing JSON block (D-28).
_JSON_BLOCK_MARKER_START = "<!-- NYQUIST_AUDIT_JSON_BEGIN -->"
_JSON_BLOCK_MARKER_END = "<!-- NYQUIST_AUDIT_JSON_END -->"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_filter_spec(seal_dir: pathlib.Path = SEAL_DIR_DEFAULT) -> dict[str, Any]:
    """Read filter_spec.json read-only (D-32)."""
    return json.loads((seal_dir / "filter_spec.json").read_text(encoding="utf-8"))


def _join_vol_with_labels(
    vol_df: pl.DataFrame,
    slot_labels_df: pl.DataFrame,
) -> pl.DataFrame:
    """D-36 post-JOIN: inner join on (pair, bar_time == pair, event_ts).

    Returns a frame containing 'bucket' from vol_df and 'cell_id' from
    slot_labels_df (plus any other slot_labels columns).

    Raises
    ------
    AssertionError
        If the JOIN produces zero rows (indicates D-33 schema mismatch --
        caller must fix the key columns before calling this function).
    """
    joined = vol_df.join(
        slot_labels_df,
        left_on=["pair", "bar_time"],
        right_on=["pair", "event_ts"],
        how="inner",
    )
    assert len(joined) > 0, (
        "D-33 schema mismatch: vol_df x slot_labels_df inner JOIN on "
        "(pair, bar_time) == (pair, event_ts) produced 0 rows. "
        "Verify that column names and dtypes align."
    )
    return joined


def _per_bucket_stats(joined: pl.DataFrame) -> dict[str, dict[str, int]]:
    """Compute n_min (distinct cell_id) and n_eff (row count) per bucket.

    D-36: n_min = joined.filter(bucket==b)["cell_id"].n_unique()
    D-25: n_eff = joined.filter(bucket==b).height (simple raw count)
    """
    stats: dict[str, dict[str, int]] = {}
    for b in _BUCKETS:
        subset = joined.filter(pl.col("bucket") == b)
        n_min = subset["cell_id"].n_unique() if subset.height > 0 else 0
        n_eff = subset.height
        stats[b] = {"n_min": n_min, "n_eff": n_eff}
    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_nyquist_audit(
    vol_per_slot: pl.DataFrame,
    slot_labels_df: pl.DataFrame,  # D-36 post-JOIN cell_id source
    filter_spec: dict[str, Any],
    engine_commit: str = _ENGINE_COMMIT,
) -> dict[str, Any]:
    """Execute post-JOIN Nyquist audit and return result dict.

    Parameters
    ----------
    vol_per_slot:
        D-33 schema: (pair, bar_time, atr_14, rolling_quantile_low,
        rolling_quantile_high, bucket, vol_input_ts). No cell_id column.
    slot_labels_df:
        Must contain columns: pair, event_ts, cell_id.
    filter_spec:
        Loaded from SEAL filter_spec.json (read-only, D-32).
    engine_commit:
        Sealed engine commit hash (default = Sealed Anchor "a5a1102").

    Returns
    -------
    dict
        Keys: per_bucket, kill_switch_fired, kill_switch_reason,
        nyquist_compliant, spike_001_diagnostic_warning,
        signal_commit_v411, engine_commit, timestamp_utc,
        vol_input_ts_range.

    Raises
    ------
    AssertionError
        If the inner JOIN yields 0 rows (D-33 schema mismatch).
    """
    # D-36 post-JOIN
    joined = _join_vol_with_labels(vol_per_slot, slot_labels_df)

    # Per-bucket n_min / n_eff
    per_bucket = _per_bucket_stats(joined)

    # D-27: VOL_HIGH kill-switch (n_min < _N_MIN_THR OR n_eff < _N_EFF_THR)
    high = per_bucket[_BUCKET_HIGH]
    kill_switch_fired = (high["n_min"] < _N_MIN_THR) or (high["n_eff"] < _N_EFF_THR)
    kill_switch_reason: str | None = None
    if kill_switch_fired:
        reasons = []
        if high["n_min"] < _N_MIN_THR:
            reasons.append(f"n_min={high['n_min']}<{_N_MIN_THR}")
        if high["n_eff"] < _N_EFF_THR:
            reasons.append(f"n_eff={high['n_eff']}<{_N_EFF_THR}")
        kill_switch_reason = f"VOL_HIGH bucket: {' OR '.join(reasons)}"

    nyquist_compliant = not kill_switch_fired

    # D-07 spike_001_diagnostic: VOL_HIGH pass_count in [10, 14]
    spike_diag_enabled = filter_spec.get("spike_001_diagnostic", {}).get(
        "enabled", False
    )
    spike_001_diagnostic_warning = spike_diag_enabled and (
        _SPIKE_001_LOW_BOUND <= high["n_min"] <= _SPIKE_001_HIGH_BOUND
    )

    # vol_input_ts range
    ts_col = vol_per_slot["vol_input_ts"].drop_nulls()
    if ts_col.len() > 0:
        ts_min = str(ts_col.min())
        ts_max = str(ts_col.max())
    else:
        ts_min = ts_max = None

    return {
        "nyquist_compliant": nyquist_compliant,
        "per_bucket": per_bucket,
        "kill_switch_fired": kill_switch_fired,
        "kill_switch_reason": kill_switch_reason,
        "spike_001_diagnostic_warning": spike_001_diagnostic_warning,
        "signal_commit_v411": SIGNAL_COMMIT_V411_EXPECTED,
        "engine_commit": engine_commit,
        "timestamp_utc": datetime.now(tz=timezone.utc).isoformat(),
        "vol_input_ts_range": {"min": ts_min, "max": ts_max},
    }


def emit_validation_md(result: dict[str, Any], out_path: pathlib.Path) -> None:
    """Write audit result into 93-VALIDATION.md.

    Behaviour (D-28):
    1. Updates YAML frontmatter `nyquist_compliant:` to match result.
    2. Replaces the trailing JSON block between HTML comment markers
       with the canonical JSON (jq -cS style, indented for readability).
    3. Preserves all other content in the file verbatim.

    Raises
    ------
    ValueError
        If the marker pair is not found in the existing file content
        (the file must have been seeded with marker stubs).
    """
    existing = out_path.read_text(encoding="utf-8")

    # 1. Update YAML frontmatter `nyquist_compliant:`
    compliant_str = "true" if result["nyquist_compliant"] else "false"
    updated = re.sub(
        r"^(nyquist_compliant:\s*).*$",
        rf"\g<1>{compliant_str}",
        existing,
        flags=re.MULTILINE,
    )

    # 2. Replace JSON block between markers
    if _JSON_BLOCK_MARKER_START not in updated:
        raise ValueError(
            f"Marker '{_JSON_BLOCK_MARKER_START}' not found in {out_path}. "
            "Seed the file with marker stubs before calling emit_validation_md()."
        )

    # Build the JSON payload (D-29 required fields, VOL_ prefix keys).
    payload = {
        "nyquist_compliant": result["nyquist_compliant"],
        "per_bucket": result["per_bucket"],
        "kill_switch_fired": result["kill_switch_fired"],
        "kill_switch_reason": result["kill_switch_reason"],
        "spike_001_diagnostic_warning": result["spike_001_diagnostic_warning"],
        "signal_commit_v411": result["signal_commit_v411"],
        "engine_commit": result["engine_commit"],
        "timestamp_utc": result["timestamp_utc"],
        "vol_input_ts_range": result["vol_input_ts_range"],
    }
    json_str = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    new_block = (
        f"{_JSON_BLOCK_MARKER_START}\n"
        f"```json\n{json_str}\n```\n"
        f"{_JSON_BLOCK_MARKER_END}"
    )

    # Replace everything between (and including) the markers.
    pattern = (
        re.escape(_JSON_BLOCK_MARKER_START) + r".*?" + re.escape(_JSON_BLOCK_MARKER_END)
    )
    updated = re.sub(pattern, new_block, updated, flags=re.DOTALL)

    out_path.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns
    -------
    int
        0 if nyquist_compliant, 1 if kill-switch fired.
    """
    parser = argparse.ArgumentParser(
        description="Phase 93 Nyquist audit + null-ship-v3 kill-switch (CLASS-04)"
    )
    parser.add_argument(
        "--vol-parquet",
        type=pathlib.Path,
        default=_DEFAULT_VOL_PARQUET,
        help="Path to vol_per_slot.parquet (D-33 schema)",
    )
    parser.add_argument(
        "--slot-labels-parquet",
        type=pathlib.Path,
        default=_DEFAULT_SLOT_LABELS_PARQUET,
        help="Path to slot_labels.parquet (D-36 cell_id source)",
    )
    parser.add_argument(
        "--validation-md",
        type=pathlib.Path,
        default=_DEFAULT_VALIDATION_MD,
        help="Path to 93-VALIDATION.md to update",
    )
    parser.add_argument(
        "--seal-dir",
        type=pathlib.Path,
        default=SEAL_DIR_DEFAULT,
        help="Path to Phase 92 SEAL directory (read-only)",
    )
    args = parser.parse_args(argv)

    filter_spec = _load_filter_spec(args.seal_dir)

    vol_df = pl.read_parquet(args.vol_parquet)
    slot_labels_df = pl.read_parquet(args.slot_labels_parquet)

    result = run_nyquist_audit(vol_df, slot_labels_df, filter_spec)

    # Emit VALIDATION.md
    emit_validation_md(result, args.validation_md)

    # Summary to stdout
    print(
        f"[nyquist_audit] VOL_HIGH n_min={result['per_bucket'][_BUCKET_HIGH]['n_min']} "
        f"n_eff={result['per_bucket'][_BUCKET_HIGH]['n_eff']} "
        f"kill_switch={result['kill_switch_fired']} "
        f"nyquist_compliant={result['nyquist_compliant']}"
    )

    if result["kill_switch_fired"]:
        print(f"[nyquist_audit] KILL-SWITCH FIRED: {result['kill_switch_reason']}")
        print(
            "[nyquist_audit] null-ship-v3 path active — "
            "do not proceed to Phase 94/95 without resolving this."
        )
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
