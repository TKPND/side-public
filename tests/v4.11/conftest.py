"""Shared fixtures for Phase 93 tests (CLASS-01..04).

D-35 Import path lock:
  scripts/v4.11/ has a dot in its name -> cannot be a Python package.
  Insert its absolute path to sys.path so test files can use
  `from seal_drift_check import ...` (flat import) instead of
  `from scripts.v4_11.seal_drift_check import ...` (which is impossible).
"""

from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_V411 = _REPO_ROOT / "scripts" / "v4.11"
# D-35: flat import path for scripts/v4.11 (dot-in-dir prevents package import).
# MUST precede any `from seal_drift_check import ...` in test files.
if str(SCRIPTS_V411) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_V411))

_SEAL_DIR = (
    _REPO_ROOT / ".planning" / "phases" / "92-scope-lock-pre-registration-seal" / "SEAL"
)
EXPECTED_SEAL_FILES = ["classifier_spec.json", "filter_spec.json", "vol_cuts.json"]


@pytest.fixture
def synthetic_ohlc_2pair() -> pl.DataFrame:
    """D-30 synthetic OHLC: 2 pair x 120 bars, seed=42, daily bars.

    Returns pl.DataFrame with columns=[pair, bar_time, open, high, low, close].
    Total rows = 240 (2 pairs x 120 bars).
    """
    rng = np.random.default_rng(42)
    pairs = ["EURUSD", "USDJPY"]
    n_bars = 120
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n_bars)]
    frames: list[pl.DataFrame] = []
    for pair in pairs:
        close = np.cumprod(1 + rng.normal(0, 0.001, n_bars))
        spread = rng.uniform(0.0005, 0.003, n_bars)
        high = close * (1 + spread / 2)
        low = close * (1 - spread / 2)
        open_ = close * (1 + rng.normal(0, 0.0005, n_bars))
        frames.append(
            pl.DataFrame(
                {
                    "pair": [pair] * n_bars,
                    "bar_time": dates,
                    "open": open_.tolist(),
                    "high": high.tolist(),
                    "low": low.tolist(),
                    "close": close.tolist(),
                }
            )
        )
    return pl.concat(frames)


@pytest.fixture
def seal_dir_fixture() -> pathlib.Path:
    """Real SEAL dir (read-only; D-17 untouched)."""
    return _SEAL_DIR


@pytest.fixture
def seal_dir_canonical(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write 3 SEAL JSON to tmp as canonical bytes (no trailing newline).

    Used as a stable, deterministic fixture for drift tests.
    The files are canonical (sort_keys=True, compact separators) but
    without trailing newlines — matching what canonical_bytes() returns.
    """
    for fname in EXPECTED_SEAL_FILES:
        obj = json.loads((_SEAL_DIR / fname).read_text(encoding="utf-8"))
        blob = json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        (tmp_path / fname).write_bytes(blob)
    return tmp_path


# ---- Phase 94 additions ----

V4_10_DIR = _REPO_ROOT / "reports" / "v4.10"
V4_11_DIR = _REPO_ROOT / "reports" / "v4.11"


@pytest.fixture
def v4_10_ship_decision_path() -> pathlib.Path:
    """Absolute path to reports/v4.10/v4_10_ship_decision.json (Phase 94 PARITY baseline)."""
    return V4_10_DIR / "v4_10_ship_decision.json"


@pytest.fixture
def v4_10_p_adj_path() -> pathlib.Path:
    """Absolute path to reports/v4.10/p_adj_v410.json (count_edges input)."""
    return V4_10_DIR / "p_adj_v410.json"


@pytest.fixture
def v4_10_dd_traces_path() -> pathlib.Path:
    """Absolute path to data/v4.10/dd_traces.parquet (compute_primary_metrics input).

    Note: this path may not exist in all environments; tests consuming this
    fixture should `pytest.skip()` on FileNotFoundError (real-data dependent).
    """
    return _REPO_ROOT / "data" / "v4.10" / "dd_traces.parquet"


@pytest.fixture
def reports_v411_dir() -> pathlib.Path:
    """Absolute path to reports/v4.11/ (Phase 94 output root)."""
    return V4_11_DIR


@pytest.fixture
def seal_filter_spec_path() -> pathlib.Path:
    """Absolute path to SEAL filter_spec.json (READ-ONLY, D-17).

    post_filter_m_prime=64 is nested inside .fwer_denominator.
    There is NO separate fwer_denominator.json file.
    """
    return (
        _REPO_ROOT
        / ".planning"
        / "phases"
        / "92-scope-lock-pre-registration-seal"
        / "SEAL"
        / "filter_spec.json"
    )
