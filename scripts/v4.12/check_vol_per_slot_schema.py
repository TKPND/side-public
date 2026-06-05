"""
check_vol_per_slot_schema.py — Phase 101 Wave 0 Task 1: vol_per_slot.parquet schema audit.

Resolves OQ-2: confirms (or surfaces mismatch on) D-71 JOIN-key compatibility for
Nyquist audit — JOIN keys = event_ts × pair × vol_bucket, vol_bucket ∈ {low, mid, high}.

Searches data/v4.12/ first, falls back to data/v4.11/. Emits canonical JSON schema
report and exits with structured diagnostic on D-71 mismatch.

Usage:
    uv run python scripts/v4.12/check_vol_per_slot_schema.py \
        --output scripts/v4.12/tests/fixtures/vol_per_slot_schema.json

    --strict : exit 1 on D-71 mismatch (default: exit 0 with diagnostic in JSON)

Exit codes:
    0  : success (schema dumped, optional D-71 OK)
    1  : --strict + D-71 mismatch
    2  : data file not found in any expected location

Citations: D-71 (macro_stance_per_event 7-col schema), D-23-v412 (JOIN-key invariant),
101-01-PLAN.md Task 1, n_per_cell_audit.py (pattern reference).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import polars as pl
except ImportError:
    print("ERROR: polars is not installed. Run: uv add polars", file=sys.stderr)
    sys.exit(1)

# ── D-71 expected JOIN-key contract ───────────────────────────────────────
EXPECTED_JOIN_KEYS: list[str] = ["event_ts", "pair", "vol_bucket"]
EXPECTED_VOL_BUCKET_VALUES: set[str] = {"low", "mid", "high"}

SEARCH_PATHS: list[Path] = [
    Path("data/v4.12/vol_per_slot.parquet"),
    Path("data/v4.11/vol_per_slot.parquet"),
]


def _locate() -> Path:
    """Find vol_per_slot.parquet across data/v4.12/ → data/v4.11/."""
    for p in SEARCH_PATHS:
        if p.exists():
            return p
    print(
        "ERROR: vol_per_slot.parquet not found in any of:\n  "
        + "\n  ".join(str(p) for p in SEARCH_PATHS),
        file=sys.stderr,
    )
    sys.exit(2)


def _diagnose(df: pl.DataFrame, source: Path) -> dict:
    """
    Build canonical schema report + D-71 mismatch diagnostic.
    Schema dict keys are sorted for sha256-stable JSON.
    """
    actual_columns = df.columns
    actual_dtypes = {col: str(df[col].dtype) for col in actual_columns}

    missing_keys = [k for k in EXPECTED_JOIN_KEYS if k not in actual_columns]
    d71_compatible = len(missing_keys) == 0

    # Probe vol_bucket values (or surrogate column 'bucket' if present)
    vol_bucket_probe: dict[str, object] = {}
    probe_col = (
        "vol_bucket"
        if "vol_bucket" in actual_columns
        else ("bucket" if "bucket" in actual_columns else None)
    )
    if probe_col is not None:
        unique_vals = sorted(
            str(v) for v in df[probe_col].unique().drop_nulls().to_list()
        )
        vol_bucket_probe = {
            "probed_column": probe_col,
            "unique_values": unique_vals,
            "matches_expected_set": set(unique_vals) == EXPECTED_VOL_BUCKET_VALUES,
        }
    else:
        vol_bucket_probe = {
            "probed_column": None,
            "unique_values": [],
            "matches_expected_set": False,
        }

    return {
        "source_path": str(source),
        "row_count": df.height,
        "actual_columns": actual_columns,
        "actual_dtypes": actual_dtypes,
        "expected_join_keys": EXPECTED_JOIN_KEYS,
        "expected_vol_bucket_values": sorted(EXPECTED_VOL_BUCKET_VALUES),
        "missing_join_keys": missing_keys,
        "d71_join_compatible": d71_compatible,
        "vol_bucket_probe": vol_bucket_probe,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="check_vol_per_slot_schema.py — D-71 JOIN-key audit"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSON path (e.g., scripts/v4.12/tests/fixtures/vol_per_slot_schema.json)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on D-71 mismatch (default: exit 0, diagnostic in JSON)",
    )
    args = parser.parse_args(argv)

    src = _locate()
    df = pl.read_parquet(src)
    report = _diagnose(df, src)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True))

    status = "OK" if report["d71_join_compatible"] else "MISMATCH"
    print(f"[{status}] wrote {args.output} (source={src}, rows={report['row_count']})")

    if args.strict and not report["d71_join_compatible"]:
        print(
            f"D-71 MISMATCH: missing_join_keys={report['missing_join_keys']}\n"
            f"  actual_columns={report['actual_columns']}\n"
            f"  vol_bucket_probe={report['vol_bucket_probe']}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
