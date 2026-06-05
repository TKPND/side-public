"""DIAG-V413-02: bit-exact 480 行 (192 + 192 + 64 + 32) per CONTEXT D-V413-06.

Wave 0 RED state: parquet 未生成のため fixture skip でテスト全体 skip 扱い。
Wave 1 aggregator emit 後に GREEN 化。
"""

from __future__ import annotations

import polars as pl


def test_total_rowcount_bit_exact(diagnosis_v413_parquet) -> None:
    df = diagnosis_v413_parquet
    EXPECTED_TOTAL = 480
    assert df.height == EXPECTED_TOTAL, (
        f"row count mismatch: got {df.height}, expected {EXPECTED_TOTAL}"
    )


def test_per_milestone_counts(diagnosis_v413_parquet) -> None:
    df = diagnosis_v413_parquet
    per_milestone = df.group_by("milestone").agg(pl.len().alias("n")).to_dicts()
    counts = {r["milestone"]: r["n"] for r in per_milestone}
    expected = {"v4.9": 192, "v4.10": 192, "v4.11": 64, "v4.12": 32}
    assert counts == expected, (
        f"per-milestone count mismatch: got {counts}, expected {expected}"
    )


def test_sidecar_expected_row_counts_match(
    diagnosis_v413_parquet, diagnosis_v413_sidecar
) -> None:
    df = diagnosis_v413_parquet
    per_milestone = df.group_by("milestone").agg(pl.len().alias("n")).to_dicts()
    counts = {r["milestone"]: r["n"] for r in per_milestone}
    counts["total"] = sum(counts.values())
    assert diagnosis_v413_sidecar["expected_row_counts"] == counts, (
        f"sidecar mismatch: sidecar={diagnosis_v413_sidecar['expected_row_counts']}, "
        f"parquet={counts}"
    )
