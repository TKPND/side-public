"""v4.8 Phase 82 POWER-01: Wave-1 cell-level power aggregator.

Reads data/slot_labels.parquet (Phase 81 output), groups by (event_type, cell_id),
computes per-cell rho_bar / VIF / n_eff_predicted for the wave-1 kill-switch.

n_eff formula (pre-registered power_budget.json D-29):
    n_eff_predicted = (1 - rho_bar) * n_nominal / VIF
rho_bar: median of pair-pair Spearman correlations of sign-of-returns (D-12, NG: smoothing)
VIF: statsmodels variance_inflation_factor on [long, neutral, short] columns

Threat mitigations:
    T-82-01: n_eff formula is pre-registered; no post-hoc modification allowed.
    T-82-02: GATE_K = 4 is a module-top constant (variable-ization prohibited).
    T-82-03: VIF = inf/nan -> n_eff_predicted = 0.0 (kill-switch guaranteed to fire).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from statsmodels.stats.outliers_influence import variance_inflation_factor

# --- Pre-registered constants (power_budget.json D-29, T-82-02) ---
# DO NOT variable-ize or pass as a parameter.
GATE_K: int = 4

# Required columns in slot_labels.parquet
_REQUIRED_COLUMNS = ["event_type", "cell_id", "long", "neutral", "short", "pair"]


@dataclass
class CellStats:
    """Per-(event_type, cell_id) power statistics."""

    event_type: str
    cell_id: str
    n_nominal: int
    rho_bar: float  # median pair-pair Spearman of sign(long-short) series
    vif: float  # mean VIF of [long, neutral, short] columns
    n_eff_predicted: float  # (1 - rho_bar) * n_nominal / VIF  (D-29)
    fallback_level: (
        int  # 0=primary, 1=pooled-across-liquidity, 2=pooled-across-duration
    )


def load_slot_labels(path: Union[str, Path]) -> pd.DataFrame:
    """Load slot_labels.parquet and validate required columns.

    Args:
        path: Path to slot_labels.parquet (Phase 81 output).

    Returns:
        DataFrame with at minimum columns: event_type, cell_id, long, neutral, short, pair.

    Raises:
        ValueError: If any required column is absent.
    """
    df = pd.read_parquet(path)
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return df


def _compute_rho_bar(df: pd.DataFrame) -> float:
    """Compute median pair-pair Spearman correlation of sign(long - short).

    D-12: raw Spearman, no smoothing.

    Args:
        df: Rows for a single (event_type, cell_id) cell.

    Returns:
        rho_bar clamped to [0.0, 1.0].
        Returns 0.0 if fewer than 2 pairs or no valid pair-pair correlations.
    """
    # sign_series per row (sign of long - short)
    signs = np.sign(df["long"].values - df["short"].values)
    pairs = df["pair"].values

    unique_pairs = list(dict.fromkeys(pairs))  # preserve order, deduplicate
    if len(unique_pairs) < 2:
        return 0.0

    # Build sign vector per pair (one value per row in this pair's rows)
    pair_signs: dict[str, np.ndarray] = {}
    for p in unique_pairs:
        mask = pairs == p
        pair_signs[p] = signs[mask]

    rho_values: list[float] = []
    for p1, p2 in combinations(unique_pairs, 2):
        s1 = pair_signs[p1]
        s2 = pair_signs[p2]
        # align by length — take min to handle unequal lengths
        n = min(len(s1), len(s2))
        if n < 2:
            continue
        r, _ = spearmanr(s1[:n], s2[:n])
        if np.isnan(r):
            continue
        rho_values.append(float(r))

    if not rho_values:
        return 0.0

    rho_bar = float(np.median(rho_values))
    # Clamp to [0.0, 1.0] per PLAN action spec
    # (negative correlation treated as zero — conservative for n_eff)
    return float(np.clip(rho_bar, 0.0, 1.0))


def _compute_vif(df: pd.DataFrame) -> float:
    """Compute mean VIF of [long, neutral, short] columns.

    T-82-03: Returns float('inf') if VIF calculation fails (inf/nan),
    which causes n_eff_predicted = 0.0 (kill-switch guaranteed to fire).

    Args:
        df: Rows for a single cell.

    Returns:
        Mean VIF across the three feature columns, or float('inf') on failure.
    """
    X = df[["long", "neutral", "short"]].values.astype(float)
    n_rows, n_cols = X.shape

    if n_rows < 3:
        return float("inf")  # T-82-03: insufficient data → kill-switch fires

    # Check rank — if not full column rank, VIF is undefined
    rank = np.linalg.matrix_rank(X)
    if rank < n_cols:
        return float("inf")  # T-82-03: forces n_eff = 0.0

    try:
        vifs = [float(variance_inflation_factor(X, i)) for i in range(n_cols)]
    except Exception:
        return float("inf")

    if any(np.isnan(v) or np.isinf(v) for v in vifs):
        return float("inf")  # T-82-03

    return float(np.mean(vifs))


def compute_cell_stats(
    df: pd.DataFrame,
    event_type: str,
    cell_id: str,
    fallback_level: int = 0,
) -> CellStats:
    """Compute power statistics for one (event_type, cell_id) cell.

    Args:
        df: Rows belonging to this cell.
        event_type: Event type label (e.g. "ECB").
        cell_id: Cell identifier (e.g. "0-60m_x_HIGH").
        fallback_level: 0=primary, 1=L1, 2=L2.

    Returns:
        CellStats with n_eff_predicted = (1 - rho_bar) * n_nominal / VIF.
    """
    n_nominal = len(df)
    rho_bar = _compute_rho_bar(df)
    vif = _compute_vif(df)

    if vif <= 0 or np.isinf(vif) or np.isnan(vif):
        # T-82-03: VIF pathological -> kill-switch guaranteed
        n_eff_predicted = 0.0
    else:
        n_eff_predicted = (1.0 - rho_bar) * n_nominal / vif

    # Floor at 0.0 (negative values are non-physical)
    n_eff_predicted = max(0.0, n_eff_predicted)

    return CellStats(
        event_type=event_type,
        cell_id=cell_id,
        n_nominal=n_nominal,
        rho_bar=rho_bar,
        vif=vif,
        n_eff_predicted=n_eff_predicted,
        fallback_level=fallback_level,
    )


def group_by_event_cell(
    df: pd.DataFrame,
    fallback_level: int = 0,
) -> list[CellStats]:
    """Group slot_labels by (event_type, cell_id) and compute CellStats per cell.

    Args:
        df: Full slot_labels DataFrame (or filtered subset).
        fallback_level: Propagated to each CellStats.

    Returns:
        List of CellStats, one per non-empty (event_type, cell_id) group.
    """
    if df.empty:
        return []

    results: list[CellStats] = []
    for (event_type, cell_id), group in df.groupby(
        ["event_type", "cell_id"], sort=True
    ):
        if len(group) == 0:
            continue  # empty cell — skip stats computation
        stats = compute_cell_stats(
            group,
            event_type=str(event_type),
            cell_id=str(cell_id),
            fallback_level=fallback_level,
        )
        results.append(stats)
    return results


def group_by_event_duration(df: pd.DataFrame) -> list[CellStats]:
    """L1 fallback: pool across liquidity, group by (event_type, duration_bucket) only.

    Sets fallback_level=1 on all results.
    """
    df = df.copy()
    df["cell_id"] = df["duration_bucket"]  # e.g. "0-60m"
    return group_by_event_cell(df, fallback_level=1)


def group_by_event_liquidity(df: pd.DataFrame) -> list[CellStats]:
    """L2 fallback: pool across duration, group by (event_type, liquidity_regime) only.

    Sets fallback_level=2 on all results.
    """
    df = df.copy()
    df["cell_id"] = df["liquidity_regime"]  # e.g. "HIGH"
    return group_by_event_cell(df, fallback_level=2)


# ---------------------------------------------------------------------------
# Plan 03: hierarchical fallback, kill-switch, JSON update, CLI
# ---------------------------------------------------------------------------

# Pre-registered gate_k constant (power_budget.json D-08, D-11 — MUST NOT CHANGE).
# T-82-09: assert enforced in emit_wave1_decision to prevent accidental relaxation.
_GATE_K_REGISTERED: int = 4


def apply_hierarchical_fallback(
    escalate_to: Union[str, None],
    df: pd.DataFrame,
) -> tuple[list[CellStats], int]:
    """Apply hierarchical fallback based on audit_gate.escalate_to.

    Returns (cell_stats_list, fallback_level_int).

    Level 0: "L0" or None → full 2x3 cells (primary analysis)
    Level 1: "L1 ..." → pooled across liquidity (duration only, 2 cells)
    Level 2: "L2 ..." → pooled across duration (liquidity only, 3 cells)

    Unknown escalate_to values default to L0 with a stderr warning.
    """
    import sys as _sys

    if escalate_to is None or (
        isinstance(escalate_to, str) and escalate_to.startswith("L0")
    ):
        return group_by_event_cell(df, fallback_level=0), 0
    elif isinstance(escalate_to, str) and escalate_to.startswith("L1"):
        return group_by_event_duration(df), 1
    elif isinstance(escalate_to, str) and escalate_to.startswith("L2"):
        return group_by_event_liquidity(df), 2
    else:
        print(
            f"[wave1_power] WARNING: unknown escalate_to={escalate_to!r}, defaulting to L0",
            file=_sys.stderr,
        )
        return group_by_event_cell(df, fallback_level=0), 0


def emit_wave1_decision(
    stats_list: list[CellStats],
    gate_k: int = _GATE_K_REGISTERED,
) -> str:
    """Wave-1 kill-switch: return 'null-ship-v3' if ALL cells fail gate_k, else 'proceed'.

    D-06 (82-CONTEXT): fire only when unanimous — all cells × all events have
    n_eff_predicted < gate_k.
    D-26 (79-CONTEXT): partial pass → Phase 83 full verdict.

    T-82-09: assert gate_k == 4 to prevent accidental relaxation.
    """
    assert gate_k == _GATE_K_REGISTERED, (
        f"gate_k must be {_GATE_K_REGISTERED} per pre-registration D-11 (got {gate_k})"
    )
    if not stats_list:
        return "null-ship-v3"  # no cells = no power
    any_pass = any(s.n_eff_predicted >= gate_k for s in stats_list)
    return "proceed" if any_pass else "null-ship-v3"


def build_cell_output_dict(
    stats: CellStats,
    kappa: "KappaResult",
    bs: "BootstrapResult",
) -> dict:
    """Build the per-cell output dict matching CONTEXT.md schema specifics.

    Returns a dict with 7 keys:
        rho_bar, vif, n_eff_predicted, fleiss_kappa_scalar,
        fleiss_kappa_pairwise, bootstrap_ci_95, bootstrap_block_len
    """
    return {
        "rho_bar": round(stats.rho_bar, 6),
        "vif": round(stats.vif, 6),
        "n_eff_predicted": round(stats.n_eff_predicted, 6),
        "fleiss_kappa_scalar": round(kappa.scalar, 6),
        "fleiss_kappa_pairwise": {k: round(v, 6) for k, v in kappa.pairwise.items()},
        "bootstrap_ci_95": [round(bs.ci_95[0], 6), round(bs.ci_95[1], 6)],
        "bootstrap_block_len": round(bs.block_len, 6),
    }


def update_regime_breakdown(
    json_path: Path,
    _slot_labels_df: pd.DataFrame,
    stats_map: dict,
    pooled_stats_map: dict,
    fallback_level: int,
    wave1_decision: str,
) -> None:
    """Update regime_breakdown.json with wave-1 stats (additive only).

    T-82-07: Existing Phase 81 fields are never overwritten.
    D-11: Both cell-wise stats (stats_map) and pooled stats (pooled_stats_map)
          are recorded. Pooled stats go to 'cells_by_event_pooled' when
          fallback_level > 0 (L1/L2 hierarchical fallback).

    Args:
        json_path: Path to regime_breakdown.json.
        _slot_labels_df: Unused (reserved for future use).
        stats_map: dict keyed by (event, cell_id) → per-cell output dict (L0 cells).
        pooled_stats_map: dict keyed by (event, pool_cell_id) → per-pool-cell dict
                          (L1/L2 pooled cells). Empty dict for L0.
        fallback_level: 0, 1, or 2.
        wave1_decision: "proceed" | "null-ship-v3".
    """
    import json as _json

    data = _json.loads(json_path.read_text())

    # --- Attach per-cell stats to existing cells_by_event entries (L0 case) ---
    for event, cells in data.get("cells_by_event", {}).items():
        for cell in cells:
            key = (event, cell["cell_id"])
            if key in stats_map:
                cell_out = stats_map[key]
                for k, v in cell_out.items():
                    cell[k] = v  # additive — keys added, no existing key overwritten
                cell.pop("_phase82_placeholder", None)  # remove placeholder

    # --- Attach pooled stats under a dedicated section (L1/L2 fallback) ---
    # D-11: "cell-wise stats と pooled stats の両方を保持する"
    if pooled_stats_map:
        pooled_section: dict = {}
        for (event, pool_id), pool_out in pooled_stats_map.items():
            if event not in pooled_section:
                pooled_section[event] = []
            pooled_section[event].append({"pool_cell_id": pool_id, **pool_out})
        data["cells_by_event_pooled"] = pooled_section

    # --- Remove top-level _phase82_placeholder if present ---
    data.pop("_phase82_placeholder", None)

    # --- Emit wave1_decision and wave1_fallback_level (Phase 82-owned, always overwrite) ---
    data["wave1_decision"] = wave1_decision
    data["wave1_fallback_level"] = fallback_level

    json_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False))


def run_wave1(
    slot_labels_path: Path = Path("data/slot_labels.parquet"),
    regime_breakdown_path: Path = Path(
        "docs/reports/v4.8-regime-v2/regime_breakdown.json"
    ),
) -> str:
    """Main entry point for Phase 82 wave-1 aggregator.

    Returns wave1_decision: "proceed" | "null-ship-v3".

    Steps:
        1. Load slot_labels.parquet.
        2. Read regime_breakdown.json → get audit_gate.escalate_to.
        3. Apply hierarchical fallback (L0/L1/L2).
        4. Compute Fleiss kappa + bootstrap CI per cell.
        5. Emit wave1_decision (kill-switch).
        6. Update regime_breakdown.json (additive only).
    """
    import json as _json
    import sys as _sys

    import numpy as np

    # Import distributional stats module via importlib (v4.8 is not a valid Python path)
    import importlib.util as _ilu

    _dist_spec = _ilu.spec_from_file_location(
        "wave1_distributional_run",
        Path(__file__).parent / "wave1_distributional.py",
    )
    _dist = _ilu.module_from_spec(_dist_spec)
    _sys.modules["wave1_distributional_run"] = _dist
    _dist_spec.loader.exec_module(_dist)
    compute_fleiss_kappa = _dist.compute_fleiss_kappa
    compute_bootstrap_ci = _dist.compute_bootstrap_ci

    # 1. Load slot_labels
    df = load_slot_labels(slot_labels_path)

    # 2. Read regime_breakdown to get audit_gate.escalate_to
    data = _json.loads(regime_breakdown_path.read_text())
    escalate_to = data.get("audit_gate", {}).get("escalate_to", None)

    # 3. Apply hierarchical fallback
    cell_stats_list, fallback_level = apply_hierarchical_fallback(escalate_to, df)

    # 4. Compute Fleiss kappa + bootstrap CI per cell, build stats_map
    stats_map: dict = {}
    pooled_stats_map: dict = {}

    for stats in cell_stats_list:
        # Determine cell subset for distributional stats
        mask = df["event_type"] == stats.event_type
        if stats.fallback_level == 0:
            mask = mask & (df["cell_id"] == stats.cell_id)
        elif stats.fallback_level == 1:
            mask = mask & (df["duration_bucket"] == stats.cell_id)
        elif stats.fallback_level == 2:
            mask = mask & (df["liquidity_regime"] == stats.cell_id)
        cell_df = df[mask]

        kappa = compute_fleiss_kappa(cell_df)

        sign_series = np.sign(
            cell_df["long"].values.astype(float)
            - cell_df["short"].values.astype(float)
        )
        bs = compute_bootstrap_ci(sign_series)

        cell_out = build_cell_output_dict(stats, kappa, bs)

        if stats.fallback_level == 0:
            stats_map[(stats.event_type, stats.cell_id)] = cell_out
        else:
            pooled_stats_map[(stats.event_type, stats.cell_id)] = cell_out

    # 5. Wave-1 kill-switch decision
    wave1_decision = emit_wave1_decision(cell_stats_list)

    # 6. Update regime_breakdown.json (additive only)
    update_regime_breakdown(
        regime_breakdown_path,
        df,
        stats_map,
        pooled_stats_map,
        fallback_level,
        wave1_decision,
    )

    return wave1_decision


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Phase 82 Wave-1 Aggregator")
    parser.add_argument(
        "--slot-labels",
        type=Path,
        default=Path("data/slot_labels.parquet"),
        help="Path to slot_labels.parquet (Phase 81 output)",
    )
    parser.add_argument(
        "--regime-breakdown",
        type=Path,
        default=Path("docs/reports/v4.8-regime-v2/regime_breakdown.json"),
        help="Path to regime_breakdown.json (Phase 81 output)",
    )
    args = parser.parse_args()
    decision = run_wave1(args.slot_labels, args.regime_breakdown)
    print(f"[wave1_power] wave1_decision: {decision}")
    sys.exit(0 if decision in ("proceed", "null-ship-v3") else 1)
