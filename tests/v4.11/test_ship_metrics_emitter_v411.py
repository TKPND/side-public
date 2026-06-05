"""Phase 95 Plan 2 Task 3: pytest for ship_metrics_emitter_v411.py.

Acceptance (D-51..D-54):
  - test_data_provenance_literal: ship_metrics.data_provenance ==
    "vol-regime-v411-f8ccc8a" exact literal (D-53)
  - test_sextuple_pin_seven_keys: sextuple_pin_stamp has 7 keys
    (6 anchors + data_provenance)
  - test_permutation_null_block_complete: top-level `permutation_null`
    has all required keys (D-54)
  - test_ship_verdict_conjunction_false_when_observed_zero: with
    edge_count=0 → ship_verdict=False regardless of perm result
  - test_overlay_evaluation_carried_untouched: active overlay_evaluation
    == neutral overlay_evaluation bit-exact (D-06 section merge)
  - test_neutral_mode_not_modified: `git status --porcelain` on neutral
    file is empty before + after emit invocation
  - test_4_primary_metrics_exposed: top-level edge_count_p_adj_005 +
    primary_metrics.{turnover_sharpe_median, es_median}, plus
    ship_metrics.ship_verdict boolean.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ship_metrics_emitter_v411 import (
    _DATA_PROVENANCE,
    _SEXTUPLE_PIN_STAMP,
    build_ship_decision_doc,
    main as emit_main,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ACTIVE = _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "v4_11_ship_decision.json"
_NEUTRAL = (
    _REPO_ROOT / "reports" / "v4.11" / "neutral_mode" / "v4_11_ship_decision.json"
)


def _load_active() -> dict:
    assert _ACTIVE.exists(), "run ship_metrics_emitter_v411.py before these tests"
    return json.loads(_ACTIVE.read_text(encoding="utf-8"))


def test_data_provenance_literal() -> None:
    """D-53: exact literal, no back-fill allowed."""
    assert _DATA_PROVENANCE == "vol-regime-v411-f8ccc8a"
    doc = _load_active()
    assert doc["ship_metrics"]["data_provenance"] == "vol-regime-v411-f8ccc8a"


def test_sextuple_pin_seven_keys() -> None:
    """6 anchors + data_provenance = 7 keys."""
    assert len(_SEXTUPLE_PIN_STAMP) == 7
    assert set(_SEXTUPLE_PIN_STAMP.keys()) == {
        "threshold_commit",
        "regime_commit",
        "sizing_exit_commit",
        "sizing_exit_commit_v410",
        "signal_commit_v411",
        "engine_commit",
        "data_provenance",
    }
    # signal_commit_v411 must match the full sha256 literal
    assert (
        _SEXTUPLE_PIN_STAMP["signal_commit_v411"]
        == "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae"
    )
    doc = _load_active()
    stamp = doc["ship_metrics"]["sextuple_pin_stamp"]
    assert len(stamp) == 7
    assert stamp["signal_commit_v411"].startswith("f8ccc8a8")
    assert stamp["engine_commit"] == "a5a1102"


def test_permutation_null_block_complete() -> None:
    """D-54 additive block must carry all required fields."""
    doc = _load_active()
    assert "permutation_null" in doc
    pn = doc["permutation_null"]
    required = {
        "B",
        "seed",
        "shuffle_unit",
        "observed_edge_count_p_adj_005",
        "null_percentiles",
        "ship_condition_met",
        "n_tested",
        "n_padded",
        "kill_switch_consumed",
    }
    assert required <= set(pn.keys()), f"missing: {required - set(pn.keys())}"
    assert pn["B"] == 2000
    assert pn["seed"] == 20260425
    assert pn["shuffle_unit"] == "cell"
    assert set(pn["null_percentiles"].keys()) >= {"p50", "p95", "p99"}


def test_ship_verdict_conjunction_false_when_observed_zero() -> None:
    """Phase 93 degenerate: observed=0 → ship_verdict=False structurally."""
    doc = _load_active()
    if doc["ship_metrics"]["edge_count_p_adj_005"] == 0:
        assert doc["ship_metrics"]["ship_verdict"] is False
    # And ship_condition_met must be False when observed==p95==0 (D-50 tie fail)
    if (
        doc["permutation_null"]["observed_edge_count_p_adj_005"] == 0
        and doc["permutation_null"]["null_percentiles"]["p95"] == 0.0
    ):
        assert doc["permutation_null"]["ship_condition_met"] is False


def test_overlay_evaluation_carried_untouched() -> None:
    """D-06 section merge: overlay_evaluation bit-exact from neutral."""
    active = _load_active()
    neutral = json.loads(_NEUTRAL.read_text(encoding="utf-8"))
    assert active["overlay_evaluation"] == neutral["overlay_evaluation"]


def test_neutral_mode_not_modified() -> None:
    """D-51 / PARITY-V411-01: neutral ship_decision file must be untouched.

    Check git status (--porcelain) reports no modification for that path
    before AND after running the emitter.
    """

    def _porcelain(p: Path) -> str:
        r = subprocess.run(
            ["git", "status", "--porcelain", str(p)],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        return r.stdout.strip()

    before = _porcelain(_NEUTRAL)
    assert before == "", f"neutral already dirty before emit: {before!r}"
    emit_main()
    after = _porcelain(_NEUTRAL)
    assert after == "", f"emitter modified neutral: {after!r}"


def test_build_doc_deterministic() -> None:
    """Pure build (no write) is deterministic for the same upstream inputs."""
    d1 = build_ship_decision_doc()
    d2 = build_ship_decision_doc()
    assert d1 == d2


def test_primary_metrics_has_four_fields() -> None:
    """D-52: 4 primary metrics present.

    Top-level: edge_count_p_adj_005, ship_verdict, coverage_tier.
    primary_metrics: edge_count_p_adj_005 + turnover_sharpe_median + es_median.
    """
    doc = _load_active()
    sm = doc["ship_metrics"]
    assert "edge_count_p_adj_005" in sm
    assert "ship_verdict" in sm
    assert "coverage_tier" in sm
    pm = sm["primary_metrics"]
    assert "edge_count_p_adj_005" in pm
    assert "turnover_sharpe_median" in pm
    assert "es_median" in pm
    # verdict conjunction — must be a bool
    assert isinstance(sm["ship_verdict"], bool)
