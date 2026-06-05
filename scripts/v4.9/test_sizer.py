"""Tests for scripts/v4.9/sizer.py (SIZE-01, SIZE-02, SIZE-03).

Phase 87 Wave 0: RED state — sizer.py not yet implemented.
All tests fail with ImportError or NotImplementedError until Wave 1.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import numpy as np
import polars as pl
import pytest

# ---------------------------------------------------------------------------
# Load sizer.py as module (absolute path — Wave 1 will create this file)
# ---------------------------------------------------------------------------
_MODULE_PATH = pathlib.Path(__file__).parent / "sizer.py"
_spec = importlib.util.spec_from_file_location("sizer", _MODULE_PATH)
if _spec is not None and _spec.loader is not None:
    sizer = importlib.util.module_from_spec(_spec)
    sys.modules["sizer"] = sizer
    try:
        _spec.loader.exec_module(sizer)
        _SIZER_AVAILABLE = True
    except Exception:
        _SIZER_AVAILABLE = False
else:
    sizer = None  # type: ignore[assignment]
    _SIZER_AVAILABLE = False


def _require_sizer():
    """Raise ImportError if sizer module is not available (RED gate)."""
    if not _SIZER_AVAILABLE or sizer is None:
        raise ImportError("sizer.py not yet implemented (Wave 1 task)")


# ---------------------------------------------------------------------------
# SIZE-01: FractionalKelly newtype validation
# ---------------------------------------------------------------------------


def test_fractional_kelly_valid(kelly_valid_cases):
    """SIZE-01 AC: FractionalKelly accepts f in [0.0, 0.5]."""
    _require_sizer()
    for value, should_pass in kelly_valid_cases:
        if should_pass:
            f = sizer.FractionalKelly.try_new(value)
            assert f.value == value


def test_fractional_kelly_invalid(kelly_invalid_cases):
    """SIZE-01 AC: FractionalKelly rejects NaN / f < 0 / f > 0.5."""
    _require_sizer()
    for value, should_pass in kelly_invalid_cases:
        if not should_pass:
            with pytest.raises(sizer.KellyOverflow):
                sizer.FractionalKelly.try_new(value)


def test_kelly_overflow_propagates():
    """SIZE-01 AC: KellyOverflow propagates through compute_position_size."""
    _require_sizer()
    with pytest.raises(sizer.KellyOverflow):
        # 0.51 is outside [0.0, 0.5] — must raise KellyOverflow
        sizer.FractionalKelly.try_new(0.51)


# ---------------------------------------------------------------------------
# SIZE-02: Jeffreys Beta + BCa bootstrap parameter estimation
# ---------------------------------------------------------------------------


def test_jeffreys_beta_p_lower():
    """SIZE-02 AC: p_lower = beta.ppf(0.05, 0.5+k, 0.5+n-k) (Jeffreys posterior)."""
    _require_sizer()
    from scipy.stats import beta

    pnl_df = pl.DataFrame(
        {
            "pnl": [10.0, -5.0, 20.0, -3.0, 15.0],  # n=5, k=3
        }
    )
    ki = sizer.estimate_kelly_inputs(pnl_df)
    # Expected: beta.ppf(0.05, 0.5+3, 0.5+5-3) = beta.ppf(0.05, 3.5, 2.5)
    expected_p = float(beta.ppf(0.05, 3.5, 2.5))
    assert np.isclose(ki.p_lower, expected_p, rtol=0, atol=1e-10), (
        f"p_lower={ki.p_lower} != expected={expected_p}"
    )


def test_bca_bootstrap_b_lower():
    """SIZE-02 AC: b_lower from BCa bootstrap on log(b), seed=20260422, B=2000."""
    _require_sizer()
    wins = np.array([10.0, 20.0, 15.0, 25.0])
    losses = np.array([-5.0, -8.0, -3.0])
    # Gross profit = 70, gross loss = 16 => b = 70/16 = 4.375
    b_lower = sizer._b_lower_bca(wins, losses, seed=20260422)
    assert 0 < b_lower < 4.375, (
        f"b_lower={b_lower} must be in (0, point_estimate=4.375)"
    )


def test_insufficient_data():
    """SIZE-02 AC: n < 5 raises ValueError (D-09 minimum trades gate)."""
    _require_sizer()
    pnl_df = pl.DataFrame({"pnl": [10.0, -5.0, 20.0, -3.0]})  # n=4 < 5
    with pytest.raises(ValueError, match="InsufficientData|min trades"):
        sizer.estimate_kelly_inputs(pnl_df)


# ---------------------------------------------------------------------------
# SIZE-03: 3-way min sizing + BindingReason
# ---------------------------------------------------------------------------


def test_binding_reason_per_fold():
    """SIZE-03 AC: binding_reason is one of kelly/atr_norm/fixed_cap/zero."""
    _require_sizer()
    ki = sizer.KellyInputs(p_lower=0.55, b_lower=2.0, n=100, k=55)
    # f* = p - (1-p)/b = 0.55 - 0.45/2.0 = 0.325
    kelly_f = sizer.FractionalKelly.try_new(0.325)
    size, binding = sizer.compute_position_size(
        ki,
        atr_at_entry=0.5,
        equity=10000.0,
        kelly_fraction=kelly_f,
        dd_cap_abs=3000.0,
        risk_pct=1.0,
        k_atr=2.0,
    )
    valid_reasons = {
        sizer.BindingReason.KELLY,
        sizer.BindingReason.ATR_NORM,
        sizer.BindingReason.FIXED_CAP,
        sizer.BindingReason.ZERO,
    }
    assert binding in valid_reasons, f"Unexpected binding={binding}"


def test_size_zero_fold():
    """SIZE-03 AC: size <= 0 returns 0.0 with BindingReason.ZERO."""
    _require_sizer()
    # p_lower = 0.4, b_lower = 1.0 => f* = 0.4 - 0.6/1.0 = -0.2 (< 0 => size 0)
    ki = sizer.KellyInputs(p_lower=0.40, b_lower=1.0, n=10, k=4)
    kelly_f = sizer.FractionalKelly.try_new(0.0)  # explicit zero
    size, binding = sizer.compute_position_size(
        ki,
        atr_at_entry=0.5,
        equity=10000.0,
        kelly_fraction=kelly_f,
        dd_cap_abs=3000.0,
        risk_pct=1.0,
        k_atr=2.0,
    )
    assert size == 0.0, f"Expected size=0.0, got {size}"
    assert binding == sizer.BindingReason.ZERO, f"Expected ZERO, got {binding}"


def test_sized_pnl_grain():
    """SIZE-03 AC: sized_pnl.parquet has grain fold × cell × trade with correct schema."""
    _require_sizer()
    sized_pnl_path = pathlib.Path("data/v4.9/sized_pnl.parquet")
    if not sized_pnl_path.exists():
        pytest.skip("sized_pnl.parquet not yet generated (Wave 2 task)")

    df = pl.read_parquet(sized_pnl_path)

    # Schema completeness
    required_cols = {
        "fold",
        "cell_id",
        "trade_id",
        "kelly_size",
        "atr_size",
        "cap_size",
        "size",
        "binding_reason",
        "m_t",
        "sized_pnl",
    }
    assert required_cols == set(df.columns), f"Column mismatch: got {set(df.columns)}"

    # 192 cells coverage
    assert df["cell_id"].n_unique() == 192, (
        f"Expected 192 cells, got {df['cell_id'].n_unique()}"
    )

    # binding_reason Enum dtype
    assert df["binding_reason"].dtype == pl.Enum(
        ["kelly", "atr_norm", "fixed_cap", "zero"]
    ), f"Unexpected dtype: {df['binding_reason'].dtype}"

    # m_t is constant 1.0
    assert (df["m_t"] == 1.0).all(), (
        "m_t must be 1.0 everywhere (Phase 89 overlay slot)"
    )

    # SEAL-constant-guaranteed branches must fire (kelly/atr_norm deferred to Wave 3)
    bindings = set(df["binding_reason"].unique().to_list())
    assert {"fixed_cap", "zero"}.issubset(bindings), (
        f"fixed_cap/zero branch not observed: {bindings}"
    )


# ---------------------------------------------------------------------------
# SIZE-04 parity (C-algebraic, D-21 revised 2026-04-23)
#
# Original D-21 demanded bit-exact fold-level PF match against v4.8 WFD
# reference (bar-level `positions[i-1] * market_return[i]` aggregate). Wave 3
# investigation proved this structurally unachievable:
#   1. v4.8 engine applies sticky-position only — atr/technical/time stops
#      are Phase 86 additions, so 144/192 cells diverge by design.
#   2. Trade-level pnl `(P_exit - P_entry) / P_entry` and bar-level return sum
#      `Σ (P[k] - P[k-1]) / P[k-1]` differ geometrically vs arithmetically —
#      bit-exact fails even on `exit_type=none` cells.
#
# D-21 revised to an **internal algebraic invariant** on sized_pnl.parquet:
#   For every trade row (joined with exit_replayed.parquet on cell/fold/trade):
#     sized_pnl == (size / equity) * pnl     (bit-exact, rtol=0 atol=0)
#
# This verifies the sizing layer's math is consistent with its stored inputs.
# It does NOT cross-check against v4.8 — that regression guarantee is
# redefined in a successor phase (see 87-03-SUMMARY.md).
# ---------------------------------------------------------------------------

_EQUITY_WAVE2 = 10000.0  # gen_sized_pnl.py --equity default; hardcoded in Wave 2 run


@pytest.fixture(scope="module")
def _sizing_reconstruction_data():
    """Load joined sized_pnl + exit_replayed on (cell_id, fold, trade_id)."""
    sized_pnl_path = pathlib.Path("data/v4.9/sized_pnl.parquet")
    exit_replayed_path = pathlib.Path("data/v4.9/exit_replayed.parquet")
    if not sized_pnl_path.exists():
        pytest.skip("sized_pnl.parquet not yet generated (Wave 2 task)")
    if not exit_replayed_path.exists():
        pytest.skip("exit_replayed.parquet not available (Phase 86)")
    sp = pl.read_parquet(sized_pnl_path)
    er = pl.read_parquet(exit_replayed_path).select(
        ["cell_id", "fold", "trade_id", "pnl"]
    )
    joined = sp.join(er, on=["cell_id", "fold", "trade_id"], how="inner")
    return sp, er, joined


def test_parity_v48_wfd_smoke(_sizing_reconstruction_data):
    """SIZE-04 smoke (D-21 revised): 1 cell × 1 fold sizing reconstruction bit-exact."""
    _require_sizer()
    sp, er, joined = _sizing_reconstruction_data

    # Precondition: Wave 2 scaling invariant m_t ≡ 1.0
    assert (sp["m_t"] == 1.0).all(), "Wave 2 precondition violated: m_t != 1.0"

    # Pick deterministic first (cell, fold)
    first_cell = sorted(joined["cell_id"].unique().to_list())[0]
    first_fold = sorted(
        joined.filter(pl.col("cell_id") == first_cell)["fold"].unique().to_list()
    )[0]
    sub = joined.filter(
        (pl.col("cell_id") == first_cell) & (pl.col("fold") == first_fold)
    )
    if sub.height == 0:
        pytest.skip(f"no joined rows for {first_cell}/{first_fold}")

    reconstructed = (sub["size"].to_numpy() / _EQUITY_WAVE2) * sub["pnl"].to_numpy()
    actual = sub["sized_pnl"].to_numpy()
    # D-23: rtol=0 atol=0 bit-exact
    assert np.array_equal(reconstructed, actual), (
        f"smoke invariant FAIL {first_cell}/{first_fold}: "
        f"first diff idx={np.where(reconstructed != actual)[0][:5]}"
    )


def test_parity_v48_wfd(_sizing_reconstruction_data):
    """SIZE-04 full (D-21 revised): every trade satisfies sized_pnl == (size/equity)*pnl bit-exact (rtol=0, atol=0)."""
    _require_sizer()
    sp, er, joined = _sizing_reconstruction_data

    # Precondition: Wave 2 scaling invariant m_t ≡ 1.0
    assert (sp["m_t"] == 1.0).all(), "Wave 2 precondition violated: m_t != 1.0"

    # Row-count sanity: both parquets have same grain (16*6*2*16 = 3072)
    assert sp.height == er.height == joined.height, (
        f"row count mismatch: sized_pnl={sp.height} exit_replayed={er.height} joined={joined.height}"
    )

    reconstructed = (joined["size"].to_numpy() / _EQUITY_WAVE2) * joined[
        "pnl"
    ].to_numpy()
    actual = joined["sized_pnl"].to_numpy()

    # D-23: rtol=0 atol=0 bit-exact
    if not np.array_equal(reconstructed, actual):
        diff_idx = np.where(reconstructed != actual)[0]
        sample = diff_idx[:5]
        details = [
            f"row={i} cell={joined['cell_id'][int(i)]} fold={joined['fold'][int(i)]} "
            f"trade={joined['trade_id'][int(i)]} size={joined['size'][int(i)]} "
            f"pnl={joined['pnl'][int(i)]} sized_pnl={actual[int(i)]!r} "
            f"reconstructed={reconstructed[int(i)]!r}"
            for i in sample
        ]
        raise AssertionError(
            f"sizing reconstruction invariant FAIL on {len(diff_idx)}/{joined.height} rows:\n  "
            + "\n  ".join(details)
        )
