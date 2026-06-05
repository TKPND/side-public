"""Shared fixtures for Phase 87 tests (SIZE-01..04)."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest


@pytest.fixture
def kelly_valid_cases() -> list[tuple[float, bool]]:
    """Valid Kelly fractions: [0.25, 0.49, 0.50]."""
    return [(0.25, True), (0.49, True), (0.50, True)]


@pytest.fixture
def kelly_invalid_cases() -> list[tuple[float, bool]]:
    """Invalid Kelly fractions: [-0.01, 0.51, 1.0, inf, nan]."""
    return [
        (-0.01, False),
        (0.51, False),
        (1.0, False),
        (float("inf"), False),
        (float("nan"), False),
    ]


@pytest.fixture
def synth_wins_losses() -> tuple[np.ndarray, np.ndarray]:
    """Deterministic synthetic wins/losses for BCa bootstrap test (seed=20260422)."""
    rng = np.random.default_rng(20260422)
    wins = rng.uniform(5.0, 25.0, size=60)
    losses = rng.uniform(-15.0, -1.0, size=40)
    return wins, losses


@pytest.fixture
def synth_trades_df() -> pl.DataFrame:
    """Synthetic trades DataFrame for SIZE-03 integration tests."""
    rng = np.random.default_rng(20260422)
    return pl.DataFrame(
        {
            "fold": [0] * 100,
            "cell_id": ["cell_A"] * 100,
            "trade_id": list(range(100)),
            "pnl": list(rng.normal(loc=0.5, scale=10.0, size=100)),
            "atr_at_entry": [0.5] * 100,
        }
    )


@pytest.fixture
def seed() -> int:
    """Canonical random seed for Phase 87 (D-11)."""
    return 20260422
