"""Phase 94 FILT-01 tests — skeleton for Plan 01.

Plan 02 expands with filter_cells full coverage.
"""

from __future__ import annotations

import importlib
import pathlib
import subprocess

import polars as pl
import pytest

# conftest.py already inserts scripts/v4.11 into sys.path (D-35).
import vol_regime_filter  # type: ignore[import-not-found]
from vol_regime_filter import strip_vol_prefix


class TestStripVolPrefix:
    def test_high(self) -> None:
        assert strip_vol_prefix("VOL_HIGH") == "HIGH"

    def test_mid(self) -> None:
        assert strip_vol_prefix("VOL_MID") == "MID"

    def test_low(self) -> None:
        assert strip_vol_prefix("VOL_LOW") == "LOW"

    def test_na(self) -> None:
        assert strip_vol_prefix("VOL_NA") == "NA"

    def test_idempotent(self) -> None:
        assert strip_vol_prefix("HIGH") == "HIGH"

    def test_empty(self) -> None:
        assert strip_vol_prefix("") == ""


class TestSealDriftFailClose:
    def test_import_fires_verify_seal_or_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import-time drift check: mutate compute hash -> RuntimeError on reimport."""
        import seal_drift_check as sdc

        monkeypatch.setattr(
            sdc, "compute_signal_commit_v411", lambda *_a, **_kw: "0" * 64
        )

        # Re-import module; verify_seal_or_raise at top-level must fire.
        with pytest.raises(RuntimeError):
            importlib.reload(vol_regime_filter)


class TestFilterCells:
    @pytest.fixture
    def synthetic_cells_and_vol(self) -> tuple[pl.DataFrame, pl.DataFrame]:
        """4 cells × 4 bucket coverage, deterministic."""
        from datetime import datetime

        cells = pl.DataFrame(
            {
                "cell_id": ["c_high", "c_mid", "c_low", "c_missing"],
                "pair": ["EURUSD", "EURUSD", "USDJPY", "USDJPY"],
                "event_ts": [
                    datetime(2024, 1, 15),
                    datetime(2024, 1, 16),
                    datetime(2024, 1, 17),
                    datetime(2024, 1, 18),  # no match in vol_df → VOL_NA
                ],
            }
        )
        vol = pl.DataFrame(
            {
                "pair": ["EURUSD", "EURUSD", "USDJPY"],
                "bar_time": [
                    datetime(2024, 1, 15),
                    datetime(2024, 1, 16),
                    datetime(2024, 1, 17),
                ],
                "bucket": ["VOL_HIGH", "VOL_MID", "VOL_LOW"],
            }
        )
        return cells, vol

    def test_active_mode_drops_non_high(self, synthetic_cells_and_vol):
        cells, vol = synthetic_cells_and_vol
        result = vol_regime_filter.filter_cells(cells, vol, neutral_mode=False)
        by_cell = dict(zip(result["cell_id"].to_list(), result["pass_flag"].to_list()))
        assert by_cell == {
            "c_high": True,
            "c_mid": False,
            "c_low": False,
            "c_missing": False,  # VOL_NA in active mode → drop
        }

    def test_neutral_mode_passes_all(self, synthetic_cells_and_vol):
        cells, vol = synthetic_cells_and_vol
        result = vol_regime_filter.filter_cells(cells, vol, neutral_mode=True)
        by_cell = dict(zip(result["cell_id"].to_list(), result["pass_flag"].to_list()))
        # D-39: neutral mode allows {HIGH, MID, LOW, NA} → all pass.
        assert all(by_cell.values()), f"expected all True, got {by_cell}"

    def test_output_schema(self, synthetic_cells_and_vol):
        cells, vol = synthetic_cells_and_vol
        result = vol_regime_filter.filter_cells(cells, vol)
        assert result.columns == ["cell_id", "pass_flag", "bucket"]
        assert result.schema["pass_flag"] == pl.Boolean
        assert result.schema["bucket"] == pl.Utf8
        assert result.schema["cell_id"] == pl.Utf8

    def test_missing_cell_gets_vol_na(self, synthetic_cells_and_vol):
        cells, vol = synthetic_cells_and_vol
        result = vol_regime_filter.filter_cells(cells, vol)
        missing_row = result.filter(pl.col("cell_id") == "c_missing")
        assert missing_row["bucket"].item() == "VOL_NA"

    def test_row_count_preserved(self, synthetic_cells_and_vol):
        cells, vol = synthetic_cells_and_vol
        result = vol_regime_filter.filter_cells(cells, vol, neutral_mode=False)
        assert result.height == cells.height


class TestCLI:
    def test_cli_help_shows_neutral_mode(self):

        result = subprocess.run(
            ["uv", "run", "python", "scripts/v4.11/vol_regime_filter.py", "--help"],
            capture_output=True,
            text=True,
            cwd=str(pathlib.Path.cwd()),
        )
        assert result.returncode == 0
        assert "--neutral-mode" in result.stdout
        assert "PARITY baseline" in result.stdout or "allowed_buckets" in result.stdout

    def test_cli_output_dir_path_traversal_rejected(self, tmp_path):

        # /tmp/... resolves OUTSIDE repo root → exit non-zero.
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "scripts/v4.11/vol_regime_filter.py",
                "--output-dir",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            cwd=str(pathlib.Path.cwd()),
        )
        assert result.returncode != 0
        assert "repo root" in (result.stderr + result.stdout).lower()
