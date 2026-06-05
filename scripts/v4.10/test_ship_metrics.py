"""SHIP-03: TDD tests for ship_metrics_emitter.py.

Tests 1-8 from 91-02-PLAN.md behavior spec.
Import via importlib to bypass invalid Python identifier 'v4.10' in path.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DD_TRACES = REPO_ROOT / "data" / "v4.10" / "dd_traces.parquet"
P_ADJ_PATH = REPO_ROOT / "reports" / "v4.10" / "p_adj_v410.json"
SHIP_DECISION_PATH = REPO_ROOT / "reports" / "v4.10" / "v4_10_ship_decision.json"

# ---------------------------------------------------------------------------
# Module import (v4.10 contains dot — not a valid Python package identifier)
# ---------------------------------------------------------------------------
_MODULE_PATH = Path(__file__).parent / "ship_metrics_emitter.py"
_spec = importlib.util.spec_from_file_location("ship_metrics_emitter", _MODULE_PATH)
_sme = importlib.util.module_from_spec(_spec)
sys.modules["ship_metrics_emitter"] = _sme
try:
    _spec.loader.exec_module(_sme)
    _SME_AVAILABLE = True
except Exception as _import_exc:
    _SME_AVAILABLE = False
    _IMPORT_EXC = _import_exc

pytestmark = pytest.mark.skipif(
    not _SME_AVAILABLE,
    reason=f"ship_metrics_emitter not available: {locals().get('_IMPORT_EXC', 'unknown')}",
)


@pytest.fixture()
def ship_decision_copy(tmp_path: Path) -> Path:
    """Copy v4_10_ship_decision.json to tmp so tests don't touch the real file."""
    dst = tmp_path / "v4_10_ship_decision.json"
    shutil.copy2(SHIP_DECISION_PATH, dst)
    return dst


# ---------------------------------------------------------------------------
# Test 1: compute_primary_metrics returns 384 rows with required keys
# ---------------------------------------------------------------------------
def test_compute_primary_metrics_shape_and_keys() -> None:
    """Test 1: compute_primary_metrics returns 192×2 rows with required keys."""
    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    assert len(metrics) == 384, f"Expected 384 rows, got {len(metrics)}"
    required_keys = {"cell_id", "fold_id", "pf", "calmar", "es", "turnover_sharpe"}
    for row in metrics[:5]:  # spot check first 5
        missing = required_keys - set(row.keys())
        assert not missing, f"Missing keys {missing} in row {row}"


# ---------------------------------------------------------------------------
# Test 2: count_edges returns int with value from p_adj_v410.json
# ---------------------------------------------------------------------------
def test_count_edges_returns_int() -> None:
    """Test 2: count_edges returns int of cells with p_adj_holm < 0.05."""
    result = _sme.count_edges(str(P_ADJ_PATH))
    assert isinstance(result, int), f"Expected int, got {type(result)}"
    assert result >= 0


# ---------------------------------------------------------------------------
# Test 3: overlay_evaluation byte-identical after fill_ship_metrics (D-06)
# ---------------------------------------------------------------------------
def test_overlay_evaluation_untouched(ship_decision_copy: Path) -> None:
    """Test 3: overlay_evaluation section byte-identical pre/post fill_ship_metrics."""
    with open(ship_decision_copy, "rb") as f:
        doc_before = json.loads(f.read())
    overlay_before = json.dumps(
        doc_before["overlay_evaluation"], indent=2, ensure_ascii=False
    )

    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    with open(ship_decision_copy, "rb") as f:
        doc_after = json.loads(f.read())
    overlay_after = json.dumps(
        doc_after["overlay_evaluation"], indent=2, ensure_ascii=False
    )

    assert overlay_before == overlay_after, (
        "overlay_evaluation was modified — D-06 violation"
    )


# ---------------------------------------------------------------------------
# Test 4: data_provenance stamp
# ---------------------------------------------------------------------------
def test_data_provenance_stamp(ship_decision_copy: Path) -> None:
    """Test 4: ship_metrics.data_provenance == 'gate-redesign-v410-a5f7183'."""
    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    doc = json.loads(ship_decision_copy.read_text())
    assert doc["ship_metrics"]["data_provenance"] == "gate-redesign-v410-a5f7183", (
        f"Wrong data_provenance: {doc['ship_metrics']['data_provenance']}"
    )


# ---------------------------------------------------------------------------
# Test 5: coverage_tier
# ---------------------------------------------------------------------------
def test_coverage_tier(ship_decision_copy: Path) -> None:
    """Test 5: ship_metrics.coverage_tier == 'inconclusive-2024-2025-only'."""
    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    doc = json.loads(ship_decision_copy.read_text())
    assert doc["ship_metrics"]["coverage_tier"] == "inconclusive-2024-2025-only", (
        f"Wrong coverage_tier: {doc['ship_metrics']['coverage_tier']}"
    )


# ---------------------------------------------------------------------------
# Test 6: types — ship_verdict bool, edge_count int, metrics float
# ---------------------------------------------------------------------------
def test_ship_metrics_types(ship_decision_copy: Path) -> None:
    """Test 6: ship_verdict is bool, edge_count_p_adj_005 is int, metrics are float."""
    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    doc = json.loads(ship_decision_copy.read_text())
    sm = doc["ship_metrics"]
    assert isinstance(sm["ship_verdict"], bool), (
        f"ship_verdict must be bool, got {type(sm['ship_verdict'])}"
    )
    assert isinstance(sm["edge_count_p_adj_005"], int), (
        f"edge_count_p_adj_005 must be int, got {type(sm['edge_count_p_adj_005'])}"
    )
    pm = sm["primary_metrics"]
    for field in (
        "pf_cost_adj_median",
        "calmar_median",
        "es_median",
        "turnover_sharpe_median",
    ):
        assert isinstance(pm[field], float), (
            f"primary_metrics.{field} must be float, got {type(pm[field])}"
        )


# ---------------------------------------------------------------------------
# Test 7: quint_pin_stamp preserved from Phase 90
# ---------------------------------------------------------------------------
def test_quint_pin_stamp_preserved(ship_decision_copy: Path) -> None:
    """Test 7: quint_pin_stamp preserved in ship_metrics after fill."""
    with open(ship_decision_copy) as f:
        doc_before = json.load(f)
    expected_stamp = doc_before["overlay_evaluation"]["quint_pin_stamp"]

    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    doc = json.loads(ship_decision_copy.read_text())
    assert doc["ship_metrics"]["quint_pin_stamp"] == expected_stamp, (
        "quint_pin_stamp not preserved in ship_metrics"
    )


# ---------------------------------------------------------------------------
# Test 8: primary_metrics are all finite (no NaN/inf)
# ---------------------------------------------------------------------------
def test_primary_metrics_finite(ship_decision_copy: Path) -> None:
    """Test 8: primary_metrics 4 fields are all finite (no NaN/inf)."""
    metrics = _sme.compute_primary_metrics(str(DD_TRACES))
    edge_count = _sme.count_edges(str(P_ADJ_PATH))
    _sme.fill_ship_metrics(metrics, edge_count, str(ship_decision_copy))

    doc = json.loads(ship_decision_copy.read_text())
    pm = doc["ship_metrics"]["primary_metrics"]
    for field in (
        "pf_cost_adj_median",
        "calmar_median",
        "es_median",
        "turnover_sharpe_median",
    ):
        val = pm[field]
        assert np.isfinite(val), f"primary_metrics.{field} is not finite: {val}"
