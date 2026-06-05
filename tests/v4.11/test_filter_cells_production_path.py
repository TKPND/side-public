"""Phase 94 gap-closure (D-42 step 1) — SC#4 override audit evidence.

Invokes filter_cells() via synthetic fixture with fully-populated event_ts +
vol-regime coverage to prove the production code-path (non-deviation JOIN) runs
and yields pass_flag=True >= 1. Complements existing test_vol_regime_filter.py
TestFilterCells coverage by standing alone as dedicated SC#4 closure evidence.

See: 94-CONTEXT.md Area E (D-41 pre-reg invariant, D-42 3-element compound),
     94-VERIFICATION.md gaps[0].override (closed_with_overrides status).
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from vol_regime_filter import (
    filter_cells,
)  # D-35 flat import (conftest.py injects sys.path)


class TestFilterCellsProductionPath:
    """D-42 step 1: production code-path (non-deviation JOIN) invocation evidence.

    All tests use synthetic-only fixtures (no real data file reads).
    Purpose: prove filter_cells() executes the normal LEFT JOIN path with
    allowed_buckets=[HIGH] (SEAL active-mode) and yields pass_flag=True >= 1.

    Per D-41: m_prime=64 is a SEAL pre-reg grid design invariant, not a
    real-data pass-count gate. This test suite validates the production
    code-path correctness via synthetic data, not real-data m_prime matching.
    """

    @pytest.fixture
    def synthetic_cells_and_vol(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Synthetic fixture: 4 cells + 3 vol rows with full event_ts coverage.

        cells_df columns: (cell_id: str, pair: str, event_ts: datetime)
        vol_per_slot_df columns: (pair: str, bar_time: datetime, bucket: str)

        Designed so that:
        - 2 cells have bucket=VOL_HIGH (will pass_flag=True in active mode)
        - 1 cell has bucket=VOL_MID (will pass_flag=False in active mode)
        - 1 cell has no matching vol row (bucket=VOL_NA, pass_flag=False)
        """
        ts1 = datetime(2024, 1, 15, 9, 0, 0)
        ts2 = datetime(2024, 1, 16, 9, 0, 0)
        ts3 = datetime(2024, 1, 17, 9, 0, 0)
        ts4 = datetime(2024, 1, 18, 9, 0, 0)  # no matching vol row

        cells_df = pl.DataFrame(
            {
                "cell_id": ["C001", "C002", "C003", "C004"],
                "pair": ["EURUSD", "USDJPY", "EURUSD", "USDJPY"],
                "event_ts": [ts1, ts2, ts3, ts4],
            }
        )

        # 3 vol rows: VOL_HIGH x2, VOL_MID x1 (C004/ts4 has no matching row)
        vol_per_slot_df = pl.DataFrame(
            {
                "pair": ["EURUSD", "USDJPY", "EURUSD"],
                "bar_time": [ts1, ts2, ts3],
                "bucket": ["VOL_HIGH", "VOL_HIGH", "VOL_MID"],
            }
        )

        return cells_df, vol_per_slot_df

    def test_production_path_pass_flag_ge_1(
        self, synthetic_cells_and_vol: tuple[pl.DataFrame, pl.DataFrame]
    ) -> None:
        """D-42 step 1 core assertion: production JOIN path yields pass_flag>=1.

        Verifies that filter_cells() runs the normal LEFT JOIN code-path
        (not the deviation branch) and produces at least 1 pass_flag=True cell
        when vol-regime data is properly matched.

        This is the primary SC#4 override audit evidence per D-42 step 1.
        """
        cells_df, vol_per_slot_df = synthetic_cells_and_vol

        result = filter_cells(cells_df, vol_per_slot_df, neutral_mode=False)

        # Core D-42 step 1 assertion: production path must yield pass_flag>=1
        n_pass = int(result.filter(pl.col("pass_flag")).height)
        assert n_pass >= 1, (
            "D-42 step 1: filter_cells production path must yield pass_flag>=1 via VOL_HIGH JOIN"
        )

        # Negative evidence: production JOIN succeeded, so NOT ALL cells are VOL_NA
        # (deviation branch would produce exclusively VOL_NA for every row)
        buckets = set(result["bucket"].to_list())
        assert buckets != {"VOL_NA"}, (
            "D-42 step 1: production JOIN path must not produce exclusively VOL_NA "
            "(that would indicate deviation branch, not production path)"
        )

        # Schema check: result must have expected columns
        assert set(result.columns) == {"cell_id", "pass_flag", "bucket"}, (
            "filter_cells must return (cell_id, pass_flag, bucket) schema"
        )

        # Row count must match input cells
        assert result.height == cells_df.height, (
            "filter_cells result row count must equal input cells row count"
        )

    def test_production_path_active_drops_non_high(
        self, synthetic_cells_and_vol: tuple[pl.DataFrame, pl.DataFrame]
    ) -> None:
        """Auxiliary: active-mode (neutral_mode=False) drops non-HIGH buckets.

        Verifies that allowed_buckets=[HIGH] filter is active in production
        code-path: VOL_MID cell (C003) and VOL_NA cell (C004) are pass_flag=False,
        while VOL_HIGH cells (C001, C002) are pass_flag=True.

        This confirms the SEAL allowed_buckets=[HIGH] filter logic executes
        correctly in the production code-path (not bypassed by deviation branch).
        """
        cells_df, vol_per_slot_df = synthetic_cells_and_vol

        result = filter_cells(cells_df, vol_per_slot_df, neutral_mode=False)

        # Build lookup: cell_id -> (pass_flag, bucket)
        result_dict = {
            row["cell_id"]: (row["pass_flag"], row["bucket"])
            for row in result.to_dicts()
        }

        # VOL_HIGH cells must pass (production path with SEAL allowed_buckets=[HIGH])
        assert result_dict["C001"][0] is True, "C001 (VOL_HIGH) must be pass_flag=True"
        assert result_dict["C002"][0] is True, "C002 (VOL_HIGH) must be pass_flag=True"

        # VOL_MID cell must fail (not in SEAL allowed_buckets=[HIGH])
        assert result_dict["C003"][0] is False, "C003 (VOL_MID) must be pass_flag=False"

        # VOL_NA cell (no match) must fail
        assert result_dict["C004"][0] is False, (
            "C004 (VOL_NA, no JOIN match) must be pass_flag=False"
        )
        assert result_dict["C004"][1] == "VOL_NA", (
            "C004 unmatched cell must have bucket=VOL_NA"
        )
