"""Tests for scripts/v4.9/kelly_sensitivity.py (SIZE-04).

Phase 87 Wave 0: RED state — kelly_sensitivity.py not yet implemented.
All tests fail with ImportError until Wave 3.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Load kelly_sensitivity.py as module (Wave 3 will create this file)
# ---------------------------------------------------------------------------
_MODULE_PATH = pathlib.Path(__file__).parent / "kelly_sensitivity.py"
_spec = importlib.util.spec_from_file_location("kelly_sensitivity", _MODULE_PATH)
if _spec is not None and _spec.loader is not None:
    kelly_sensitivity = importlib.util.module_from_spec(_spec)
    sys.modules["kelly_sensitivity"] = kelly_sensitivity
    try:
        _spec.loader.exec_module(kelly_sensitivity)
        _KS_AVAILABLE = True
    except Exception:
        _KS_AVAILABLE = False
else:
    kelly_sensitivity = None  # type: ignore[assignment]
    _KS_AVAILABLE = False


def _require_ks():
    """Raise ImportError if kelly_sensitivity module is not available (RED gate)."""
    if not _KS_AVAILABLE or kelly_sensitivity is None:
        raise ImportError("kelly_sensitivity.py not yet implemented (Wave 3 task)")


# ---------------------------------------------------------------------------
# SIZE-04: Robust region gate
# ---------------------------------------------------------------------------


def test_robust_region():
    """SIZE-04 AC: 5-point grid all >= 0.25 => robust_pass = True."""
    _require_ks()

    # Load sizer for KellyInputs
    _sizer_path = pathlib.Path(__file__).parent / "sizer.py"
    _sizer_spec = importlib.util.spec_from_file_location("sizer", _sizer_path)
    if _sizer_spec is None or _sizer_spec.loader is None:
        pytest.skip("sizer.py not yet implemented (Wave 1 task)")
    sizer = importlib.util.module_from_spec(_sizer_spec)
    _sizer_spec.loader.exec_module(sizer)

    # Strong edge: p=0.60, b=3.0 => f* = 0.60 - 0.40/3.0 = 0.467
    ki = sizer.KellyInputs(p_lower=0.60, b_lower=3.0, n=100, k=60)
    grid = kelly_sensitivity.kelly_sensitivity_grid(ki, delta=0.10)
    assert all(f >= 0.25 for f in grid.values()), (
        f"All 5 grid points should pass gate >= 0.25, got: {grid}"
    )


def test_power_budget_schema():
    """SIZE-04 AC: power_budget_v49.json has correct schema (192 cells, required keys)."""
    _require_ks()

    budget_path = pathlib.Path("data/v4.9/power_budget_v49.json")
    if not budget_path.exists():
        pytest.skip("power_budget_v49.json not yet generated (Wave 3 task)")

    budget = json.loads(budget_path.read_text())

    # Required top-level keys (D-20)
    required_keys = {
        "sizing_exit_commit",
        "seed",
        "grid_points",
        "grid_delta",
        "kelly_fraction_min",
        "robust_region_pass_cells",
        "total_cells",
        "cells",
    }
    assert required_keys.issubset(set(budget.keys())), (
        f"Missing keys: {required_keys - set(budget.keys())}"
    )
    assert budget["total_cells"] == 192, f"total_cells={budget['total_cells']} != 192"
    assert len(budget["cells"]) == 192, f"cells count={len(budget['cells'])} != 192"


def test_skipped_reason_schema():
    """SIZE-04 AC: cell entries have skipped_reason field (Pitfall 3 — n<5 handling).

    Cells with insufficient data (n < 5 across all folds) must have
    skipped_reason: str (non-null) instead of sizing results.
    This prevents silent silencing of underpowered cells.
    """
    _require_ks()

    budget_path = pathlib.Path("data/v4.9/power_budget_v49.json")
    if not budget_path.exists():
        pytest.skip("power_budget_v49.json not yet generated (Wave 3 task)")

    budget = json.loads(budget_path.read_text())

    for cell_id, cell_entry in budget["cells"].items():
        # Each cell entry must have a skipped_reason key (may be null for normal cells)
        assert "skipped_reason" in cell_entry, (
            f"Cell {cell_id} missing 'skipped_reason' field (Pitfall 3 guard)"
        )
        # If skipped, skipped_reason must be a non-empty string
        sr = cell_entry["skipped_reason"]
        if sr is not None:
            assert isinstance(sr, str) and len(sr) > 0, (
                f"Cell {cell_id} skipped_reason is not a non-empty string: {sr!r}"
            )
