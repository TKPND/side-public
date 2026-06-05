"""test_parquet_schema.py — Phase 101 Wave 2 Plan 101-05 Task 1 (5 GREEN).

D-71 7-column schema enforcement on `data/v4.12/macro_stance_per_event.parquet`.

Replaces the 3 xfail skeletons from Plan 101-01 Wave 0 Task 2.

Citations:
    CONTEXT.md L87-96 — D-71 schema (7 cols, dtypes, kill_set bool, model_version short8)
    CONTEXT.md L98-102 — D-72 label_provenance enum
    CONTEXT.md L121 — 2024-Q1 4 events × 4 pairs = 16 rows (cardinality authoritative)
    CLASS-V412-02 — D-71 schema enforcement
    101-05-PLAN.md Task 1 — emit + 5 GREEN tests
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

D71_COLUMNS: list[str] = [
    "event_ts",
    "pair",
    "central_bank",
    "stance",
    "kill_set",
    "label_provenance",
    "model_version",
]

D72_LABEL_PROVENANCE: frozenset[str] = frozenset(
    {
        "frozen-llm-once-prosusai-finbert",
        "frozen-llm-once-roberta-base",
    }
)

PARQUET_PATH = Path("data/v4.12/macro_stance_per_event.parquet")
VOL_PER_SLOT_PATH = Path("data/v4.11/vol_per_slot.parquet")


@pytest.fixture(scope="module")
def df() -> pl.DataFrame:
    if not PARQUET_PATH.exists():
        pytest.fail(f"missing artifact: {PARQUET_PATH}")
    return pl.read_parquet(PARQUET_PATH)


def test_macro_stance_per_event_schema_d71_columns(df: pl.DataFrame) -> None:
    """D-71 schema = exactly 7 columns in canonical order (CONTEXT L87-96)."""
    assert df.columns == D71_COLUMNS, f"expected {D71_COLUMNS}, got {df.columns}"
    # Q1 cardinality (CONTEXT L121): 4 events × 4 pairs = 16.
    assert df.height == 16, f"expected 16 rows (4 Q1 events × 4 pairs), got {df.height}"


def test_dtypes_d71(df: pl.DataFrame) -> None:
    """D-71 dtypes: event_ts Datetime[ns,UTC], kill_set Boolean, others Utf8/String."""
    schema = dict(zip(df.columns, df.dtypes))
    assert schema["event_ts"] == pl.Datetime(time_unit="ns", time_zone="UTC")
    assert schema["pair"] == pl.Utf8
    assert schema["central_bank"] == pl.Utf8
    assert schema["stance"] == pl.Utf8
    assert schema["kill_set"] == pl.Boolean
    assert schema["label_provenance"] == pl.Utf8
    assert schema["model_version"] == pl.Utf8


def test_stance_values_in_taxonomy(df: pl.DataFrame) -> None:
    """stance ⊆ {HAWK, DOV, NEUT} (D-69 macro stance taxonomy)."""
    unique = set(df["stance"].drop_nulls().to_list())
    assert unique <= {"HAWK", "DOV", "NEUT"}, f"unexpected stance values: {unique}"
    # kill_set MUST be the indicator function of (stance == NEUT).
    derived = df.with_columns((pl.col("stance") == "NEUT").alias("_kd"))
    mismatch = derived.filter(pl.col("kill_set") != pl.col("_kd")).height
    assert mismatch == 0, f"{mismatch} rows where kill_set != (stance==NEUT)"


def test_label_provenance_format(df: pl.DataFrame) -> None:
    """label_provenance ∈ D-72 enum; model_version is HF commit_sha[:8]."""
    provenance_values = set(df["label_provenance"].drop_nulls().to_list())
    assert provenance_values <= D72_LABEL_PROVENANCE, (
        f"label_provenance not in D-72 enum: {provenance_values}"
    )
    # model_version: short8 hex of HF commit_sha (CONTEXT D-71).
    versions = df["model_version"].drop_nulls().to_list()
    assert all(len(v) == 8 for v in versions), (
        f"model_version not short8: {sorted(set(versions))}"
    )
    assert all(all(c in "0123456789abcdef" for c in v) for v in versions)
    # Within a single emit, the pin is uniform.
    assert len(set(versions)) == 1, f"mixed model_version: {set(versions)}"


def test_join_able_with_vol_per_slot(df: pl.DataFrame) -> None:
    """D-71 must JOIN to v4.11 vol_per_slot on (pair, bar_time).

    vol_per_slot.bar_time is naive UTC; cast event_ts to naive before joining.
    Phase 102 constraint: vol_per_slot only has USDJPY/EURUSD; AUDUSD/EURJPY
    will be added later. Q1 4 events × 2 covered pairs = 8 expected rows.
    """
    if not VOL_PER_SLOT_PATH.exists():
        pytest.skip(f"vol_per_slot artifact missing: {VOL_PER_SLOT_PATH}")
    vol = pl.read_parquet(VOL_PER_SLOT_PATH)
    left = df.with_columns(
        pl.col("event_ts").dt.replace_time_zone(None).alias("bar_time")
    ).select(["bar_time", "pair", "stance", "kill_set"])
    joined = left.join(vol, on=["bar_time", "pair"], how="inner")
    assert joined.height > 0, "no overlap between D-71 and vol_per_slot"
    # 4 Q1 dates × {USDJPY, EURUSD} = 8 rows.
    assert joined.height == 8, (
        f"expected 8 join rows (4 Q1 × 2 covered pairs), got {joined.height}"
    )
