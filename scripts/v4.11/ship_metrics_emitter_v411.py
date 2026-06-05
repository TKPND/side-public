"""v4.11 Phase 95 SHIP-03/04: active-mode v4_11_ship_decision.json emitter.

Emits the canonical active-mode ship decision JSON at
`reports/v4.11/active_mode/v4_11_ship_decision.json` with:
  - 4 primary metrics + ship_verdict conjunction (D-52)
  - data_provenance = "vol-regime-v411-f8ccc8a" literal (D-53)
  - sextuple-pin stamp (6 anchors + data_provenance derived literal)
  - permutation_null block (D-54) merged from Plan 2 Task 1 artifact
  - overlay_evaluation carried 1:1 from neutral mode (D-06 section merge,
    D-51 neutral留置)

Neutral-mode `reports/v4.11/neutral_mode/v4_11_ship_decision.json` is
read-only (PARITY-V411-01 baseline) — this emitter MUST NOT mutate it.

Inputs:
  - reports/v4.11/active_mode/p_adj_v411.json (Plan 1 output)
  - reports/v4.11/active_mode/permutation_null_v411.json (Plan 2 Task 1)
  - reports/v4.11/neutral_mode/v4_11_ship_decision.json (PARITY baseline,
    read-only)
  - data/v4.11/cells_post_filter.parquet (pass_flag mask)

Output:
  - reports/v4.11/active_mode/v4_11_ship_decision.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# ---------------------------------------------------------------------------
# Pre-registered constants (D-52 / D-53 / D-54)
# ---------------------------------------------------------------------------
_DATA_PROVENANCE: str = "vol-regime-v411-f8ccc8a"  # D-53: signal_commit_v411[:7]
_COVERAGE_TIER: str = "inconclusive-2024-q1-filtered"  # v4.11 window
_ALPHA: float = 0.05
_SCHEMA_VERSION: str = "v4.11.0"

# Six anchors + data_provenance derived literal (7 keys total)
_SEXTUPLE_PIN_STAMP: dict[str, str] = {
    "threshold_commit": "6527cbc",
    "regime_commit": "90bf4b2",
    "sizing_exit_commit": "8a4e49d2000b08e9e1b93b5f9f0de661d5dff7613d8dfc8339313452a3b81fab",
    "sizing_exit_commit_v410": "a5f71831851bc09fea1ac5f1335e8f3e01465913ec1a4e771c1c53072b51f27f",
    "signal_commit_v411": "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae",
    "engine_commit": "a5a1102",
    "data_provenance": _DATA_PROVENANCE,
}

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_NEUTRAL_SHIP: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "neutral_mode" / "v4_11_ship_decision.json"
)
_ACTIVE_SHIP: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "v4_11_ship_decision.json"
)
_P_ADJ: Path = _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "p_adj_v411.json"
_PERM_NULL: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "permutation_null_v411.json"
)
_CELLS_POST_FILTER: Path = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"


# ---------------------------------------------------------------------------
# D-52 median aggregation (over pass_flag=true cells)
# ---------------------------------------------------------------------------
def _compute_medians_over_tested(
    neutral_doc: dict, tested: list[str]
) -> tuple[float, float]:
    """Compute turnover_sharpe_median / es_median over tested cells only.

    Primary: neutral_doc['per_cell_metrics'] list (if present) with
    entries {cell_id, turnover_sharpe, es}.
    Fallback: neutral_doc['ship_metrics']['primary_metrics'] (coarse —
    inherited full-grid medians; used when Phase 94 did not emit per_cell
    breakdown). Returns (0.0, 0.0) only when both sources are absent.
    """
    per_cell = neutral_doc.get("per_cell_metrics")
    if per_cell:
        tested_set = set(tested)
        filtered = [m for m in per_cell if m.get("cell_id") in tested_set]
        if filtered:
            ts = float(np.nanmedian([m["turnover_sharpe"] for m in filtered]))
            es = float(np.nanmedian([m["es"] for m in filtered]))
            return ts, es
    pm = neutral_doc.get("ship_metrics", {}).get("primary_metrics", {})
    return (
        float(pm.get("turnover_sharpe_median", 0.0)),
        float(pm.get("es_median", 0.0)),
    )


# ---------------------------------------------------------------------------
# Main pipeline (D-51..D-54)
# ---------------------------------------------------------------------------
def build_ship_decision_doc() -> dict:
    """Assemble the active-mode ship_decision doc without writing it.

    Exposed as a standalone function so tests can assert the shape without
    side effects.
    """
    p_adj_doc = json.loads(_P_ADJ.read_text(encoding="utf-8"))
    perm_doc = json.loads(_PERM_NULL.read_text(encoding="utf-8"))
    neutral_doc = json.loads(_NEUTRAL_SHIP.read_text(encoding="utf-8"))
    cells = pl.read_parquet(_CELLS_POST_FILTER)
    tested = (
        cells.filter(pl.col("pass_flag") == True)  # noqa: E712 polars idiom
        .get_column("cell_id")
        .to_list()
    )

    # 1. Edge count from the real Holm-adjusted p-values
    edge_count = sum(
        1
        for r in p_adj_doc["results"]
        if r["p_adj_holm"] is not None and r["p_adj_holm"] < _ALPHA
    )

    # 2. 2/4 primary medians (turnover_sharpe, es) via pass_flag mask (D-52)
    ts_med, es_med = _compute_medians_over_tested(neutral_doc, tested)

    # 3. ship_verdict conjunction — structural condition at Plan 2:
    #    edge_count > 0 AND permutation ship_condition_met.
    #    grep_gates_v411.sh (Plan 3) and sextuple-pin audit (Plan 4) remain
    #    downstream gates; Plan 4 final audit confirms those pass.
    ship_verdict = bool(edge_count > 0 and perm_doc["ship_condition_met"])

    # 4. permutation_null block (D-54 additive shape)
    perm_block = {
        "B": perm_doc["provenance"]["B"],
        "seed": perm_doc["provenance"]["seed"],
        "shuffle_unit": perm_doc["provenance"]["shuffle_unit"],
        "observed_edge_count_p_adj_005": perm_doc["observed_edge_count_p_adj_005"],
        "null_percentiles": perm_doc["null_percentiles"],
        "ship_condition_met": perm_doc["ship_condition_met"],
        "n_tested": p_adj_doc["provenance"]["n_tested"],
        "n_padded": p_adj_doc["provenance"]["n_padded"],
        "kill_switch_consumed": p_adj_doc["provenance"]["kill_switch_consumed"],
    }

    # 5. Assemble doc (D-06 section merge — overlay_evaluation 1:1 carry)
    doc = {
        "schema_version": _SCHEMA_VERSION,
        "overlay_evaluation": neutral_doc.get("overlay_evaluation", {}),
        "ship_metrics": {
            "ship_verdict": ship_verdict,
            "coverage_tier": _COVERAGE_TIER,
            "edge_count_p_adj_005": int(edge_count),
            "primary_metrics": {
                "edge_count_p_adj_005": int(edge_count),
                "turnover_sharpe_median": ts_med,
                "es_median": es_med,
            },
            "data_provenance": _DATA_PROVENANCE,
            "sextuple_pin_stamp": _SEXTUPLE_PIN_STAMP,
        },
        "permutation_null": perm_block,
    }
    return doc


def main(output_path: Path = _ACTIVE_SHIP) -> None:
    doc = build_ship_decision_doc()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"[ship_metrics_emitter_v411] edge_count="
        f"{doc['ship_metrics']['edge_count_p_adj_005']} "
        f"ship_verdict={doc['ship_metrics']['ship_verdict']} "
        f"-> {output_path}"
    )


if __name__ == "__main__":
    main()
