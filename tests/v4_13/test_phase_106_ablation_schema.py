"""Phase 106 Wave 0 RED scaffold — ABLATION-V413-01.

diagnosis_v413_ablation.parquet schema 検証 (5 列 × 20 行 long-format)。
Wave 1 で emit_ablation_v413.py が emit する artifact を検証する。

Analog: tests/v4_13/test_phase_105_failure_modes_histogram.py
"""

from __future__ import annotations
from pathlib import Path
import polars as pl
import pytest

EXPECTED_ROWS = 20  # len(DIMENSIONS) * len(MILESTONES) = 5 * 4
EXPECTED_COLUMNS = [
    "milestone",
    "dimension",
    "baseline_pass_count",
    "ablated_pass_count",
    "delta",
]
EXPECTED_DTYPES = [pl.Utf8, pl.Utf8, pl.Int64, pl.Int64, pl.Int64]
DIMS = ["pair", "fee_bps", "window", "regime_cuts", "sizing"]
MILESTONES = ["v4.9", "v4.10", "v4.11", "v4.12"]


def _load_ablation(path: Path) -> pl.DataFrame:
    if not path.exists():
        pytest.fail(
            f"Wave 1 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 106 Wave 1 で emit_ablation_v413.py が ablation parquet を emit する."
        )
    return pl.read_parquet(path)


def test_ablation_row_count_dual_pin(phase106_ablation_path: Path) -> None:
    df = _load_ablation(phase106_ablation_path)
    assert df.height == EXPECTED_ROWS, (
        f"literal: expected {EXPECTED_ROWS}, got {df.height}"
    )
    assert df.height == len(DIMS) * len(MILESTONES)


def test_ablation_columns(phase106_ablation_path: Path) -> None:
    df = _load_ablation(phase106_ablation_path)
    assert df.columns == EXPECTED_COLUMNS


def test_ablation_dtypes(phase106_ablation_path: Path) -> None:
    df = _load_ablation(phase106_ablation_path)
    assert df.dtypes == EXPECTED_DTYPES


def test_ablation_dimension_values_are_5_axes(phase106_ablation_path: Path) -> None:
    df = _load_ablation(phase106_ablation_path)
    assert sorted(df["dimension"].unique().to_list()) == sorted(DIMS)


def test_ablation_milestone_values_are_4_versions(phase106_ablation_path: Path) -> None:
    df = _load_ablation(phase106_ablation_path)
    assert sorted(df["milestone"].unique().to_list()) == sorted(MILESTONES)


def test_ablation_baseline_pass_count_zero(phase106_ablation_path: Path) -> None:
    """全行 baseline_pass_count == 0 (Phase 105 forensic 結論、pass_flag=False 全行)."""
    df = _load_ablation(phase106_ablation_path)
    assert (df["baseline_pass_count"] == 0).all()


def test_ablation_delta_all_zero(phase106_ablation_path: Path) -> None:
    """全シナリオ delta == 0 (D-106-02 trivial_baseline_pathway 条件 1/3)."""
    df = _load_ablation(phase106_ablation_path)
    assert (df["delta"] == 0).all()
