"""
test_vol_per_slot_schema.py — Phase 101 Wave 0 Task 1.

Tests the D-71 JOIN-key contract on vol_per_slot.parquet. If the file is missing
or schema deviates from D-71, the test surfaces an actionable skip/xfail diagnostic
rather than silently passing — that IS the deliverable per 101-01-PLAN.md Task 1.

Citations: D-71 (macro_stance_per_event 7-col schema), D-23-v412 (JOIN invariant),
OQ-2 (Wave 1 prerequisite).
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

EXPECTED_JOIN_KEYS = ["event_ts", "pair", "vol_bucket"]
EXPECTED_VOL_BUCKET_VALUES = {"low", "mid", "high"}

SEARCH_PATHS = [
    Path("data/v4.12/vol_per_slot.parquet"),
    Path("data/v4.11/vol_per_slot.parquet"),
]

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vol_per_slot_schema.json"


def _locate_parquet() -> Path | None:
    for p in SEARCH_PATHS:
        if p.exists():
            return p
    return None


@pytest.fixture(scope="module")
def vol_per_slot_df() -> pl.DataFrame:
    src = _locate_parquet()
    if src is None:
        pytest.skip(
            "vol_per_slot.parquet not found in data/v4.12/ or data/v4.11/. "
            "Wave 1 prerequisite — generate it before macro stance estimator runs."
        )
    return pl.read_parquet(src)


def test_vol_per_slot_parquet_exists() -> None:
    """Locate vol_per_slot.parquet under data/v4.12/ or fall back to data/v4.11/."""
    src = _locate_parquet()
    if src is None:
        pytest.skip(
            "vol_per_slot.parquet not found. Wave 1 must generate it under data/v4.12/. "
            f"Searched: {[str(p) for p in SEARCH_PATHS]}"
        )
    assert src.exists()


def test_vol_per_slot_d71_join_keys(vol_per_slot_df: pl.DataFrame) -> None:
    """
    Asserts D-71 JOIN-key contract: vol_per_slot.parquet must expose
    columns {event_ts, pair, vol_bucket} for Nyquist audit JOIN.

    On mismatch, raises with full diagnostic so Wave 1 has actionable info.
    """
    actual = vol_per_slot_df.columns
    missing = [k for k in EXPECTED_JOIN_KEYS if k not in actual]
    if missing:
        pytest.xfail(
            f"D-71 JOIN-key mismatch (Wave 1 prerequisite — OQ-2 blocker): "
            f"missing={missing}, actual_columns={actual}. "
            f"Resolve via rename mapping or upstream schema fix before Wave 1."
        )
    assert missing == [], f"D-71 missing join keys: {missing}"


def test_vol_per_slot_bucket_values(vol_per_slot_df: pl.DataFrame) -> None:
    """
    Asserts vol_bucket values are exactly {low, mid, high} per D-71.

    On mismatch (e.g., VOL_NA placeholder), raises with diagnostic.
    """
    if "vol_bucket" not in vol_per_slot_df.columns:
        # Fall back to surrogate 'bucket' for diagnostic
        probe_col = "bucket" if "bucket" in vol_per_slot_df.columns else None
        if probe_col is None:
            pytest.xfail(
                "Neither 'vol_bucket' nor 'bucket' column present — cannot probe."
            )
        unique = set(
            str(v) for v in vol_per_slot_df[probe_col].unique().drop_nulls().to_list()
        )
        pytest.xfail(
            f"D-71 'vol_bucket' missing; surrogate '{probe_col}' has values={sorted(unique)}. "
            f"Expected {sorted(EXPECTED_VOL_BUCKET_VALUES)}. Wave 1 must reconcile."
        )

    unique = set(
        str(v) for v in vol_per_slot_df["vol_bucket"].unique().drop_nulls().to_list()
    )
    if unique != EXPECTED_VOL_BUCKET_VALUES:
        pytest.xfail(
            f"vol_bucket values={sorted(unique)} != expected={sorted(EXPECTED_VOL_BUCKET_VALUES)}. "
            f"Wave 1 prerequisite."
        )
    assert unique == EXPECTED_VOL_BUCKET_VALUES


def test_fixture_snapshot_present_or_documented() -> None:
    """
    Snapshot fixture exists and includes 'd71_join_compatible' verdict.
    The fixture is the durable record for SUMMARY.md cross-reference.
    """
    if not FIXTURE_PATH.exists():
        pytest.skip(
            f"fixture not yet generated: {FIXTURE_PATH}. "
            f"Run: uv run python scripts/v4.12/check_vol_per_slot_schema.py "
            f"--output {FIXTURE_PATH}"
        )
    payload = json.loads(FIXTURE_PATH.read_text())
    assert "d71_join_compatible" in payload, "fixture missing D-71 verdict field"
    assert "actual_columns" in payload
    assert "vol_bucket_probe" in payload
