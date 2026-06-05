"""
test_n_per_cell_emit.py — Wave 0 contract for n_per_cell_audit.py (Task 100-00-01).

These tests define the D-C1 JSON shape and grid constants that Wave 1 task
100-01-02 (scripts/v4.12/n_per_cell_audit.py) MUST satisfy.

Wave 0 status:  n_per_cell_audit.py does not exist yet → tests skip/xfail
                with reason "PENDING Wave 1".

Wave 1 status:  once n_per_cell_audit.py is implemented, all 3 tests should PASS.
"""

import importlib.util
import json
from pathlib import Path

import pytest

# ── D-C1 locked grid (single source of truth: 100-PLAN.md <interfaces>) ──
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
NULL_SHIP_THRESHOLD_N_MIN = 20

# ── Module availability probe ────────────────────────────────────────────
_AUDIT_MODULE_PATH = (
    Path(__file__).parent.parent.parent / "scripts" / "v4.12" / "n_per_cell_audit.py"
)
_AUDIT_AVAILABLE = _AUDIT_MODULE_PATH.exists()
_PENDING_REASON = (
    "PENDING Wave 1: scripts/v4.12/n_per_cell_audit.py not yet implemented"
)


def _load_audit_module():
    """Dynamically load n_per_cell_audit.py; raises ImportError if absent."""
    spec = importlib.util.spec_from_file_location(
        "n_per_cell_audit", _AUDIT_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(_PENDING_REASON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Test 1: grid constants ────────────────────────────────────────────────
@pytest.mark.skipif(not _AUDIT_AVAILABLE, reason=_PENDING_REASON)
def test_grid_constants():
    """Asserts PAIRS / EVENTS / TOD / VOL_STANCE match locked D-C1 values."""
    mod = _load_audit_module()
    assert list(mod.PAIRS) == PAIRS, f"PAIRS mismatch: {mod.PAIRS}"
    assert list(mod.EVENTS) == EVENTS, f"EVENTS mismatch: {mod.EVENTS}"
    assert list(mod.TOD) == TOD, f"TOD mismatch: {mod.TOD}"
    assert list(mod.VOL_STANCE) == VOL_STANCE, f"VOL_STANCE mismatch: {mod.VOL_STANCE}"
    assert mod.EXPECTED_CELLS == EXPECTED_CELLS
    assert mod.NULL_SHIP_THRESHOLD_N_MIN == NULL_SHIP_THRESHOLD_N_MIN


# ── Test 2: emit smoke (1-cell --check mode) ─────────────────────────────
@pytest.mark.skipif(not _AUDIT_AVAILABLE, reason=_PENDING_REASON)
def test_emit_smoke_one_cell(dummy_classifier_parquet, tmp_path):
    """
    Invokes n_per_cell_audit.main(["--data-parquet", <fixture>, "--output",
    <tmp.json>, "--check"]) and asserts the output JSON shape.

    --check = Wave 0 smoke mode: emits 1 cell only per D-C1 wave-0 gate.
    """
    mod = _load_audit_module()
    output_file = tmp_path / "audit_out.json"
    mod.main(
        [
            "--data-parquet",
            str(dummy_classifier_parquet),
            "--output",
            str(output_file),
            "--check",
        ]
    )
    assert output_file.exists(), "n_per_cell_audit did not create output file"
    result = json.loads(output_file.read_text())

    # Required top-level keys per D-C1 / plan <behavior>
    assert "cells_raw_192" in result, f"Missing cells_raw_192 in output: {list(result)}"
    assert "marginals" in result, f"Missing marginals in output: {list(result)}"
    assert result.get("expected_cells") == EXPECTED_CELLS, (
        f"expected_cells mismatch: {result.get('expected_cells')}"
    )
    assert result.get("null_ship_threshold_n_min") == NULL_SHIP_THRESHOLD_N_MIN, (
        f"null_ship_threshold_n_min mismatch: {result.get('null_ship_threshold_n_min')}"
    )


# ── Test 3: full-grid runtime ≤30 s ─────────────────────────────────────
@pytest.mark.skipif(not _AUDIT_AVAILABLE, reason=_PENDING_REASON)
@pytest.mark.timeout(30)
def test_emit_runtime_under_30s(dummy_classifier_parquet, tmp_path):
    """
    Full-grid emit (no --check) must complete in ≤30 s on a dev box (D-C1 cost target).
    pytest-timeout enforces the wall-clock limit.
    """
    mod = _load_audit_module()
    output_file = tmp_path / "audit_full.json"
    mod.main(
        [
            "--data-parquet",
            str(dummy_classifier_parquet),
            "--output",
            str(output_file),
        ]
    )
    assert output_file.exists()
    result = json.loads(output_file.read_text())
    assert result.get("expected_cells") == EXPECTED_CELLS
