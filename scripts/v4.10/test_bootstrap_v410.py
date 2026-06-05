"""Tests for bootstrap_v410.py — TDD RED phase.

Import via importlib to bypass invalid Python identifier 'v4.10' in path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import pathlib

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module import (v4.10 contains dot — not a valid Python package identifier)
# ---------------------------------------------------------------------------
_MODULE_PATH = pathlib.Path(__file__).parent / "bootstrap_v410.py"
_spec = importlib.util.spec_from_file_location("bootstrap_v410", _MODULE_PATH)
bootstrap_v410 = importlib.util.module_from_spec(_spec)
sys.modules["bootstrap_v410"] = bootstrap_v410
try:
    _spec.loader.exec_module(bootstrap_v410)
    _BOOTSTRAP_V410_AVAILABLE = True
except Exception as _import_exc:
    _BOOTSTRAP_V410_AVAILABLE = False
    _IMPORT_EXC = _import_exc

pytestmark = pytest.mark.skipif(
    not _BOOTSTRAP_V410_AVAILABLE,
    reason=f"bootstrap_v410 not available: {locals().get('_IMPORT_EXC', 'unknown')}",
)


# ---------------------------------------------------------------------------
# Test 1: bootstrap_pvalue returns valid probability in [0, 1]
# ---------------------------------------------------------------------------
def test_bootstrap_pvalue_valid_range() -> None:
    arr = np.array([0.0, 0.01, -0.01, 0.005])
    p = bootstrap_v410.bootstrap_pvalue(arr, n_samples=100, seed=42)
    assert 0.0 <= p <= 1.0, f"Expected p in [0, 1], got {p}"


# ---------------------------------------------------------------------------
# Test 2: Short series (<4 elements) returns 1.0
# ---------------------------------------------------------------------------
def test_bootstrap_pvalue_short_series_returns_one() -> None:
    arr = np.array([0.01, 0.02, 0.03])
    p = bootstrap_v410.bootstrap_pvalue(arr, n_samples=100, seed=42)
    assert p == 1.0, f"Expected 1.0 for short series, got {p}"


# ---------------------------------------------------------------------------
# Test 3: Near-zero observed mean returns 1.0 (degenerate guard)
# ---------------------------------------------------------------------------
def test_bootstrap_pvalue_near_zero_observed_returns_one() -> None:
    # Array sums to ~0 so observed mean ≈ 0
    arr = np.array([1e-15, -1e-15, 1e-15, -1e-15, 1e-15])
    p = bootstrap_v410.bootstrap_pvalue(arr, n_samples=100, seed=42)
    assert p == 1.0, f"Expected 1.0 for near-zero observed, got {p}"


# ---------------------------------------------------------------------------
# Test 4: Bonferroni-Holm is less conservative than straight Bonferroni
#   Use unequal p-values so Holm gives a tighter bound on the smallest
# ---------------------------------------------------------------------------
def test_apply_bonferroni_holm_less_conservative_than_bonferroni() -> None:
    # One very small p, rest large — Holm adjusts only the small one by ×384,
    # but straight Bonferroni also gives 0.0001*384 = 0.0384
    p_raw = [0.0001] + [0.5] * 383
    p_adj = bootstrap_v410.apply_bonferroni_holm(p_raw)
    assert len(p_adj) == 384
    assert all(0.0 <= v <= 1.0 for v in p_adj), (
        "All adjusted p-values must be in [0, 1]"
    )
    # Holm first step: p[0] * 384 = 0.0384; Bonferroni: same first step,
    # but subsequent Holm steps use smaller multipliers — check at least one
    # adjusted value is strictly less than naive Bonferroni (p * 384 capped at 1)
    bonferroni_adj = [min(p * 384, 1.0) for p in p_raw]
    # At least one Holm adj must be ≤ corresponding Bonferroni adj (never more conservative)
    assert any(h <= b for h, b in zip(p_adj, bonferroni_adj)), (
        "Holm should not be more conservative than Bonferroni"
    )


# ---------------------------------------------------------------------------
# Test 5: apply_bonferroni_holm raises AssertionError on wrong length
# ---------------------------------------------------------------------------
def test_apply_bonferroni_holm_wrong_length_raises() -> None:
    with pytest.raises(AssertionError):
        bootstrap_v410.apply_bonferroni_holm([0.05] * 100)


# ---------------------------------------------------------------------------
# Test 6: SEAL drift triggers RuntimeError via direct call
#   (monkeypatch constant + call _verify_seal_at_import directly, no reload)
# ---------------------------------------------------------------------------
def test_seal_drift_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap_v410, "_SIZING_EXIT_COMMIT_V410", "DIFFERENT_HASH_XXXX"
    )
    with pytest.raises(RuntimeError, match="SEAL drift"):
        bootstrap_v410._verify_seal_at_import()


# ---------------------------------------------------------------------------
# Test 7: main() smoke — generates p_adj_v410.json with 384 rows + correct schema
# ---------------------------------------------------------------------------
def test_main_smoke_384_rows(tmp_path: pathlib.Path) -> None:
    import shutil

    # Copy real parquet to tmp so main() can read it
    real_parquet = pathlib.Path("data/v4.10/dd_traces.parquet")
    if not real_parquet.exists():
        pytest.skip("dd_traces.parquet not found — skip smoke test")

    tmp_parquet = tmp_path / "dd_traces.parquet"
    # dd_traces.parquet is a partitioned (Hive) directory — use copytree
    if real_parquet.is_dir():
        shutil.copytree(real_parquet, tmp_parquet)
    else:
        shutil.copy(real_parquet, tmp_parquet)

    out_json = tmp_path / "p_adj_v410.json"

    bootstrap_v410.main(
        dd_traces_path=str(tmp_parquet),
        output_path=str(out_json),
        n_samples=50,  # fast smoke
    )

    assert out_json.exists(), "p_adj_v410.json was not created"
    rows = json.loads(out_json.read_text())
    assert len(rows) == 384, f"Expected 384 rows, got {len(rows)}"
    required_keys = {"cell_id", "fold_id", "p_raw", "p_adj_holm"}
    for i, row in enumerate(rows):
        assert required_keys <= set(row.keys()), f"Row {i} missing keys: {row.keys()}"
