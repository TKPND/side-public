"""Phase 106 Wave 0 RED scaffold — ABLATION-V413-03.

top_axis / secondary_axis literal pin + axis_cardinality literal pin
+ trivial_baseline_pathway boolean (D-106-02 hybrid tie-breaker)。

Analog: tests/v4_13/test_phase_105_evidence_sidecar.py::test_intended_threshold_scale_pinned
"""

from __future__ import annotations
import json
from pathlib import Path
import pytest

DIMENSIONS = ["pair", "fee_bps", "window", "regime_cuts", "sizing"]
EXPECTED_AXIS_CARDINALITY = {
    "pair": 1,
    "fee_bps": 1,
    "window": 80,
    "regime_cuts": 1,
    "sizing": 13,
}
EXPECTED_TOP_AXIS = "window"
EXPECTED_SECONDARY_AXIS = "sizing"


def _load_score(path: Path) -> dict:
    if not path.exists():
        pytest.fail(
            f"Wave 1 artifact 未 emit (Wave 0 RED expected): {path}\n"
            "Phase 106 Wave 1 で emit_ablation_v413.py が ablation_score.json を emit する."
        )
    return json.loads(path.read_bytes())


def test_top_axis_window(phase106_score_path: Path) -> None:
    """全 480 行 cardinality: window=80, sizing=13 → top/secondary 一意決定 (D-106-02)."""
    d = _load_score(phase106_score_path)
    assert d["top_axis"] == EXPECTED_TOP_AXIS
    assert d["secondary_axis"] == EXPECTED_SECONDARY_AXIS


def test_pathway_flag_true(phase106_score_path: Path) -> None:
    """trivial_baseline_pathway=true (Phase 107 短絡 trigger)."""
    d = _load_score(phase106_score_path)
    assert d["trivial_baseline_pathway"] is True


def test_axis_cardinality_pinned(phase106_score_path: Path) -> None:
    d = _load_score(phase106_score_path)
    assert d["axis_cardinality"] == EXPECTED_AXIS_CARDINALITY


def test_first_order_all_none_under_var_zero(phase106_score_path: Path) -> None:
    """Var[Y]=0 退化解 → first_order 全 axis が None (RFC 8259 準拠)."""
    d = _load_score(phase106_score_path)
    for axis in DIMENSIONS:
        assert d["first_order"][axis] is None, (
            f"first_order[{axis}] = {d['first_order'][axis]!r}"
        )


def test_total_order_all_none_under_var_zero(phase106_score_path: Path) -> None:
    d = _load_score(phase106_score_path)
    for axis in DIMENSIONS:
        assert d["total_order"][axis] is None, (
            f"total_order[{axis}] = {d['total_order'][axis]!r}"
        )
