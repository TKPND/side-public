"""
conftest.py — Wave 0 scaffold fixtures for n_per_cell audit tests.

Provides `dummy_classifier_parquet` fixture: writes a 192-row parquet
matching the locked D-C1 grid schema that n_per_cell_audit.py will read.

Column names are the canonical filter columns:
  pair, event, tod, vol_stance, obs_id
"""
import pytest

# Guard: polars is a production dependency; if absent, skip all audit tests.
polars = pytest.importorskip("polars")
import polars as pl
import itertools
from pathlib import Path

# ── Locked D-C1 grid (100-PLAN.md <interfaces> §n_per_cell grid) ──────────
PAIRS = ["USDJPY", "EURUSD", "AUDUSD", "EURJPY"]
EVENTS = [
    "FOMC_2024-01-31",
    "FOMC_2024-03-20",
    "ECB_2024-01-25",
    "ECB_2024-03-07",
]
TOD = ["pre", "during", "post"]
VOL_STANCE = ["HIGH_HAWK", "HIGH_DOV", "LOW_HAWK", "LOW_DOV"]
EXPECTED_CELLS = 4 * 4 * 3 * 4  # 192


@pytest.fixture(scope="session")
def dummy_classifier_parquet(tmp_path_factory):
    """
    Write a 192-row parquet (one synthetic row per cell) to
    tests/audit/fixtures/dummy_classifier_v412.parquet and return its path.

    Each row has exactly one obs per (pair, event, tod, vol_stance) tuple,
    plus a unique obs_id.  This satisfies the D-C1 schema contract so that
    Wave 1 n_per_cell_audit.py can filter on these columns.
    """
    rows = [
        {
            "pair": pair,
            "event": event,
            "tod": tod,
            "vol_stance": vol_stance,
            "obs_id": idx,
        }
        for idx, (pair, event, tod, vol_stance) in enumerate(
            itertools.product(PAIRS, EVENTS, TOD, VOL_STANCE)
        )
    ]
    assert len(rows) == EXPECTED_CELLS, f"Expected {EXPECTED_CELLS} rows, got {len(rows)}"

    df = pl.DataFrame(rows)

    # Write to a stable path inside the repo so Wave 1 can reference it.
    fixture_dir = Path(__file__).parent / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / "dummy_classifier_v412.parquet"
    df.write_parquet(fixture_path)
    return fixture_path
