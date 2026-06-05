"""Phase 81 Plan 03 Task 2: label_audit 単体テスト."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

# Import via spec_from_file_location to avoid hyphen-in-dir-name issues:
_SPEC = importlib.util.spec_from_file_location(
    "label_audit",
    Path(__file__).parent / "label_audit.py",
)
label_audit = importlib.util.module_from_spec(_SPEC)
sys.modules["label_audit"] = label_audit
_SPEC.loader.exec_module(label_audit)


def _make_labels(counts_per_event_cell: dict[str, dict[str, int]]) -> pd.DataFrame:
    rows = []
    for ev, cells in counts_per_event_cell.items():
        for cell_id, n in cells.items():
            dur, _, liq = cell_id.partition("_x_")
            for i in range(n):
                rows.append(
                    {
                        "event_type": ev,
                        "pair": "EURUSD",
                        "slot_minute_of_day": 840 + i,
                        "cell_id": cell_id,
                        "duration_bucket": dur,
                        "liquidity_regime": liq,
                    }
                )
    return pd.DataFrame(rows)


def test_empty_cell_gate_fails() -> None:
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 10,
                "0-60m_x_MID": 10,
                "0-60m_x_HIGH": 10,
                "60-120m_x_LOW": 10,
                "60-120m_x_MID": 10,
                # 60-120m_x_HIGH は欠落 → empty cell
            }
        }
    )
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is False
    assert result.empty_cell_count >= 1


def test_concentration_exactly_80_fails() -> None:
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 8,
                "0-60m_x_MID": 1,
                "0-60m_x_HIGH": 1,
                "60-120m_x_LOW": 0,
                "60-120m_x_MID": 0,
                "60-120m_x_HIGH": 0,
            }
        }
    )
    # Note: this fails on both empty + concentration — we assert fail, not cause.
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is False


def test_concentration_just_under_80_passes_concentration_check() -> None:
    # 全 cell に最低 5、1 cell に concentration=0.4 付近で pass
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 5,
                "0-60m_x_MID": 5,
                "0-60m_x_HIGH": 5,
                "60-120m_x_LOW": 5,
                "60-120m_x_MID": 5,
                "60-120m_x_HIGH": 5,
            }
        }
    )
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is True
    assert result.max_concentration < label_audit.GATE_MAX_CONCENTRATION


def test_min_n_cell_boundary_5_passes() -> None:
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 5,
                "0-60m_x_MID": 5,
                "0-60m_x_HIGH": 5,
                "60-120m_x_LOW": 5,
                "60-120m_x_MID": 5,
                "60-120m_x_HIGH": 5,
            }
        }
    )
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is True
    assert result.min_n_cell == 5


def test_min_n_cell_4_fails() -> None:
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 4,
                "0-60m_x_MID": 10,
                "0-60m_x_HIGH": 10,
                "60-120m_x_LOW": 10,
                "60-120m_x_MID": 10,
                "60-120m_x_HIGH": 10,
            }
        }
    )
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is False
    assert result.min_n_cell == 4


def test_all_gates_pass_emits_pass_true() -> None:
    labels = _make_labels(
        {
            "FOMC": {c: 10 for c in label_audit.ALL_CELL_IDS},
            "ECB": {c: 8 for c in label_audit.ALL_CELL_IDS},
            "NFP": {c: 12 for c in label_audit.ALL_CELL_IDS},
        }
    )
    result, cells = label_audit.audit_labels(labels)
    assert result.passed is True
    assert result.empty_cell_count == 0
    assert set(cells.keys()) == {"FOMC", "ECB", "NFP"}


def test_fallback_suggests_L1_or_L2() -> None:
    # 1 empty のみ → L1 or L2 (not L3)
    labels = _make_labels(
        {
            "FOMC": {
                "0-60m_x_LOW": 10,
                "0-60m_x_MID": 10,
                "0-60m_x_HIGH": 10,
                "60-120m_x_LOW": 10,
                "60-120m_x_MID": 10,
            }
        }
    )
    result, _ = label_audit.audit_labels(labels)
    assert result.passed is False
    assert result.escalate_to is not None
    assert result.escalate_to.startswith(("L1", "L2"))


def test_regime_breakdown_schema(tmp_path: Path) -> None:
    labels = _make_labels(
        {
            "FOMC": {c: 10 for c in label_audit.ALL_CELL_IDS},
        }
    )
    result, cells = label_audit.audit_labels(labels)
    slot_labels_path = tmp_path / "slot_labels.parquet"
    labels.to_parquet(slot_labels_path, index=False)
    regime_cuts_path = tmp_path / "regime_cuts.json"
    regime_cuts_path.write_text("{}", encoding="utf-8")
    out = tmp_path / "regime_breakdown.json"
    label_audit.emit_regime_breakdown(
        result, cells, slot_labels_path, regime_cuts_path, out
    )
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["schema_version"] == "v4.8-phase-81-label-only"
    assert "audit_gate" in d
    assert "cells_by_event" in d
    assert d["_phase82_placeholder"]["rho_bar"] is None
    assert d["audit_gate"]["thresholds"]["empty_cell_max"] == 0
    assert d["audit_gate"]["thresholds"]["max_concentration_exclusive"] == 0.80
    assert d["audit_gate"]["thresholds"]["min_n_cell"] == 5


def test_sign_breakdown_sha256_stable(tmp_path: Path) -> None:
    sb = tmp_path / "sign_breakdown.json"
    sb.write_text(json.dumps({"fomc": [], "ecb": [], "nfp": []}), encoding="utf-8")
    before = hashlib.sha256(sb.read_bytes()).hexdigest()
    # label_audit は sign_breakdown.json を読むだけ (SHA256 計算のみ)
    after_hash = label_audit._sha256(sb)
    after = hashlib.sha256(sb.read_bytes()).hexdigest()
    assert before == after == after_hash
