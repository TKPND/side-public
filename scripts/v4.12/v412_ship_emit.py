"""SHIP-V412-04/05: v4_12_ship_decision.json emit.

Integrates:
  - data/v4.12/p_adj_v412.json          (Plan 103-02 Bonferroni-Holm output)
  - data/v4.12/permutation_null_v412.json (Plan 103-03 stance-shuffle null)
  - data/v4.12/kill_switch_v412.json    (Plan 103-04 standalone kill switch)
  - reports/v4.11/neutral_mode/v4_11_ship_decision.json (D-06 overlay carry,
    PARITY-V412-01 confirmed bit-exact in Phase 102)

Emits:
  - reports/v4.12/v4_12_ship_decision.json  (final ship artifact, Plan 103-06 audit)

Design:
  - D-06 schema: v4.10 overlay_evaluation 1:1 carry from neutral baseline
  - septuple_pin_stamp: 7 anchors (6 v4.11 carry + signal_commit_v412 from SEAL)
  - D-27 honest null-ship-v4: kill_switch_fired=true → ship_verdict=false +
    null_ship_v4_close=true (legitimate outcome, not failure)
  - D-50 strict inequality: ship_condition_met carry from permutation_null_v412.json
    (already enforces observed > p95 strict)
  - data_provenance: "macro-stance-v412-9160234" (signal_commit_v412[:7])
  - 4 primary metrics: edge_count_p_adj_005 + turnover_sharpe_median + es_median
    + pf_cost_adj_median (D-06 v4.10 schema, carry from v4.11 neutral baseline)

Threat T-103-04 (Tampering, ship_decision drift):
  - septuple_pin_stamp[signal_commit_v412] sourced from Phase 101 SEAL canonical_sha256
    (drift detection: SEAL change → emit fails); Plan 103-06
    verify_signal_commit_v412.sh enforces 4-artifact chain integrity (D-23-v412 rule:
    signal_commit_v412.json itself excluded from chain)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_P_ADJ: Path = _REPO_ROOT / "data" / "v4.12" / "p_adj_v412.json"
_PERM_NULL: Path = _REPO_ROOT / "data" / "v4.12" / "permutation_null_v412.json"
_KILL_SWITCH: Path = _REPO_ROOT / "data" / "v4.12" / "kill_switch_v412.json"
_NEUTRAL_V411_SHIP: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "neutral_mode" / "v4_11_ship_decision.json"
)
_OUTPUT: Path = _REPO_ROOT / "reports" / "v4.12" / "v4_12_ship_decision.json"

_SEAL_PATH: Path = (
    _REPO_ROOT
    / ".planning"
    / "phases"
    / "101-pre-reg-seal-signal-commit-v412-7th-anchor-macro-stance-estimator-nyquist-audit"
    / "SEAL"
    / "signal_commit_v412.json"
)

_SCHEMA_VERSION: str = "v4.12.0"
_COVERAGE_TIER: str = "macro-stance-v412"
_DATA_PROVENANCE: str = "macro-stance-v412-9160234"  # signal_commit_v412[:7]
_ALPHA: float = 0.05
_SHIP_CONDITION_RULE: str = "observed > p95 (D-50 strict inequality)"

# 6 v4.11 sextuple anchors (carried bit-for-bit from
# scripts/v4.11/ship_metrics_emitter_v411.py:_SEXTUPLE_PIN_STAMP). The 7th anchor
# (signal_commit_v412) is loaded dynamically from Phase 101 SEAL for drift detection.
_V411_SEXTUPLE_CARRY: dict[str, str] = {
    "threshold_commit": "6527cbc",
    "regime_commit": "90bf4b2",
    "sizing_exit_commit": "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab",
    "sizing_exit_commit_v410": "a5f71831851bc09fea1ac5f1335e8f3e01465913ec1a4e771c1c53072b51f27f",
    "signal_commit_v411": "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae",
    "engine_commit": "a5a1102",
}


def _load_septuple_pin_from_seal() -> dict[str, str]:
    """Build 7-anchor septuple pin: 6 v4.11 carry + signal_commit_v412 from SEAL.

    The Phase 101 SEAL artifact (signal_commit_v412.json) is the source-of-truth
    for the 7th anchor — its `canonical_sha256` field is read at emit time so
    that any SEAL drift causes emit divergence. The 6 v4.11 anchors are
    constants carried from scripts/v4.11/ship_metrics_emitter_v411.py.

    Drift semantics (T-103-04 mitigation):
      - If SEAL canonical_sha256 changes → septuple_pin_stamp[signal_commit_v412]
        changes → ship_decision.json content changes → Plan 103-06 audit detects.
      - If SEAL is missing → RuntimeError (fail-close).

    Returns:
        dict with exactly 7 keys; raises RuntimeError on schema violation.
    """
    if not _SEAL_PATH.exists():
        raise RuntimeError(f"SEAL artifact missing: {_SEAL_PATH}")
    seal = json.loads(_SEAL_PATH.read_text(encoding="utf-8"))
    canonical = seal.get("canonical_sha256")
    if not canonical or not isinstance(canonical, str) or len(canonical) != 64:
        raise RuntimeError(
            f"SEAL canonical_sha256 invalid (expected 64-char hex): {canonical!r}"
        )
    stamp: dict[str, str] = dict(_V411_SEXTUPLE_CARRY)
    stamp["signal_commit_v412"] = canonical

    expected_keys = {
        "threshold_commit",
        "regime_commit",
        "sizing_exit_commit",
        "sizing_exit_commit_v410",
        "signal_commit_v411",
        "signal_commit_v412",
        "engine_commit",
    }
    if set(stamp.keys()) != expected_keys:
        raise RuntimeError(
            f"Septuple-pin: expected 7 anchors {expected_keys}, "
            f"got {set(stamp.keys())}. Diff: {expected_keys ^ set(stamp.keys())}"
        )
    return stamp


def _compute_primary_metrics(
    p_adj_doc: dict, neutral_doc: dict
) -> dict[str, float | int]:
    """Compute 4 primary metrics per D-06 schema (v4.10 carry).

    edge_count_p_adj_005 derives from p_adj_v412.json (Holm-adjusted p-values).
    The 3 backtest medians (turnover_sharpe / es / pf_cost_adj) are carried 1:1
    from v4.11 neutral baseline because Phase 102 PARITY-V412-01 confirmed the
    neutral-mode-macro bypass is bit-exact equivalent to v4.11 neutral
    (signal_commit_v412 affects only stance distribution, not per-cell PF).

    When p_adj_v412.json is fully padded (n_tested=0, the real-data state where
    Phase 102 cells_post_compound_filter has 0 active cells), edge_count=0
    and the kill switch fires upstream.

    Args:
        p_adj_doc: parsed data/v4.12/p_adj_v412.json
        neutral_doc: parsed reports/v4.11/neutral_mode/v4_11_ship_decision.json

    Returns:
        dict with 4 keys: edge_count_p_adj_005, turnover_sharpe_median,
        es_median, pf_cost_adj_median
    """
    # CR-04: filter on `status == "tested"` to exclude padded slots from
    # edge count (padded rows can have p_adj_holm < ALPHA after Holm step-down
    # when many real tests have very small p_raw, but they are not real edges).
    edge_count = sum(
        1
        for r in p_adj_doc.get("results", [])
        if r.get("status") == "tested"
        and r.get("p_adj_holm") is not None
        and r["p_adj_holm"] < _ALPHA
    )

    # D-06 carry: v4.11 neutral baseline primary_metrics (PARITY-V412-01 bit-exact)
    # WR-06: require explicit keys; silent 0.0 default would mask Phase 92 anchor
    # schema drift (Phase 98 14-commit revert lesson — partial drift cascades).
    neutral_pm = neutral_doc.get("ship_metrics", {}).get("primary_metrics", {})
    required_keys = ("turnover_sharpe_median", "es_median", "pf_cost_adj_median")
    missing = [k for k in required_keys if k not in neutral_pm]
    if missing:
        raise RuntimeError(
            f"v4.11 neutral baseline missing primary metric(s): {missing} — "
            "Phase 92 anchor schema drift (PARITY-V412-01 bit-exact carry broken)"
        )
    return {
        "edge_count_p_adj_005": int(edge_count),
        "turnover_sharpe_median": float(neutral_pm["turnover_sharpe_median"]),
        "es_median": float(neutral_pm["es_median"]),
        "pf_cost_adj_median": float(neutral_pm["pf_cost_adj_median"]),
    }


def _build_overlay_evaluation(neutral_doc: dict) -> dict:
    """Carry overlay_evaluation 1:1 from v4.11 neutral baseline.

    D-06 section merge (PARITY-V412-01): Phase 102 confirmed the
    neutral-mode-macro bypass produces bit-exact identical overlay_evaluation
    fields to v4.11 neutral. This helper inherits the v4.11 helper semantics
    from scripts/v4.11/ship_metrics_emitter_v411.py.
    """
    return neutral_doc.get("overlay_evaluation", {})


def build_ship_decision_doc() -> dict:
    """Assemble the v4.12 ship_decision doc without writing it.

    Exposed as a standalone function so tests can assert the shape without
    side effects.
    """
    p_adj_doc = json.loads(_P_ADJ.read_text(encoding="utf-8"))
    perm_doc = json.loads(_PERM_NULL.read_text(encoding="utf-8"))
    kill_switch_doc = json.loads(_KILL_SWITCH.read_text(encoding="utf-8"))
    neutral_doc = json.loads(_NEUTRAL_V411_SHIP.read_text(encoding="utf-8"))

    primary_metrics = _compute_primary_metrics(p_adj_doc, neutral_doc)
    edge_count = int(primary_metrics["edge_count_p_adj_005"])

    kill_switch_fired = bool(kill_switch_doc["kill_switch_fired"])
    ship_condition_met = bool(
        perm_doc["ship_condition_met"]
    )  # D-50 strict already enforced

    # D-27 honest null-ship-v4 closure path:
    #   ship_verdict = (not kill_switch_fired) AND (edge_count > 0) AND ship_condition_met
    #   null_ship_v4_close = kill_switch_fired (legitimate close, NOT failure)
    ship_verdict = (not kill_switch_fired) and (edge_count > 0) and ship_condition_met
    null_ship_v4_close = kill_switch_fired

    septuple_pin = _load_septuple_pin_from_seal()
    overlay_evaluation = _build_overlay_evaluation(neutral_doc)

    # provenance fallbacks for mock fixtures (real artifact has all keys)
    provenance = p_adj_doc.get("provenance", {})
    n_tested = int(provenance.get("n_tested", 0))
    n_padded = int(provenance.get("n_padded", 0))

    # CR-03: split kill_switch semantics into two distinct fields:
    #   - kill_switch_fired: binary (from kill_switch_v412.json, this run's outcome)
    #   - bootstrap_kill_switch_consumed: provenance from p_adj_v412.json (whether
    #     the upstream bootstrap actually consumed a kill_switch_consumed flag at
    #     pad-time, i.e., what the FWER chain SAW). Conflating these breaks D-27
    #     auditability (the honest null close path needs both signals visible).
    bootstrap_kill_switch_consumed = bool(provenance.get("kill_switch_consumed", False))

    permutation_block = {
        "B": int(perm_doc["B"]),
        "seed": int(perm_doc["seed"]),
        "shuffle_unit": str(perm_doc["shuffle_unit"]),
        "observed_edge_count_p_adj_005": int(perm_doc["observed_edge_count_p_adj_005"]),
        "null_percentiles": perm_doc["null_percentiles"],
        "ship_condition_met": ship_condition_met,
        "ship_condition_rule": _SHIP_CONDITION_RULE,
        "n_tested": n_tested,
        "n_padded": n_padded,
        "kill_switch_fired": kill_switch_fired,
        "bootstrap_kill_switch_consumed": bootstrap_kill_switch_consumed,
    }

    doc = {
        "schema_version": _SCHEMA_VERSION,
        "overlay_evaluation": overlay_evaluation,
        "ship_metrics": {
            "ship_verdict": ship_verdict,
            "null_ship_v4_close": null_ship_v4_close,
            "coverage_tier": _COVERAGE_TIER,
            "edge_count_p_adj_005": edge_count,
            "primary_metrics": primary_metrics,
            "data_provenance": _DATA_PROVENANCE,
            "septuple_pin_stamp": septuple_pin,
        },
        "permutation_null": permutation_block,
    }
    return doc


def _json_default(obj):
    """JSON encoder fallback for numpy scalar types in overlay_evaluation."""
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def main() -> None:
    doc = build_ship_decision_doc()
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True, default=_json_default)
        f.write("\n")
    print(f"[v412_ship_emit] -> {_OUTPUT}")
    print(f"  ship_verdict={doc['ship_metrics']['ship_verdict']}")
    print(f"  null_ship_v4_close={doc['ship_metrics']['null_ship_v4_close']}")
    print(f"  edge_count_p_adj_005={doc['ship_metrics']['edge_count_p_adj_005']}")
    print(f"  data_provenance={doc['ship_metrics']['data_provenance']}")


if __name__ == "__main__":
    main()
