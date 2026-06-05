"""Phase 82 Plan 02: wave1_distributional.py unit tests.

TDD RED phase — tests reference wave1_distributional which does not yet exist.

Tests cover:
- KappaResult dataclass fields
- compute_fleiss_kappa() scalar + pairwise
- BootstrapResult dataclass fields
- compute_bootstrap_ci() CI + reproducibility
- Edge cases: n_slots < 2, len(series) < 4, all-same-label
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# -- import via importlib (scripts/v4.8 has dot in path, not valid package) --
_SPEC = importlib.util.spec_from_file_location(
    "wave1_distributional", Path(__file__).parent / "wave1_distributional.py"
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["wave1_distributional"] = _MOD
_SPEC.loader.exec_module(_MOD)

KappaResult = _MOD.KappaResult
BootstrapResult = _MOD.BootstrapResult
compute_fleiss_kappa = _MOD.compute_fleiss_kappa
compute_bootstrap_ci = _MOD.compute_bootstrap_ci


# ---------------------------------------------------------------------------
# Task 1: KappaResult + compute_fleiss_kappa
# ---------------------------------------------------------------------------


class TestKappaResult:
    def test_compute_fleiss_kappa_returns_kappa_result(self):
        """Test 1: compute_fleiss_kappa(df) が KappaResult を返す"""
        df = pd.DataFrame(
            {"long": [10, 12, 8], "neutral": [5, 4, 6], "short": [3, 2, 4]}
        )
        result = compute_fleiss_kappa(df)
        assert isinstance(result, KappaResult)

    def test_kappa_scalar_is_float(self):
        """Test 2: KappaResult.scalar は float"""
        df = pd.DataFrame(
            {"long": [10, 12, 8], "neutral": [5, 4, 6], "short": [3, 2, 4]}
        )
        result = compute_fleiss_kappa(df)
        assert isinstance(result.scalar, float)

    def test_pairwise_has_three_keys(self):
        """Test 3: KappaResult.pairwise に 3 キーが存在する"""
        df = pd.DataFrame(
            {"long": [10, 12, 8], "neutral": [5, 4, 6], "short": [3, 2, 4]}
        )
        result = compute_fleiss_kappa(df)
        assert set(result.pairwise.keys()) == {
            "long_vs_neutral",
            "long_vs_short",
            "neutral_vs_short",
        }

    def test_n_slots_less_than_2_returns_zeros(self):
        """Test 4: n_slots < 2 の場合 kappa_scalar = 0.0, pairwise 全て 0.0"""
        df = pd.DataFrame({"long": [10], "neutral": [5], "short": [3]})
        result = compute_fleiss_kappa(df)
        assert result.scalar == 0.0
        assert all(v == 0.0 for v in result.pairwise.values())

    def test_empty_df_returns_zeros(self):
        """Test 4b: 空のDFでもゼロ返す"""
        df = pd.DataFrame({"long": [], "neutral": [], "short": []})
        result = compute_fleiss_kappa(df)
        assert result.scalar == 0.0
        assert all(v == 0.0 for v in result.pairwise.values())

    def test_all_same_label_returns_zero_fallback(self):
        """Test 5: 全スロット同一ラベルの場合 NaN → 0.0 フォールバック"""
        df = pd.DataFrame(
            {"long": [10, 10, 10], "neutral": [0, 0, 0], "short": [0, 0, 0]}
        )
        result = compute_fleiss_kappa(df)
        # kappa が 1.0 or NaN — NaN の場合は 0.0 フォールバック必須
        assert isinstance(result.scalar, float)
        assert not (result.scalar != result.scalar)  # not NaN


# ---------------------------------------------------------------------------
# Task 2: BootstrapResult + compute_bootstrap_ci
# ---------------------------------------------------------------------------


class TestBootstrapResult:
    def test_compute_bootstrap_ci_returns_bootstrap_result(self):
        """Test 1: compute_bootstrap_ci(series) が BootstrapResult を返す"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result = compute_bootstrap_ci(series)
        assert isinstance(result, BootstrapResult)

    def test_ci_95_is_length_2(self):
        """Test 2: BootstrapResult.ci_95 は長さ 2 のリスト [lower, upper]"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result = compute_bootstrap_ci(series)
        assert len(result.ci_95) == 2

    def test_block_len_is_float(self):
        """Test 3: BootstrapResult.block_len は float"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result = compute_bootstrap_ci(series)
        assert isinstance(result.block_len, float)

    def test_short_series_fallback(self):
        """Test 4: series 長 < 4 の場合 ci_95 = [0.0, 0.0], block_len = 1.0"""
        series = np.array([0.5, 0.3, 0.7])
        result = compute_bootstrap_ci(series)
        assert result.ci_95 == [0.0, 0.0]
        assert result.block_len == 1.0

    def test_empty_series_fallback(self):
        """Test 4b: 空 series でもフォールバック"""
        result = compute_bootstrap_ci(np.array([]))
        assert result.ci_95 == [0.0, 0.0]
        assert result.block_len == 1.0

    def test_seed42_reproducibility(self):
        """Test 5: seed=42 で結果が再現可能"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result1 = compute_bootstrap_ci(series)
        result2 = compute_bootstrap_ci(series)
        assert result1.ci_95 == result2.ci_95, "seed=42 must give identical results"

    def test_ci_lower_le_upper(self):
        """CI は lower <= upper"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result = compute_bootstrap_ci(series)
        assert result.ci_95[0] <= result.ci_95[1]

    def test_block_len_ge_1(self):
        """block_len >= 1.0"""
        series = np.array([0.5, 0.3, 0.7, 0.4, 0.6, 0.5, 0.4, 0.8, 0.3, 0.5])
        result = compute_bootstrap_ci(series)
        assert result.block_len >= 1.0
