"""Tests for v5.1 Phase 116 empty-candidate verdict artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "scripts")
import v5_1_oos_verdict as phase116  # noqa: E402


def _phase115_summary(**overrides: object) -> dict[str, object]:
    summary: dict[str, object] = {
        "phase": 115,
        "schema_version": "v5.1.phase115.1",
        "phase115_blocked": False,
        "blocker_reason": None,
        "eligible_cells": [],
        "fwer_denominator": 216,
        "entry_granularity": "30s_bar",
        "pairs": {
            "BTCUSD": {
                "cell_count": 108,
                "eligible_cell_count": 0,
                "sparse_fail_close_count": 0,
            },
            "ETHUSD": {
                "cell_count": 108,
                "eligible_cell_count": 0,
                "sparse_fail_close_count": 0,
            },
        },
    }
    summary.update(overrides)
    return summary


def test_constants_match_v5_1_claim_seal() -> None:
    assert phase116.PAIRS == ("BTCUSD", "ETHUSD")
    assert phase116.OOS_START == "2025-11-01T00:00:00Z"
    assert phase116.OOS_END_EXCLUSIVE == "2026-05-01T00:00:00Z"
    assert phase116.OOS_END_DISPLAY == "2026-04-30"
    assert phase116.OOS_PF_HURDLE == 1.5
    assert phase116.PERMUTATION_B == 2000
    assert phase116.PERMUTATION_SEED == 515113
    assert phase116.DSR_N_TRIALS == 216
    assert phase116.DSR_ALPHA == 0.05
    assert phase116.DSR_PROBABILITY_THRESHOLD == 0.95


def test_empty_phase115_candidates_build_null_ship_without_oos_execution() -> None:
    summary = _phase115_summary()

    verdict = phase116.build_final_verdict(
        summary,
        phase115_summary_path="reports/v5.1/is_backtest_fwer_summary.json",
    )

    assert verdict["phase"] == 116
    assert verdict["ship_verdict"] is False
    assert verdict["verdict"] == "null_ship"
    assert verdict["phase115_blocked"] is False
    assert verdict["candidate_count"] == 0
    assert verdict["normal_oos_executed"] is False
    assert verdict["null_ship_reasons"] == ["empty_phase116_candidate_set"]
    assert set(verdict["pairs"]) == {"BTCUSD", "ETHUSD"}
    assert verdict["pairs"]["BTCUSD"]["evaluated_cell_count"] == 0
    assert verdict["pairs"]["ETHUSD"]["evaluated_cell_count"] == 0
    assert verdict["pairs"]["BTCUSD"]["any_cell_all_phase116_gates_passed"] is False
    assert verdict["provenance"]["sealed_constants"] == {
        "fwer_denominator": 216,
        "entry_granularity": "30s_bar",
        "oos_start": "2025-11-01T00:00:00Z",
        "oos_end_exclusive": "2026-05-01T00:00:00Z",
        "oos_pf_hurdle": 1.5,
        "permutation_b": 2000,
        "permutation_seed": 515113,
        "permutation_shuffle_unit": "stance_label",
        "dsr_n_trials": 216,
        "dsr_alpha": 0.05,
        "dsr_probability_threshold": 0.95,
    }


def test_phase115_blocker_fails_closed_before_candidate_gates() -> None:
    summary = _phase115_summary(
        phase115_blocked=True,
        blocker_reason="quote_anchor_grid_failed",
        eligible_cells=[],
    )

    verdict = phase116.build_final_verdict(
        summary,
        phase115_summary_path="phase115.json",
    )

    assert verdict["ship_verdict"] is False
    assert verdict["verdict"] == "null_ship"
    assert verdict["phase115_blocked"] is True
    assert verdict["normal_oos_executed"] is False
    assert verdict["null_ship_reasons"] == ["phase115_blocked:quote_anchor_grid_failed"]


def test_write_outputs_emits_empty_permutation_dsr_and_markdown_docs(tmp_path: Path) -> None:
    verdict = phase116.build_final_verdict(
        _phase115_summary(),
        phase115_summary_path="phase115.json",
    )

    phase116.write_outputs(verdict, output_dir=tmp_path)

    final_json = json.loads((tmp_path / "final_verdict.json").read_text())
    permutation = json.loads((tmp_path / "permutation_null.json").read_text())
    dsr = json.loads((tmp_path / "dsr_summary.json").read_text())
    final_md = (tmp_path / "final_verdict.md").read_text()
    docs_md = (tmp_path / "v5.1_tick_imbalance_verdict.md").read_text()

    assert final_json == verdict
    assert permutation["permutation_b"] == 2000
    assert permutation["permutation_seed"] == 515113
    assert permutation["normal_oos_executed"] is False
    assert permutation["pairs"] == {"BTCUSD": [], "ETHUSD": []}
    assert dsr["dsr_n_trials"] == 216
    assert dsr["pairs"] == {"BTCUSD": [], "ETHUSD": []}
    assert "ship_verdict | False" in final_md
    assert "`empty_phase116_candidate_set`" in final_md
    assert docs_md == final_md
