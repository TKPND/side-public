"""DIAG-V413-01: 12 列 schema + dtype 強制 (CONTEXT D-V413-05 + Claude Discretion schema_version).

Wave 0 RED state: Phase 104 aggregator parquet 未生成のため `diagnosis_v413_parquet`
fixture が pytest.skip を発行する。Wave 1 で aggregator が emit したら GREEN に転換。
"""

from __future__ import annotations

import polars as pl

D413_COLUMNS: list[str] = [
    "milestone",
    "pair",
    "fee_bps",
    "window",
    "regime_cuts",
    "sizing",
    "pass_flag",
    "fwer_threshold",
    "observed_metric",
    "hurdle_gap",
    "observed_metric_kind",
    "schema_version",
]

D413_DTYPES: dict[str, type] = {
    "milestone": pl.Utf8,
    "pair": pl.Utf8,
    "fee_bps": pl.Float64,
    "window": pl.Utf8,
    "regime_cuts": pl.Utf8,
    "sizing": pl.Utf8,
    "pass_flag": pl.Boolean,
    "fwer_threshold": pl.Float64,
    "observed_metric": pl.Float64,
    "hurdle_gap": pl.Float64,
    "observed_metric_kind": pl.Utf8,
    "schema_version": pl.Utf8,
}


def test_columns_canonical_order(diagnosis_v413_parquet) -> None:
    df = diagnosis_v413_parquet
    assert df.columns == D413_COLUMNS, f"expected {D413_COLUMNS}, got {df.columns}"


def test_dtypes_match(diagnosis_v413_parquet) -> None:
    df = diagnosis_v413_parquet
    actual = dict(zip(df.columns, df.dtypes))
    for col, expected_dtype in D413_DTYPES.items():
        assert actual[col] == expected_dtype, (
            f"{col}: expected {expected_dtype}, got {actual[col]}"
        )


def test_schema_version_constant(diagnosis_v413_parquet) -> None:
    df = diagnosis_v413_parquet
    versions = df.get_column("schema_version").unique().to_list()
    assert versions == ["v4.13.0"], f"expected ['v4.13.0'], got {versions}"
