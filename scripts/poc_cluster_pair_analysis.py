"""PoC cluster_pair_drift trial 分布解析。

Reads 4 cell PoC scan outputs, applies fee-aware PF/Sharpe thresholds,
counts qualified trials, and prints verdict table.

Spec: docs/superpowers/specs/2026-04-09-cluster-pair-drift-poc-design.md
Plan: docs/superpowers/plans/2026-04-09-cluster-pair-drift-poc.md

values 順序 (確認元: rust/side-engine/src/scanner/mod.rs:130-131):
  [0] = oos_pf
  [1] = oos_sharpe
  [2] = oos_max_dd
"""

import json
import sys
from pathlib import Path

CELLS = [
    ("1m", 0.0),
    ("1m", 1.0),
    ("5m", 0.0),
    ("5m", 1.0),
]
DATA_DIR = Path(__file__).parent.parent / "data"


def threshold(fee: float) -> tuple[float, float]:
    """fee に応じた (PF, Sharpe) lower threshold."""
    if fee == 0.0:
        return (1.2, 0.5)
    return (0.9, 0.0)


def analyze_cell(tf: str, fee: float) -> dict:
    f = DATA_DIR / f"poc-cluster-pair-{tf}-fee{fee}-260409.json"
    if not f.exists():
        return {
            "tf": tf,
            "fee": fee,
            "error": f"file missing: {f.name}",
            "qualified": 0,
            "pass": False,
        }

    cells = json.loads(f.read_text())
    if not cells:
        return {
            "tf": tf,
            "fee": fee,
            "error": "empty cells",
            "qualified": 0,
            "pass": False,
        }
    cell = cells[0]

    complete = [t for t in cell.get("all_trials", []) if t.get("state") == "complete"]
    pf_t, sharpe_t = threshold(fee)
    qualified = [
        t
        for t in complete
        if len(t.get("values", [])) >= 2
        and t["values"][0] >= pf_t
        and t["values"][1] >= sharpe_t
    ]

    pfs = [t["values"][0] for t in complete if t.get("values")]
    sharpes = [t["values"][1] for t in complete if len(t.get("values", [])) > 1]

    return {
        "tf": tf,
        "fee": fee,
        "n_complete": len(complete),
        "n_pruned": cell.get("n_trials_pruned", 0),
        "pf_threshold": pf_t,
        "sharpe_threshold": sharpe_t,
        "qualified": len(qualified),
        "best_pf": max(pfs) if pfs else None,
        "best_sharpe": max(sharpes) if sharpes else None,
        "n_approved": len(cell.get("best_trials", [])),
        "pass": len(qualified) >= 5,
    }


def fmt(v, width=10, prec=3):
    if v is None:
        return f"{'-':>{width}}"
    if isinstance(v, float):
        return f"{v:>{width}.{prec}f}"
    return f"{v:>{width}}"


def main():
    results = [analyze_cell(tf, fee) for tf, fee in CELLS]

    print("=== PoC cluster_pair_drift 結果 ===")
    print(
        f"{'cell':<14} {'complete':>9} {'pruned':>7} {'qualified':>10} "
        f"{'best_pf':>10} {'best_sh':>10} {'approved':>9} {'PASS':>6}"
    )
    print("-" * 80)
    for r in results:
        cell_label = f"{r['tf']}/fee={r['fee']}"
        if "error" in r:
            print(f"{cell_label:<14} ERROR: {r['error']}")
            continue
        verdict = "PASS" if r["pass"] else "FAIL"
        print(
            f"{cell_label:<14} "
            f"{fmt(r['n_complete'], 9, 0)} "
            f"{fmt(r['n_pruned'], 7, 0)} "
            f"{fmt(r['qualified'], 10, 0)} "
            f"{fmt(r['best_pf'])} "
            f"{fmt(r['best_sharpe'])} "
            f"{fmt(r['n_approved'], 9, 0)} "
            f"{verdict:>6}"
        )

    overall_pass = all(r.get("pass", False) for r in results)
    print()
    print(f"Overall PoC verdict: {'PASS' if overall_pass else 'FAIL'}")
    print(f"  - 4 cell すべて qualified ≥5: {overall_pass}")
    print()
    print("Stage 1 baseline (time_of_day_drift):")
    print("  - 1m fee=0: PF 2.60 / Sharpe 8.68")
    print("  - 1m fee=1: PF 0.75")
    print("  - 5m fee=0: PF 1.78 / Sharpe 5.54")
    print("  - 5m fee=1: PF 0.97")

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
