"""
test_ship_emit_v412.py — Phase 103 Wave 2 GREEN tests for SHIP-V412-04/05 + D-27.

5 tests covering v412_ship_emit.py:
  - test_ship_decision_schema: top-level keys + 4 primary metrics keys
  - test_septuple_pin_stamp_7_anchors: 7 anchors embed (Plan 103-05 schema test)
  - test_ship_emit_v412_data_provenance_format: data_provenance == macro-stance-v412-9160234
  - test_honest_null_ship_v4: D-27 kill_switch_fired=true → null_ship_v4_close=true
  - test_ship_emit_v412_strict_inequality_observed_greater_than_p95: D-50 boundary
  - test_ship_emit_v412_four_primary_metrics_present: 4 primary metrics enumeration

Citations: 103-05-PLAN.md Tasks 1+2, SHIP-V412-04 (schema), SHIP-V412-05 (D-27),
D-06 (schema carry from v4.10), D-27 (honest null-ship-v4 closure),
D-50 (strict inequality observed > p95).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT = _REPO_ROOT / "reports" / "v4.12" / "v4_12_ship_decision.json"
_EMITTER = _REPO_ROOT / "scripts" / "v4.12" / "v412_ship_emit.py"


def _import_emitter():
    """Fresh-import v412_ship_emit module (avoids module cache between monkeypatch tests)."""
    spec = importlib.util.spec_from_file_location("v412_ship_emit", _EMITTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ship_decision_schema():
    """SHIP-V412-04: v4_12_ship_decision.json schema = D-06 (v4.10 carry)
    + 4 primary metrics + data_provenance=macro-stance-v412-<sha7>
    + D-50 strict inequality `observed > p95`."""
    mod = _import_emitter()
    mod.main()
    assert _OUTPUT.exists(), f"Missing {_OUTPUT}"
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    for key in (
        "schema_version",
        "overlay_evaluation",
        "ship_metrics",
        "permutation_null",
    ):
        assert key in doc, f"Missing top-level key: {key}"
    assert doc["schema_version"] == "v4.12.0"
    primary = doc["ship_metrics"]["primary_metrics"]
    assert "edge_count_p_adj_005" in primary
    assert "turnover_sharpe_median" in primary
    assert "es_median" in primary
    # 4th metric per D-06 schema (carry from v4.10): pf_cost_adj_median or calmar_median
    assert len(primary) >= 4, (
        f"primary_metrics must have >=4 keys, got {list(primary.keys())}"
    )


def test_septuple_pin_stamp_7_anchors():
    """SHIP-V412-04: septuple-pin 7 anchors 全 embed (threshold + regime + sizing_exit
    + sizing_exit_v410 + signal_v411 + signal_v412 + engine)."""
    mod = _import_emitter()
    mod.main()
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    stamp = doc["ship_metrics"]["septuple_pin_stamp"]
    expected = {
        "threshold_commit",
        "regime_commit",
        "sizing_exit_commit",
        "sizing_exit_commit_v410",
        "signal_commit_v411",
        "signal_commit_v412",
        "engine_commit",
    }
    assert set(stamp.keys()) == expected, (
        f"Missing/extra anchors: {set(stamp.keys()) ^ expected}"
    )
    assert (
        stamp["signal_commit_v412"]
        == "91602348c0e08a3216d914dc159a48112f8fab64ccf8cce9464fdf7814a96555"
    ), f"signal_commit_v412 mismatch: {stamp['signal_commit_v412']}"


def test_honest_null_ship_v4(tmp_path, monkeypatch):
    """SHIP-V412-05 + D-27: kill_switch_fired=true → ship_verdict=false
    + null_ship_v4_close=true (legitimate outcome, not failure)."""
    p_adj = tmp_path / "p_adj.json"
    p_adj.write_text(
        json.dumps(
            {
                "schema_version": "v4.12",
                "provenance": {
                    "n_tested": 1,
                    "n_padded": 31,
                    "kill_switch_consumed": True,
                    "signal_commit_v412": "91602348c0e08a3216d914dc159a48112f8fab64ccf8cce9464fdf7814a96555",
                },
                "results": [
                    {
                        "cell_id": "c1",
                        "status": "tested",
                        "p_raw": 0.001,
                        "p_adj_holm": 0.01,
                    }
                ],
            }
        )
    )
    perm = tmp_path / "perm.json"
    perm.write_text(
        json.dumps(
            {
                "schema_version": "v4.12",
                "B": 2000,
                "seed": 20260601,
                "shuffle_unit": "stance",
                "observed_edge_count_p_adj_005": 5,
                "null_percentiles": {"p50": 1, "p95": 3, "p99": 4},
                "ship_condition_met": True,  # observed=5 > p95=3
            }
        )
    )
    kill = tmp_path / "kill.json"
    kill.write_text(json.dumps({"kill_switch_fired": True}))

    output = tmp_path / "ship.json"
    mod = _import_emitter()
    monkeypatch.setattr(mod, "_P_ADJ", p_adj)
    monkeypatch.setattr(mod, "_PERM_NULL", perm)
    monkeypatch.setattr(mod, "_KILL_SWITCH", kill)
    monkeypatch.setattr(mod, "_OUTPUT", output)

    mod.main()
    doc = json.loads(output.read_text(encoding="utf-8"))
    assert doc["ship_metrics"]["ship_verdict"] is False, (
        "kill_switch_fired=true なのに ship_verdict=true は D-27 違反"
    )
    assert doc["ship_metrics"]["null_ship_v4_close"] is True, (
        "kill_switch_fired=true 時は null_ship_v4_close=true (D-27 legitimate)"
    )


def test_ship_emit_v412_data_provenance_format():
    """D-06: data_provenance string format = `macro-stance-v412-<sha7>`
    where sha7 derives from signal_commit_v412 canonical_sha256."""
    mod = _import_emitter()
    mod.main()
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    assert doc["ship_metrics"]["data_provenance"] == "macro-stance-v412-9160234", (
        f"data_provenance mismatch: {doc['ship_metrics']['data_provenance']}"
    )


def test_ship_emit_v412_strict_inequality_observed_greater_than_p95(
    tmp_path, monkeypatch
):
    """D-50: ship_verdict computation uses `observed > p95` (strict).
    Boundary case `observed == p95` → ship_condition_met=false → ship_verdict=false."""
    p_adj = tmp_path / "p_adj.json"
    p_adj.write_text(
        json.dumps(
            {
                "schema_version": "v4.12",
                "provenance": {
                    "n_tested": 1,
                    "n_padded": 31,
                    "kill_switch_consumed": False,
                    "signal_commit_v412": "91602348c0e08a3216d914dc159a48112f8fab64ccf8cce9464fdf7814a96555",
                },
                "results": [
                    {
                        "cell_id": "c1",
                        "status": "tested",
                        "p_raw": 0.001,
                        "p_adj_holm": 0.01,
                    }
                ],
            }
        )
    )
    perm = tmp_path / "perm.json"
    # Boundary: observed == p95 → strict > false (D-50)
    perm.write_text(
        json.dumps(
            {
                "schema_version": "v4.12",
                "B": 2000,
                "seed": 20260601,
                "shuffle_unit": "stance",
                "observed_edge_count_p_adj_005": 3,
                "null_percentiles": {"p50": 1, "p95": 3, "p99": 4},
                "ship_condition_met": False,  # observed=3 == p95=3 → False (strict)
            }
        )
    )
    kill = tmp_path / "kill.json"
    kill.write_text(json.dumps({"kill_switch_fired": False}))

    output = tmp_path / "ship.json"
    mod = _import_emitter()
    monkeypatch.setattr(mod, "_P_ADJ", p_adj)
    monkeypatch.setattr(mod, "_PERM_NULL", perm)
    monkeypatch.setattr(mod, "_KILL_SWITCH", kill)
    monkeypatch.setattr(mod, "_OUTPUT", output)

    mod.main()
    doc = json.loads(output.read_text(encoding="utf-8"))
    assert doc["permutation_null"]["ship_condition_met"] is False
    assert (
        doc["permutation_null"]["ship_condition_rule"]
        == "observed > p95 (D-50 strict inequality)"
    )
    # ship_condition_met=False なので ship_verdict=False (kill=False, edge>0 でも)
    assert doc["ship_metrics"]["ship_verdict"] is False


def test_ship_emit_v412_four_primary_metrics_present():
    """SHIP-V412-04: ship_decision contains exactly 4 primary metrics
    (carry from v4.11 emitter conventions, signal_commit_v412 header).
    Per D-06 v4.10 schema: edge_count_p_adj_005, turnover_sharpe_median, es_median, pf_cost_adj_median."""
    mod = _import_emitter()
    mod.main()
    doc = json.loads(_OUTPUT.read_text(encoding="utf-8"))
    primary = doc["ship_metrics"]["primary_metrics"]
    expected_keys = {
        "edge_count_p_adj_005",
        "turnover_sharpe_median",
        "es_median",
        "pf_cost_adj_median",
    }
    assert set(primary.keys()) == expected_keys, (
        f"primary_metrics keys mismatch: got {set(primary.keys())}, expected {expected_keys}"
    )
    assert len(primary) == 4
