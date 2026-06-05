"""tests/v4_13/test_phase_105_failure_modes_histogram.py — Phase 105 Wave 0 RED scaffold.

Resolution A 採択 (CONTEXT.md re-opened 2026-04-27): failure_mode は唯一 "degenerate"。
1-mode emit を long-format histogram parquet で proof する。

検証対象 invariant:
    - D-105-03: long-format 5 列 schema (milestone/dimension/dim_value/mode/count)
    - D-105-03: dtypes [Utf8, Utf8, Utf8, Utf8, UInt32]
    - Resolution A 1-mode: mode 列の n_unique() == 1, 唯一値 = "degenerate"
    - W6 dual-assert: 行数 == 166 (literal) AND 行数 == cardinality formula
    - 4 milestone (v4.9 / v4.10 / v4.11 / v4.12) 全出現

cardinality formula (RESEARCH.md Finding 5, Errata 2026-04-27 で 136→166 訂正):
    sum_over_milestone( sum_over_dim( N_unique(dim_value, milestone) ) )
        v4.9:  1+1+16+1+12 = 31
        v4.10: 1+1+16+1+12 = 31
        v4.11: 1+1+64+1+1  = 68
        v4.12: 1+1+32+1+1  = 36
        合計: 166

    Errata: 元 RESEARCH.md は v4.9/v4.10 の window 列を `1` と転記していた (sum
    16/milestone, total 136)。実 parquet の n_unique 実測で window=16 が正しく、
    total cells 480 = 192+192+64+32 で整合する形に訂正。

Citations:
    - 105-01-PLAN.md Task 2
    - 105-CONTEXT.md D-105-03
    - 105-RESEARCH.md Finding 5
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

EXPECTED_ROWS = 166
EXPECTED_MODE = "degenerate"
EXPECTED_COLUMNS = ["milestone", "dimension", "dim_value", "mode", "count"]
EXPECTED_DTYPES = [pl.Utf8, pl.Utf8, pl.Utf8, pl.Utf8, pl.UInt32]
DIMS = ["pair", "fee_bps", "window", "regime_cuts", "sizing"]
MILESTONES = ["v4.9", "v4.10", "v4.11", "v4.12"]


def _load_failure_modes(path: Path) -> pl.DataFrame:
    """artifact 未存在時は明示的に pytest.fail で RED させる (Wave 0 RED 状態)."""
    if not path.exists():
        pytest.fail(
            f"Wave 2 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 105 Wave 2 で emit_degeneracy_proof.py が long-format histogram を emit する."
        )
    return pl.read_parquet(path)


def test_failure_modes_long_format_schema(
    phase105_failure_modes_path: Path,
) -> None:
    """failure_modes parquet が long-format 5 列 schema (D-105-03).

    columns: milestone (Utf8), dimension (Utf8), dim_value (Utf8), mode (Utf8), count (UInt32)
    """
    df = _load_failure_modes(phase105_failure_modes_path)
    assert df.columns == EXPECTED_COLUMNS, (
        f"columns must be {EXPECTED_COLUMNS}: got {df.columns}"
    )
    actual_dtypes = [df.schema[c] for c in EXPECTED_COLUMNS]
    assert actual_dtypes == EXPECTED_DTYPES, (
        f"dtypes must be {EXPECTED_DTYPES}: got {actual_dtypes}"
    )


def test_failure_modes_single_mode(phase105_failure_modes_path: Path) -> None:
    """mode 列が単一値 = "degenerate" であること (Resolution A 1-mode emit).

    D-105-03: Resolution A 採択により MONOTONE_BOUNDARY / hurdle-shy 等の他 mode は emit しない.
    """
    df = _load_failure_modes(phase105_failure_modes_path)
    assert "mode" in df.columns, "mode 列が存在すること"
    unique_modes = df["mode"].unique().to_list()
    assert unique_modes == [EXPECTED_MODE], (
        f"全行 mode == {EXPECTED_MODE!r} であること: got unique={unique_modes}"
    )
    assert df["mode"].n_unique() == 1, (
        f"mode の n_unique() == 1 であること: got {df['mode'].n_unique()}"
    )


def test_failure_modes_row_count_matches_cardinality(
    phase105_failure_modes_path: Path,
    phase105_diagnosis_path: Path,
) -> None:
    """W6 dual-assert: 行数 == 136 (literal) AND 行数 == cardinality formula.

    formula は Phase 105 Wave 2 後の diagnosis_v413.parquet から
    sum_over_milestone(sum_over_dim(N_unique(dim_value, milestone))) で計算.
    literal 136 は RESEARCH.md Finding 5 から確定.
    """
    df_hist = _load_failure_modes(phase105_failure_modes_path)

    # Side 1: literal pin (RESEARCH.md Finding 5)
    assert df_hist.height == EXPECTED_ROWS, (
        f"literal row count: expected {EXPECTED_ROWS}, got {df_hist.height}"
    )

    # Side 2: cardinality formula computed from diagnosis_v413.parquet
    if not phase105_diagnosis_path.exists():
        pytest.fail(
            f"diagnosis_v413.parquet 未存在 (Wave 0 RED expected): {phase105_diagnosis_path}\n"
            "formula 検証には diagnosis_v413.parquet (Phase 104 emit) が必要."
        )
    df_diag = pl.read_parquet(phase105_diagnosis_path)
    expected_formula = sum(
        df_diag.filter(pl.col("milestone") == m)[dim].n_unique()
        for m in MILESTONES
        for dim in DIMS
    )
    assert df_hist.height == expected_formula, (
        f"cardinality formula: expected {expected_formula}, got {df_hist.height}"
    )
    # dual-pin consistency: literal == formula
    assert expected_formula == EXPECTED_ROWS, (
        f"formula {expected_formula} != literal {EXPECTED_ROWS} — "
        "RESEARCH.md Finding 5 の cardinality 計算と Phase 104 parquet 実 cardinality に乖離"
    )


def test_failure_modes_milestones_complete(
    phase105_failure_modes_path: Path,
) -> None:
    """4 milestone (v4.9 / v4.10 / v4.11 / v4.12) 全て出現すること."""
    df = _load_failure_modes(phase105_failure_modes_path)
    actual_milestones = sorted(df["milestone"].unique().to_list())
    expected_milestones = sorted(MILESTONES)
    assert actual_milestones == expected_milestones, (
        f"4 milestone 完備: expected {expected_milestones}, got {actual_milestones}"
    )
