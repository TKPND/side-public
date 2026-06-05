"""Bit-exact regression test for scripts/fetch_ticks.py Parquet output (Phase 97 Wave 1A).

Strategy: monkeypatch scripts.lib.data_fetch.fetch_month_dukascopy to return the
reference Parquet as a DataFrame (no real network). Call main() in-process,
collect the output Parquet from tmp_path dir, and compare sha256 against the
baked fixture (usdjpy_ticks_2024-01-08.parquet).

Phase 97 D-01: Parquet 一択。D-02: sha256 + pyarrow.Table.equals() fallback diff.
The reference fixture is baked for date label "2024-01-08" but the writer outputs
the monthly label "2024-01" — the sha256 comparison is on *bytes* (path-independent),
so the same data + same writer options produce identical Parquet bytes regardless
of filename.
"""

import hashlib
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

import scripts.fetch_ticks as _cli
import scripts.lib.data_fetch as _df_lib


def _reference_df(reference_tick_parquet: Path) -> pd.DataFrame:
    """Load reference Parquet back to a DataFrame indexed by timestamp.

    dukascopy's real contract returns a DatetimeIndex-based frame; writer's first
    branch handles both index-style (real dukascopy) and column-style (this mock).
    Here we convert back to index form so the writer exercises the DatetimeIndex path.
    """
    table = pq.read_table(reference_tick_parquet)
    df = table.to_pandas()
    df = df.set_index("timestamp")
    return df


def test_fetch_ticks_bit_exact_dukascopy_tick(
    tmp_path: Path,
    reference_tick_parquet: Path,
    expected_tick_parquet_sha256: str,
):
    """USDJPY 2024-01 tick via mock → output Parquet must sha256-match reference."""
    ref_df = _reference_df(reference_tick_parquet)

    def _mock_fetch(start, end, instrument, interval, timeout_sec, log):
        if start.year == 2024 and start.month == 1:
            return ref_df.copy()
        return None

    saved = _df_lib.fetch_month_dukascopy
    _df_lib.fetch_month_dukascopy = _mock_fetch
    try:
        exit_code = _cli.main(
            [
                "--pair",
                "USDJPY",
                "--start",
                "2024-01-01",
                "--end",
                "2024-02-01",
                "--source",
                "dukascopy",
                "--interval",
                "tick",
                "--for-bq",
                "--out",
                str(tmp_path),
            ]
        )
    finally:
        _df_lib.fetch_month_dukascopy = saved

    assert exit_code == 0, f"CLI returned non-zero: {exit_code}"

    out_files = list(tmp_path.glob("usdjpy_ticks_2024-01.parquet"))
    assert len(out_files) == 1, (
        f"expected 1 parquet in {tmp_path}, got: {list(tmp_path.iterdir())}"
    )

    actual_hash = hashlib.sha256(out_files[0].read_bytes()).hexdigest()
    assert actual_hash == expected_tick_parquet_sha256, (
        f"sha256 mismatch\n  actual   = {actual_hash}\n  expected = {expected_tick_parquet_sha256}"
    )

    # Fallback diff: pyarrow.Table.equals for human-readable failure output
    actual_table = pq.read_table(out_files[0])
    reference_table = pq.read_table(reference_tick_parquet)
    assert actual_table.equals(reference_table), "Parquet table content mismatch"


def test_tick_parquet_schema(reference_tick_parquet: Path):
    """Reference Parquet has expected schema (D-03)."""
    import pyarrow as pa

    schema = pq.read_schema(reference_tick_parquet)
    names = [f.name for f in schema]
    assert names == ["timestamp", "bidPrice", "askPrice", "bidVolume", "askVolume"]
    assert schema.field("timestamp").type == pa.timestamp("ms", tz="UTC")
    for col in ["bidPrice", "askPrice", "bidVolume", "askVolume"]:
        assert schema.field(col).type == pa.float64()


def test_tick_parquet_date_filter(reference_tick_parquet: Path):
    """All rows in reference Parquet are dated 2024-01-08."""
    table = pq.read_table(reference_tick_parquet)
    ts = table.column("timestamp").to_pandas()
    assert (ts.dt.date.astype(str) == "2024-01-08").all()
