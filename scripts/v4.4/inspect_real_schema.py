"""Phase 66 Wave 0: Real schema inspector.

Empirically validates D-01/D-04/D-05 (CONTEXT.md) by walking fee_results[]
in all 6 real source report.json files and computing pf/trades/sharpe
distributions + D-05 sign distribution.

Usage:
    uv run python scripts/v4.4/inspect_real_schema.py

Decision gate (RESEARCH.md Open Q1):
  - non_zero_trades < 5% for any label → STOP and revisit D-05
  - All >= 20% → proceed to Wave 1 as locked
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SOURCES: list[tuple[str, Path]] = [
    ("usdjpy", Path("docs/reports/v4.1-n-expansion/report.json")),
    ("eurusd/fomc", Path("docs/reports/v3.9-cross-pair/eurusd/fomc/report.json")),
    ("eurusd/ecb", Path("docs/reports/v3.9-cross-pair/eurusd/ecb/report.json")),
    ("eurusd/nfp", Path("docs/reports/v3.9-cross-pair/eurusd/nfp/report.json")),
    ("audusd", Path("docs/reports/v4.2-audusd/report.json")),
    ("eurjpy", Path("docs/reports/v4.2-eurjpy/report.json")),
]


def _walk_fee_entries(raw: object) -> list[dict]:
    """Recursively find dicts that look like fee_results[] entries (have combined_oos_pf or fee_bps key)."""
    results: list[dict] = []
    if isinstance(raw, dict):
        if "fee_results" in raw and isinstance(raw["fee_results"], list):
            for entry in raw["fee_results"]:
                if isinstance(entry, dict):
                    results.append(entry)
        for v in raw.values():
            results.extend(_walk_fee_entries(v))
    elif isinstance(raw, list):
        for item in raw:
            results.extend(_walk_fee_entries(item))
    return results


def _derive_sign_candidate(fee_entry: dict) -> int:
    """D-05 rule preview (must match Wave 1 implementation)."""
    trades = fee_entry.get("combined_oos_trades", 0)
    if trades == 0:
        return 0
    pf = fee_entry.get("combined_oos_pf")
    if pf is None:
        return 0
    return 1 if float(pf) >= 1.0 else -1


def inspect(label: str, path: Path) -> bool:
    """Inspect a single source. Returns True if decision gate is PASS (non_zero_trades >= 5%)."""
    if not path.exists():
        print(f"[MISSING] {label}: {path}")
        return False
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"[ERROR] {label}: {exc}")
        return False
    entries = _walk_fee_entries(raw)
    if not entries:
        print(f"[EMPTY] {label}: no fee_results[] found")
        return False
    n = len(entries)
    nz_trades = sum(1 for e in entries if e.get("combined_oos_trades", 0) != 0)
    nz_pct = 100 * nz_trades / n
    pfs = [
        e.get("combined_oos_pf")
        for e in entries
        if e.get("combined_oos_pf") is not None
    ]
    sharpes = [
        e.get("combined_oos_sharpe")
        for e in entries
        if e.get("combined_oos_sharpe") is not None
    ]
    sign_dist = Counter(_derive_sign_candidate(e) for e in entries)
    keys_sample = sorted(entries[0].keys())
    top_keys = sorted(list(raw.keys()))[:8] if isinstance(raw, dict) else []
    print(f"\n=== {label} ({path}) ===")
    print(f"  n_entries={n}  non_zero_trades={nz_trades} ({nz_pct:.1f}%)")
    if pfs:
        sorted_pfs = sorted(pfs)
        print(
            f"  pf: min={min(pfs):.4f}  max={max(pfs):.4f}  median={sorted_pfs[len(sorted_pfs) // 2]:.4f}  n_present={len(pfs)}"
        )
    else:
        print("  pf: n_present=0")
    if sharpes:
        print(
            f"  sharpe: min={min(sharpes):.4f}  max={max(sharpes):.4f}  n_present={len(sharpes)}"
        )
    else:
        print("  sharpe: n_present=0")
    print(f"  D-05 sign dist: +1={sign_dist[1]}  -1={sign_dist[-1]}  0={sign_dist[0]}")
    print(f"  fee_entry keys sample: {keys_sample}")
    print(f"  top_keys={top_keys}")
    gate_pass = nz_pct >= 5.0
    if not gate_pass:
        print(
            f"  *** DECISION GATE FAIL: non_zero_trades={nz_pct:.1f}% < 5% threshold ***"
        )
    return gate_pass


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    print("# Phase 66 Wave 0 schema inspection (CONTEXT.md D-01/D-04/D-05)")
    all_pass = True
    gate_failures: list[str] = []
    for label, path in SOURCES:
        gate_ok = inspect(label, path)
        if not gate_ok:
            all_pass = False
            gate_failures.append(label)
    print("\n# Decision gate:")
    print(
        "# - If any pair shows non_zero_trades < 5% -> STOP and revisit D-05 (sharpe sign alternative)"
    )
    print("# - If all pairs show non_zero_trades >= 20% -> proceed to Wave 1 as locked")
    if all_pass:
        print("\n# VERDICT: PROCEED — all sources passed non_zero_trades >= 5% gate")
    else:
        print(
            f"\n# VERDICT: D-05 REVISIT REQUIRED — halt Wave 1. Failing sources: {gate_failures}"
        )


if __name__ == "__main__":
    main()
