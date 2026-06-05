"""Shared fixtures for Phase 89 tests (DD-01..03)."""

from __future__ import annotations

import pathlib
from typing import Callable

import polars as pl
import pytest


@pytest.fixture
def trade_log_mock() -> list[dict]:
    """Synthetic trade log for TestApplyStepDown / TestApplyHardCap."""
    return [
        {
            "trade_id": 1,
            "cell_id": "cell_A",
            "fold_id": 1,
            "entry_price": 100.0,
            "entry_bar": 0,
            "bars": [
                {"ts": "2026-04-23T09:30:00Z", "close": 101.0, "low": 99.0},
                {"ts": "2026-04-23T10:00:00Z", "close": 102.0, "low": 100.5},
            ],
        },
    ]


@pytest.fixture
def equity_curve_fabricator() -> Callable:
    """Factory for synthetic equity curves (bar_ts, equity)."""

    def make_equity_curve(
        initial: float,
        returns: list[float],
        event_days: list[str],
    ) -> pl.DataFrame:
        """Build synthetic fold equity curve for DD gate testing."""
        rows = []
        equity = initial
        for i, ret in enumerate(returns):
            equity *= 1.0 + ret
            rows.append(
                {
                    "bar_ts": event_days[i % len(event_days)],
                    "equity": equity,
                }
            )
        return pl.DataFrame(rows)

    return make_equity_curve


@pytest.fixture
def cell_spec_fabricator() -> Callable:
    """Factory for CellSpec mocks (192 cells)."""

    def make_cell_spec(cell_id: str) -> dict:
        return {"cell_id": cell_id, "kelly_fraction": 0.25}

    return make_cell_spec


@pytest.fixture
def seal_dir_fixture() -> pathlib.Path:
    """Reference to .planning/phases/88-pre-registration-seal-v4-10/88-SEAL."""
    return (
        pathlib.Path(__file__).resolve().parents[3]
        / ".planning"
        / "phases"
        / "88-pre-registration-seal-v4-10"
        / "88-SEAL"
    )
