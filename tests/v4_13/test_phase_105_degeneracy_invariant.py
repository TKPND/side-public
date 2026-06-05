"""tests/v4_13/test_phase_105_degeneracy_invariant.py — Phase 105 Wave 0 RED scaffold.

Phase 105 Resolution A 採択 (CONTEXT.md re-opened 2026-04-27): 全 480 cell が
`failure_mode = degenerate` で埋まることを機械固定する。Wave 2 emit が満たすべき
不変条件を TDD scaffold として先に書き、Wave 0 commit 時点では artifact 未更新
のため全 test RED で良い (RED→GREEN→REFACTOR).

検証対象 invariant:
    - GAP-V413-02 / D-105-02: 全 480 cell `failure_mode == "degenerate"`
    - GAP-V413-01 / D-105-04 / W7: `hurdle_gap` 全 480 行 NULL **かつ** dtype Float64
    - D-105-05: `schema_version == "v4.13.1"` (B1 反映: schema_version test 統合)
    - D-105-05: 13 列 (12 既存 + failure_mode 新設)
    - INTEGRITY-V413-01 / D-17 / B3: scripts/v4.13/{aggregate_diagnosis_v413,
      diagnosis_decoders}.py が Phase 104 ship 時 SHA256 から不変
      (test_d17_invariant.py の module-level constants 経由で参照、再計算禁止).

Citations:
    - 105-01-PLAN.md Task 1
    - 105-CONTEXT.md D-105-02 / D-105-04 / D-105-05
    - 105-RESEARCH.md "Don't Hand-Roll" 規約
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl
import pytest

# B3 反映: hash constants は test_d17_invariant.py から import (再計算禁止).
from tests.v4_13.test_d17_invariant import (
    AGGREGATE_HASH_PHASE104,
    DECODERS_HASH_PHASE104,
)

# Phase 105 Wave 2 後の diagnosis_v413.parquet が満たすべき期待値
EXPECTED_ROW_COUNT = 480
EXPECTED_FAILURE_MODE = "degenerate"
EXPECTED_SCHEMA_VERSION = "v4.13.1"
EXPECTED_COLUMN_COUNT = 13  # 12 既存 + failure_mode 新設


def _load_v413_parquet(path: Path) -> pl.DataFrame:
    """artifact 未存在時は明示的に pytest.fail で RED させる (Wave 0 RED 状態)."""
    if not path.exists():
        pytest.fail(
            f"Wave 2 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 105 Wave 2 で in-place 上書きされる前は本 test は RED."
        )
    return pl.read_parquet(path)


def test_all_cells_failure_mode_degenerate(phase105_diagnosis_path: Path) -> None:
    """全 480 cell が failure_mode == "degenerate" であること (D-105-02 / GAP-V413-02).

    Resolution A: hurdle_gap が ill-defined である事実を 1-mode emit で proof.
    """
    df = _load_v413_parquet(phase105_diagnosis_path)
    assert "failure_mode" in df.columns, (
        "D-105-05: failure_mode 列が新設されていること (Wave 2 で追加予定)"
    )
    assert df.height == EXPECTED_ROW_COUNT, f"480 行であること: got {df.height}"
    unique_modes = df["failure_mode"].unique().to_list()
    assert unique_modes == [EXPECTED_FAILURE_MODE], (
        f"全 480 cell が {EXPECTED_FAILURE_MODE!r} であること: got unique={unique_modes}"
    )


def test_all_hurdle_gap_null(phase105_diagnosis_path: Path) -> None:
    """hurdle_gap 列が全 480 行 NULL かつ dtype Float64 であること (W7 dual-assert).

    GAP-V413-01 / D-105-04 / W7: NULL 性 + dtype 両方を機械固定.
    Float64 dtype が壊れると polars の null semantics が変わって invariant が崩れる.
    """
    df = _load_v413_parquet(phase105_diagnosis_path)
    assert "hurdle_gap" in df.columns, "hurdle_gap 列が存在すること"
    assert df.schema["hurdle_gap"] == pl.Float64, (
        f"W7: hurdle_gap dtype must be Float64, got {df.schema['hurdle_gap']}"
    )
    assert df["hurdle_gap"].null_count() == EXPECTED_ROW_COUNT, (
        f"全 {EXPECTED_ROW_COUNT} 行 NULL であること: "
        f"null_count={df['hurdle_gap'].null_count()}"
    )


def test_schema_version_bumped_to_v413_1(phase105_diagnosis_path: Path) -> None:
    """schema_version 列が全行 "v4.13.1" であること (D-105-05, B1 統合).

    B1 反映: 元の test_phase_105_schema_version_bump.py を本 file の case として統合.
    """
    df = _load_v413_parquet(phase105_diagnosis_path)
    assert "schema_version" in df.columns, "schema_version 列が存在すること"
    unique_versions = df["schema_version"].unique().to_list()
    assert unique_versions == [EXPECTED_SCHEMA_VERSION], (
        f"全行 {EXPECTED_SCHEMA_VERSION!r} であること: got unique={unique_versions}"
    )


def test_diagnosis_parquet_has_13_columns(phase105_diagnosis_path: Path) -> None:
    """diagnosis_v413.parquet が 13 列 (既存 12 + failure_mode) であること (D-105-05)."""
    df = _load_v413_parquet(phase105_diagnosis_path)
    assert df.width == EXPECTED_COLUMN_COUNT, (
        f"13 列であること (既存 12 + failure_mode 新設): "
        f"got width={df.width}, columns={df.columns}"
    )
    assert "failure_mode" in df.columns, "failure_mode 列が新設されていること"


def test_d17_invariant_phase_104_scripts_unchanged(project_root: Path) -> None:
    """D-17 invariant: scripts/v4.13/{aggregate_diagnosis_v413,diagnosis_decoders}.py が
    Phase 104 ship 時 SHA256 (commit 06f326c) から drift してないこと.

    B3 反映: test_d17_invariant.py の module-level constants を import で参照、
    fixture 内で再計算しない (circular dependency 排除, T-105-15 mitigation).
    INTEGRITY-V413-01: hash drift 検知の single source of truth.
    """
    aggregate = project_root / "scripts" / "v4.13" / "aggregate_diagnosis_v413.py"
    decoders = project_root / "scripts" / "v4.13" / "diagnosis_decoders.py"

    h_agg = hashlib.sha256(aggregate.read_bytes()).hexdigest()
    h_dec = hashlib.sha256(decoders.read_bytes()).hexdigest()

    assert h_agg == AGGREGATE_HASH_PHASE104, (
        f"D-17 violated: aggregate_diagnosis_v413.py drift "
        f"current={h_agg[:12]} vs Phase104={AGGREGATE_HASH_PHASE104[:12]}"
    )
    assert h_dec == DECODERS_HASH_PHASE104, (
        f"D-17 violated: diagnosis_decoders.py drift "
        f"current={h_dec[:12]} vs Phase104={DECODERS_HASH_PHASE104[:12]}"
    )
