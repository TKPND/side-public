"""v4.8 Phase 81 REGIME-04 + REGIME-05: label audit gate + regime_breakdown emitter.

Gate thresholds (Phase 79 D-09 SEAL, regime_cuts.json per_cell_min_n_floor):
- empty_cell_count == 0
- max_concentration < 0.80
- min n_cell >= 5

Gate fail → exit 1 with hierarchical fallback escalation suggestion
(pooled-across-liquidity → pooled-across-duration, per power_budget.json).

Byte-identity (REGIME-05): docs/reports/v4.6-verdict-resolution/sign-forensics/
sign_breakdown.json の SHA256 を記録し、v4.5/v4.6 baseline と一致することを assert。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# Phase 79 SEAL — DO NOT EDIT
GATE_MAX_CONCENTRATION = 0.80
GATE_MIN_N_CELL = 5
GATE_EMPTY_CELL_MAX = 0
DURATION_BUCKETS = ("0-60m", "60-120m")
LIQUIDITY_REGIMES = ("LOW", "MID", "HIGH")
ALL_CELL_IDS = [f"{b}_x_{r}" for b in DURATION_BUCKETS for r in LIQUIDITY_REGIMES]

HIERARCHICAL_FALLBACK_LEVELS = [
    ("L0", "full 2x3 cells (no pool)"),
    ("L1", "pooled across liquidity (duration-only, 2 cells)"),
    ("L2", "pooled across duration (liquidity-only, 3 cells)"),
    ("L3", "fully pooled (1 cell, null-ship-v3 path)"),
]


@dataclass
class AuditResult:
    empty_cell_count: int
    max_concentration: float
    min_n_cell: int
    passed: bool
    failing_events: list[str]
    escalate_to: str | None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_labels(labels: pd.DataFrame) -> tuple[AuditResult, dict[str, Any]]:
    """Return (AuditResult, cells_by_event dict)."""
    events = sorted(labels["event_type"].unique())
    cells_by_event: dict[str, list[dict[str, Any]]] = {}
    empty_count = 0
    max_conc = 0.0
    min_n = 10**9
    failing: list[str] = []

    for ev in events:
        sub = labels[labels["event_type"] == ev]
        total = len(sub)
        per_cell = sub.groupby("cell_id").size().to_dict()
        row: list[dict[str, Any]] = []
        for cid in ALL_CELL_IDS:
            n = int(per_cell.get(cid, 0))
            conc = (n / total) if total > 0 else 0.0
            empty = n == 0
            row.append(
                {
                    "cell_id": cid,
                    "n_nominal": n,
                    "concentration": round(conc, 4),
                    "empty": empty,
                }
            )
            if empty:
                empty_count += 1
                failing.append(f"{ev}:{cid}:empty")
            else:
                if n < GATE_MIN_N_CELL:
                    failing.append(f"{ev}:{cid}:under_min_n({n}<{GATE_MIN_N_CELL})")
                if conc > max_conc:
                    max_conc = conc
                if n < min_n:
                    min_n = n
        cells_by_event[ev] = row

    if min_n == 10**9:
        min_n = 0

    passed = (
        empty_count <= GATE_EMPTY_CELL_MAX
        and max_conc < GATE_MAX_CONCENTRATION
        and min_n >= GATE_MIN_N_CELL
    )

    escalate_to = None
    if not passed:
        # Hierarchical fallback suggestion: count empty cells per axis to suggest L1 or L2.
        # Simplified heuristic: if empty concentrated in liquidity axis → pool-across-liquidity (L1).
        # Else pool-across-duration (L2).
        escalate_to = _suggest_fallback(cells_by_event)

    return (
        AuditResult(
            empty_cell_count=empty_count,
            max_concentration=round(max_conc, 4),
            min_n_cell=min_n,
            passed=passed,
            failing_events=failing,
            escalate_to=escalate_to,
        ),
        cells_by_event,
    )


def _suggest_fallback(cells_by_event: dict[str, list[dict[str, Any]]]) -> str:
    """Return "L1" / "L2" / "L3" per Phase 79 hierarchical fallback spec."""
    liquidity_empty = 0
    duration_empty = 0
    for cells in cells_by_event.values():
        for c in cells:
            if not c["empty"]:
                continue
            dur, _, liq = c["cell_id"].partition("_x_")
            if liq in {"LOW", "HIGH"}:
                liquidity_empty += 1
            if dur in {"0-60m", "60-120m"}:
                duration_empty += 1
    if liquidity_empty >= duration_empty:
        return "L1 (pooled-across-liquidity)"
    return "L2 (pooled-across-duration)"


def emit_regime_breakdown(
    result: AuditResult,
    cells_by_event: dict[str, list[dict[str, Any]]],
    slot_labels_path: Path,
    regime_cuts_path: Path,
    out_path: Path,
) -> None:
    doc = {
        "schema_version": "v4.8-phase-81-label-only",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "slot_labels_path": str(slot_labels_path),
            "slot_labels_sha256": _sha256(slot_labels_path),
            "regime_cuts_path": str(regime_cuts_path),
            "regime_cuts_sha256": _sha256(regime_cuts_path),
        },
        "audit_gate": {
            "thresholds": {
                "empty_cell_max": GATE_EMPTY_CELL_MAX,
                "max_concentration_exclusive": GATE_MAX_CONCENTRATION,
                "min_n_cell": GATE_MIN_N_CELL,
            },
            "empty_cell_count": result.empty_cell_count,
            "max_concentration": result.max_concentration,
            "min_n_cell": result.min_n_cell,
            "passed": result.passed,
            "failing_events": result.failing_events,
            "escalate_to": result.escalate_to,
        },
        "cells_by_event": cells_by_event,
        "_phase82_placeholder": {
            "rho_bar": None,
            "vif": None,
            "n_eff_predicted": None,
            "note": "Phase 82 (POWER-01/05) が cell-wise stats を追記する。",
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")


def check_sign_breakdown_identity(paths: list[Path], record_to: dict[str, Any]) -> None:
    """REGIME-05 byte-identity: sign_breakdown.json の SHA256 を record するだけで assert は別 test。"""
    record_to["sign_breakdown_sha256"] = {
        str(p): _sha256(p) for p in paths if p.exists()
    }


def main() -> int:
    p = argparse.ArgumentParser(description="v4.8 Phase 81 label audit gate")
    p.add_argument("--slot-labels", type=Path, default=Path("data/slot_labels.parquet"))
    p.add_argument("--regime-cuts", type=Path, default=Path("data/regime_cuts.json"))
    p.add_argument(
        "--sign-breakdown",
        type=Path,
        default=Path(
            "docs/reports/v4.6-verdict-resolution/sign-forensics/sign_breakdown.json"
        ),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("docs/reports/v4.8-regime-v2/regime_breakdown.json"),
    )
    args = p.parse_args()

    labels = pd.read_parquet(args.slot_labels)
    result, cells = audit_labels(labels)
    emit_regime_breakdown(result, cells, args.slot_labels, args.regime_cuts, args.out)

    # REGIME-05: record sign_breakdown.json SHA256 into stderr (baseline 比較は caller の責務)
    sb_sha = _sha256(args.sign_breakdown) if args.sign_breakdown.exists() else "MISSING"
    print(f"[label_audit] sign_breakdown.json SHA256 = {sb_sha}", file=sys.stderr)
    print(f"[label_audit] regime_breakdown.json → {args.out}", file=sys.stderr)
    print(f"[label_audit] gate passed = {result.passed}", file=sys.stderr)

    if not result.passed:
        print(
            "[label_audit] GATE FAIL — hierarchical fallback escalation suggested:",
            file=sys.stderr,
        )
        print(f"[label_audit]   escalate_to = {result.escalate_to}", file=sys.stderr)
        for level, desc in HIERARCHICAL_FALLBACK_LEVELS:
            print(f"[label_audit]   {level}: {desc}", file=sys.stderr)
        print(f"[label_audit]   failing = {result.failing_events}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
