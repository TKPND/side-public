"""v4.8 Phase 83 VERDICT-02: Full verdict FWER correction core.

Bonferroni-Holm step-down FWER correction over m=72 hypotheses
(3 events × 6 L0 cells × 4 pair slots with 1 padded sentinel per
Q2 decision). Config-drift abort guards SCOPE-03 (regime_commit=90bf4b2).

Threat mitigations:
    T-83-01: m=72 is a module-top constant; assert enforced at call site.
    T-83-02: method='holm' literal assert; BH/FDR 切替禁止 (SCOPE-03).
    T-83-03: config_drift abort exits code 1 before any computation.

Rule 3 deviation (auto-fix):
    Plan Task 3 specified --regime-cuts data/regime_cuts.json as the config
    drift target. However, REGIME_COMMIT=90bf4b2 is the Phase 79 pre-reg
    seal commit which touched .planning/milestones/.../regime_cuts.json
    (NOT data/regime_cuts.json which was created by Phase 80 at a5190ef).
    The default regime_cuts_path parameter is therefore set to the sealed
    spec file path, and callers should pass the sealed path explicitly.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

# ---------------------------------------------------------------------------
# Pre-registered constants — DO NOT MODIFY (SCOPE-03 / 79-SEAL.md)
# ---------------------------------------------------------------------------
M_HYPOTHESES: int = 72  # 6 L0 cells × 3 events × 4 pair slots (1 padded each)
ALPHA: float = 0.05  # fwer_correction_spec.md SEALED
REGIME_COMMIT: str = "90bf4b2"  # 79-SEAL.md Phase 79 pre-registration anchor
_BOOTSTRAP_SEED: int = 42  # Phase 82 踏襲 (T-82-04 / T-83-02)
_N_BOOTSTRAP_SAMPLES: int = 1000
GATE_K: int = 4  # power_budget.json SEALED
PADDED_PAIR_SENTINEL: str = "__PADDED__"
_REAL_PAIRS_EXPECTED: tuple = ("EURJPY", "EURUSD", "USDJPY")  # Q1 decision
_EVENTS_CANONICAL: tuple = ("ECB", "FOMC", "NFP")
_L0_CELLS_CANONICAL: tuple = (
    "0-60m_x_HIGH",
    "0-60m_x_LOW",
    "0-60m_x_MID",
    "60-120m_x_HIGH",
    "60-120m_x_LOW",
    "60-120m_x_MID",
)
_SPEC_PATH: str = ".planning/milestones/v4.8-phases/79-scope-lock-pre-registration/fwer_correction_spec.md"

# Sealed spec regime_cuts.json path (Phase 79 pre-registration, REGIME_COMMIT=90bf4b2)
_SEALED_REGIME_CUTS_PATH: str = (
    ".planning/milestones/v4.8-phases/79-scope-lock-pre-registration/regime_cuts.json"
)

# ---------------------------------------------------------------------------
# Sibling import: wave1_power.load_slot_labels
# ---------------------------------------------------------------------------
_power_spec = importlib.util.spec_from_file_location(
    "wave1_power_verdict", Path(__file__).parent / "wave1_power.py"
)
_power = importlib.util.module_from_spec(_power_spec)
sys.modules["wave1_power_verdict"] = _power
_power_spec.loader.exec_module(_power)
load_slot_labels = _power.load_slot_labels


# ---------------------------------------------------------------------------
# Function 1: check_config_drift
# ---------------------------------------------------------------------------


def check_config_drift(regime_cuts_path: str = _SEALED_REGIME_CUTS_PATH) -> None:
    """Verify regime_cuts.json last-commit prefix == REGIME_COMMIT.

    Exits with code 1 if git log prefix mismatches (SCOPE-03 violation).
    Returns None on success.

    T-83-03: Must be called at the top of run_fwer_only before any computation.
    """
    result = subprocess.run(
        ["git", "log", "--oneline", "-1", "--", regime_cuts_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = (
            f"ERROR: git log failed for {regime_cuts_path} — SCOPE-03 violation\n"
            f"  returncode={result.returncode}\n"
            f"  stderr={result.stderr.strip()}\n"
        )
        sys.stderr.write(msg)
        sys.exit(1)

    stdout = result.stdout.strip()
    prefix = stdout[:7] if stdout else ""
    if prefix != REGIME_COMMIT:
        msg = (
            f"ERROR: regime_cuts.json git commit mismatch — SCOPE-03 violation\n"
            f"  expected prefix: {REGIME_COMMIT}\n"
            f"  actual prefix:   {prefix!r} (from: {stdout!r})\n"
        )
        sys.stderr.write(msg)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Function 2: bootstrap_pvalue
# ---------------------------------------------------------------------------


def bootstrap_pvalue(
    sign_series: Union[np.ndarray, pd.Series],
    n_samples: int = _N_BOOTSTRAP_SAMPLES,
    seed: int = _BOOTSTRAP_SEED,
) -> float:
    """Two-sided bootstrap p-value for H0: mean(sign_series) == 0.

    Uses Politis-Romano StationaryBootstrap with optimal block length.
    Seed is fixed at _BOOTSTRAP_SEED=42 for reproducibility (T-83-02).

    Edge cases:
        - len < 4: return 1.0 (conservative, underpowered)
        - observed mean ≈ 0: return 1.0 (no detectable signal)
    """
    from arch.bootstrap import StationaryBootstrap, optimal_block_length

    arr = np.asarray(sign_series, dtype=float)
    arr = arr[~np.isnan(arr)]

    if len(arr) < 4:
        return 1.0

    observed = float(np.mean(arr))
    if abs(observed) < 1e-12:
        return 1.0

    block_df = optimal_block_length(arr)
    block_len = float(block_df["stationary"].iloc[0])
    if not np.isfinite(block_len) or block_len < 1.0:
        block_len = 1.0

    arr_h0 = arr - observed  # Center under H0: shift distribution so mean=0
    bs = StationaryBootstrap(block_len, arr_h0, seed=seed)
    means = np.array([float(np.mean(data[0])) for data, _ in bs.bootstrap(n_samples)])
    return float(np.mean(np.abs(means) >= abs(observed)))


# ---------------------------------------------------------------------------
# Function 3: compute_vif_bar
# ---------------------------------------------------------------------------


def compute_vif_bar(cells_by_event_pooled: dict) -> tuple[float, float]:
    """Compute mean VIF across all pool cells and derive m_eff.

    Args:
        cells_by_event_pooled: dict from regime_breakdown.json
            {event: [{pool_cell_id, vif, ...}, ...]}

    Returns:
        (vif_bar_raw, m_eff) where m_eff = clamp(M_HYPOTHESES / vif_bar, 1.0, M_HYPOTHESES)
    """
    vifs = [
        cell["vif"]
        for ev_cells in cells_by_event_pooled.values()
        for cell in ev_cells
        if "vif" in cell and np.isfinite(cell["vif"]) and cell["vif"] > 0
    ]
    if not vifs:
        # No valid VIF data → treat as maximum correlation (most conservative).
        # vif_bar = M_HYPOTHESES → m_eff = M_HYPOTHESES / M_HYPOTHESES = 1.0
        vif_bar = float(M_HYPOTHESES)
    else:
        vif_bar = float(np.mean(vifs))

    m_eff = max(
        1.0, min(float(M_HYPOTHESES), float(M_HYPOTHESES) / max(vif_bar, 1e-12))
    )
    return (vif_bar, m_eff)


# ---------------------------------------------------------------------------
# Function 4: build_canonical_pvalue_array
# ---------------------------------------------------------------------------


def build_canonical_pvalue_array(slot_df: pd.DataFrame) -> dict:
    """Build length-72 canonical p-value array from slot_labels DataFrame.

    Canonical ordering: event (sorted) → l0_cell (sorted) → pair_slot (3 real + 1 padded).
    Padded slots (PADDED_PAIR_SENTINEL) receive p_nominal=1.0 (Q2 A decision).

    Returns:
        {"p_values": list[float], "slot_keys": list[tuple[str,str,str]]}
    """
    real_pairs_present = sorted(slot_df["pair"].unique())
    # 4 pair slots: sorted real pairs + PADDED_PAIR_SENTINEL to fill up to 4
    pair_slots = list(real_pairs_present) + [PADDED_PAIR_SENTINEL] * (
        4 - len(real_pairs_present)
    )

    slot_keys: list[tuple[str, str, str]] = []
    p_values: list[float] = []

    # L0 grain: 3 events × 6 L0 cells × 4 pair slots = 72 hypotheses (D-08)
    for event in sorted(_EVENTS_CANONICAL):
        for l0_cell in sorted(_L0_CELLS_CANONICAL):
            for pair in pair_slots:
                slot_keys.append((event, l0_cell, pair))
                if pair == PADDED_PAIR_SENTINEL:
                    p_values.append(1.0)  # Q2 A padding (18 slots: 3×6×1)
                else:
                    mask = (
                        (slot_df["event_type"] == event)
                        & (slot_df["cell_id"] == l0_cell)
                        & (slot_df["pair"] == pair)
                    )
                    # sign = long - short (consistent with wave1_power sign_ratio)
                    sign_series = slot_df.loc[mask, "long"].astype(float) - slot_df.loc[
                        mask, "short"
                    ].astype(float)
                    p_values.append(bootstrap_pvalue(sign_series.values))

    assert len(p_values) == M_HYPOTHESES, (
        f"m must be {M_HYPOTHESES} (SCOPE-03), got {len(p_values)}"
    )
    return {"p_values": p_values, "slot_keys": slot_keys}


# ---------------------------------------------------------------------------
# Function 5: apply_bonferroni_holm
# ---------------------------------------------------------------------------


def apply_bonferroni_holm(
    p_values: list,
    alpha: float = ALPHA,
    m_eff: float | None = None,
) -> dict:
    """Apply Bonferroni-Holm step-down FWER correction.

    T-83-01: asserts len(p_values) == M_HYPOTHESES (SCOPE-03).
    T-83-02: method literal 'holm' is enforced; BH/FDR switch prohibited.

    Args:
        p_values: list of M_HYPOTHESES raw p-values
        alpha: family-wise significance level (default 0.05, SEALED)
        m_eff: VIF-adjusted effective m (informational, stored in output)

    Returns:
        dict with method, alpha, m, m_eff, p_adj, reject keys.
    """
    assert len(p_values) == M_HYPOTHESES, (
        f"m must be {M_HYPOTHESES} (SCOPE-03), got {len(p_values)}"
    )
    _METHOD_LITERAL = "holm"  # SCOPE-03: BH/FDR/no-correction switch prohibited
    reject, p_adj, _, _ = multipletests(p_values, alpha=alpha, method=_METHOD_LITERAL)
    return {
        "method": "Bonferroni-Holm",
        "alpha": alpha,
        "m": M_HYPOTHESES,
        "m_eff": m_eff,
        "p_adj": [float(p) for p in p_adj],
        "reject": [bool(r) for r in reject],
    }


# ---------------------------------------------------------------------------
# Function 6: run_fwer_only
# ---------------------------------------------------------------------------


def run_fwer_only(
    regime_breakdown_path: str,
    slot_labels_path: str,
    regime_cuts_path: str = _SEALED_REGIME_CUTS_PATH,
) -> dict:
    """Run VERDICT-02 FWER correction end-to-end.

    Args:
        regime_breakdown_path: path to docs/reports/v4.8-regime-v2/regime_breakdown.json
        slot_labels_path: path to data/slot_labels.parquet
        regime_cuts_path: path to regime_cuts.json for config drift check
            Default: sealed spec path (.planning/milestones/...) whose last commit IS 90bf4b2

    Returns:
        dict compatible with fwer_correction_spec.md §4 Report Emit Fields,
        plus slot_keys for downstream cell_verdicts (Plan 02).
    """
    # T-83-03: abort before any computation if config drifted
    check_config_drift(regime_cuts_path)

    regime_breakdown = json.loads(Path(regime_breakdown_path).read_text())
    vif_bar, m_eff = compute_vif_bar(regime_breakdown["cells_by_event_pooled"])

    df = load_slot_labels(slot_labels_path)
    canon = build_canonical_pvalue_array(df)

    fwer = apply_bonferroni_holm(canon["p_values"], alpha=ALPHA, m_eff=m_eff)
    fwer["VIF_bar"] = vif_bar
    fwer["spec_source"] = _SPEC_PATH
    fwer["regime_commit"] = REGIME_COMMIT
    fwer["slot_keys"] = [list(k) for k in canon["slot_keys"]]  # JSON serializable

    return fwer


# ---------------------------------------------------------------------------
# Function 7: compute_pool_sign_ratios
# ---------------------------------------------------------------------------


def compute_pool_sign_ratios(slot_df: pd.DataFrame) -> dict:
    """Re-compute sign_ratio per (event_type, pool_cell_id) from slot_labels.

    cells_by_event_pooled does NOT carry sign_ratio (2026-04-21 JSON schema
    verified). pool_cell_id is derived from L0 cell_id duration prefix
    (split '_x_'[0]): e.g., "0-60m_x_HIGH" -> "0-60m".

    Returns:
        dict[event_type][pool_cell_id] -> float  # long-direction rate
    """
    df = slot_df.copy()
    df["_pool_cell_id"] = df["cell_id"].astype(str).str.split("_x_").str[0]
    if "direction" in df.columns:
        df["_is_long"] = (df["direction"].astype(str) == "long").astype(float)
        grouped = df.groupby(["event_type", "_pool_cell_id"])["_is_long"].mean()
        out: dict[str, dict[str, float]] = {}
        for (ev, pcid), mean_long in grouped.items():
            out.setdefault(str(ev), {})[str(pcid)] = float(mean_long)
    else:
        # fallback: long/short are count columns (e.g. long=3, short=2 out of 5 trials)
        # sign_ratio = long_count / (long_count + short_count), ignoring neutral
        # This keeps ratio in [0.0, 1.0] regardless of slot multiplicity
        df["_long"] = df["long"].astype(float)
        df["_short"] = df["short"].astype(float)
        df["_denom"] = df["_long"] + df["_short"]
        # Avoid divide-by-zero (all-neutral rows get 0.5 = no directional signal)
        df["_is_long"] = df["_long"] / df["_denom"].clip(lower=1e-12)
        grouped = df.groupby(["event_type", "_pool_cell_id"])[["_long", "_short"]].sum()
        out: dict[str, dict[str, float]] = {}
        for (ev, pcid), row in grouped.iterrows():
            denom = row["_long"] + row["_short"]
            sr = float(row["_long"] / denom) if denom > 1e-12 else 0.5
            out.setdefault(str(ev), {})[str(pcid)] = sr
    return out


# ---------------------------------------------------------------------------
# Function 8: classify_fail_candidate
# ---------------------------------------------------------------------------


def classify_fail_candidate(
    cell: dict,
    sign_ratio: float,
    gate_k: int = GATE_K,
) -> str:
    """4-candidate ordered exclusion per CONTEXT.md D-12 (字義通り).

    Returns one of: "sampling_noise", "structural", "bug".
    "config_drift" is not returned here — check_config_drift aborts before.

    Args:
        cell: pool cell dict from cells_by_event_pooled[event][i]
              (must have n_eff_predicted, bootstrap_ci_95 keys)
        sign_ratio: sr for this (event, pool_cell_id) from compute_pool_sign_ratios
        gate_k: sampling_noise threshold (default GATE_K=4)
    """
    n_eff = float(cell.get("n_eff_predicted", 0.0))
    ci = cell.get("bootstrap_ci_95", [0.0, 0.0])
    ci_lo = float(ci[0]) if len(ci) >= 1 else 0.0
    ci_hi = float(ci[1]) if len(ci) >= 2 else 0.0
    sr = float(sign_ratio)
    # Order: sampling_noise → structural → bug (D-12)
    if n_eff < gate_k:
        return "sampling_noise"
    # D-12 字義通り: CI が 0 を跨ぐ OR sign_ratio <= 0.0 (user confirmed 2026-04-21)
    if (ci_lo <= 0.0 <= ci_hi) or sr <= 0.0:
        return "structural"
    return "bug"


# ---------------------------------------------------------------------------
# Function 9: build_cell_verdicts
# ---------------------------------------------------------------------------


def build_cell_verdicts(
    cells_by_event_pooled: dict,
    pool_signs: dict,
    gate_k: int = GATE_K,
) -> dict:
    """L1 grain cell verdicts: event > pool_cell_id -> verdict entry.

    PASS iff n_eff >= gate_k AND ci_lo > 0 AND sign_ratio > 0.0 (D-12 字義通り).
    FAIL otherwise, with candidate assigned by classify_fail_candidate.

    Args:
        cells_by_event_pooled: from regime_breakdown.json
        pool_signs: from compute_pool_sign_ratios(slot_df).
                    pool_signs[event][pool_cell_id] -> float
    """
    out: dict[str, dict] = {}
    for event, pool_cells in cells_by_event_pooled.items():
        out[event] = {}
        for cell in pool_cells:
            pcid = cell["pool_cell_id"]
            n_eff = float(cell.get("n_eff_predicted", 0.0))
            ci = cell.get("bootstrap_ci_95", [0.0, 0.0])
            ci_lo = float(ci[0]) if len(ci) >= 1 else 0.0
            ci_hi = float(ci[1]) if len(ci) >= 2 else 0.0
            # sign_ratio は pool_signs から取得 (cells_by_event_pooled には無い)
            sr = float(pool_signs.get(event, {}).get(pcid, 0.0))
            # D-12 字義通り PASS: sr > 0.0 (strict)
            is_pass = (n_eff >= gate_k) and (ci_lo > 0.0) and (sr > 0.0)
            if is_pass:
                verdict, candidate = "PASS", None
            else:
                verdict, candidate = "FAIL", classify_fail_candidate(cell, sr, gate_k)
            out[event][pcid] = {
                "verdict": verdict,
                "candidate": candidate,
                "n_eff_predicted": n_eff,
                "sign_ratio": sr,
                "ci_95": [ci_lo, ci_hi],
                "vif": float(cell.get("vif", 0.0)),
                "rho_bar": float(cell.get("rho_bar", 0.0)),
            }
    return out


# ---------------------------------------------------------------------------
# Function 10: detect_simpson_paradox
# ---------------------------------------------------------------------------


def detect_simpson_paradox(
    cells_by_event_pooled: dict,
    pool_signs: dict,
    slot_df: pd.DataFrame,
) -> dict:
    """Event-unit Simpson paradox detection (D-14).

    Compares cell-majority sign (L1 pool cells, from pool_signs) against
    event-pool sign (re-computed from slot_labels without cell stratification).
    Grain separation is essential: arithmetic mean of pool cells is nearly
    tautological against majority, so pooled must come from slot-level
    aggregation.

    cell_majority は sign_ratio > 0.5 (majority 二値判定)。
    PASS 閾値 (sr > 0.0) とは別軸。
    """
    affected: list[str] = []
    summary: dict[str, dict] = {}
    for event, pool_cells in cells_by_event_pooled.items():
        if not pool_cells:
            continue
        event_signs = pool_signs.get(event, {})
        cell_srs = [float(event_signs.get(c["pool_cell_id"], 0.0)) for c in pool_cells]
        n_positive = sum(1 for s in cell_srs if s > 0.5)
        cell_majority_positive = n_positive > len(cell_srs) / 2.0
        # Event-pool sign from slot_labels (no cell stratification)
        mask = slot_df["event_type"] == event
        if mask.sum() == 0:
            continue
        if "direction" in slot_df.columns:
            pooled_sr = float((slot_df.loc[mask, "direction"] == "long").mean())
        else:
            # fallback: long/short are count columns — use same denominator as compute_pool_sign_ratios
            long_sum = float(slot_df.loc[mask, "long"].astype(float).sum())
            short_sum = float(slot_df.loc[mask, "short"].astype(float).sum())
            denom = long_sum + short_sum
            pooled_sr = (long_sum / denom) if denom > 1e-12 else 0.5
        pooled_sign_positive = pooled_sr > 0.5
        if cell_majority_positive != pooled_sign_positive:
            affected.append(event)
            summary[event] = {
                "cell_majority_sign_positive": cell_majority_positive,
                "pooled_sign_positive": pooled_sign_positive,
                "pooled_sign_ratio": pooled_sr,
                "cell_sign_ratios": {
                    c["pool_cell_id"]: float(event_signs.get(c["pool_cell_id"], 0.0))
                    for c in pool_cells
                },
            }
    return {
        "detected": len(affected) > 0,
        "affected_events": sorted(affected),
        "cell_summary": summary,
    }


# ---------------------------------------------------------------------------
# Function 11: run_verdict_fwer (orchestrator)
# ---------------------------------------------------------------------------


def run_verdict_fwer(
    regime_breakdown_path: Union[str, Path],
    slot_labels_path: Union[str, Path],
    regime_cuts_path: Union[str, Path] = _SEALED_REGIME_CUTS_PATH,
    out_path: Union[str, Path] = "docs/reports/v4.8-regime-v2/report.json",
    threshold_commit: str = "6527cbc",
) -> dict:
    """Full verdict orchestrator: FWER + VERDICT-01 + VERDICT-04 → report.json.

    Calls run_fwer_only() from Plan 01 for FWER, then adds:
    - cell_verdicts (VERDICT-01): McLean-Pontiff 4-candidate per FAIL cell
    - simpson_paradox (VERDICT-04): event-unit Simpson paradox detection

    Args:
        regime_breakdown_path: path to regime_breakdown.json (Phase 82 output)
        slot_labels_path: path to data/slot_labels.parquet (Phase 81 output)
        regime_cuts_path: path for config_drift SHA check (default: sealed spec)
        out_path: output path for report.json
        threshold_commit: v4.7 Phase 74 anchor commit (default: 6527cbc)

    Returns:
        report dict (also written to out_path)
    """
    from datetime import datetime, timezone

    # 1. config_drift guard (aborts before any computation if drifted)
    check_config_drift(str(regime_cuts_path))

    # 2. FWER correction (Plan 01 result)
    fwer = run_fwer_only(
        str(regime_breakdown_path), str(slot_labels_path), str(regime_cuts_path)
    )

    # 3. Load regime_breakdown and slot_labels for verdict classification
    regime_bd = json.loads(Path(regime_breakdown_path).read_text())
    pooled = regime_bd["cells_by_event_pooled"]

    slot_df = load_slot_labels(str(slot_labels_path))
    pool_signs = compute_pool_sign_ratios(slot_df)

    cell_verdicts = build_cell_verdicts(pooled, pool_signs, gate_k=GATE_K)
    simpson = detect_simpson_paradox(pooled, pool_signs, slot_df)

    # 4. Assemble report
    now_iso = datetime.now(timezone.utc).isoformat()
    report = {
        "schema_version": "v4.8-regime-v2-verdict",
        "generated_at": now_iso,
        "provenance": {
            "regime_commit": REGIME_COMMIT,
            "threshold_commit": threshold_commit,
            "input_regime_breakdown": str(regime_breakdown_path),
            "input_slot_labels": str(slot_labels_path),
            "generated_at": now_iso,
        },
        "fwer_correction": fwer,
        "cell_verdicts": cell_verdicts,
        "simpson_paradox": simpson,
    }

    # 5. Write to disk
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 83 VERDICT-01/02/04: Full verdict + FWER correction"
    )
    parser.add_argument(
        "--slot-labels",
        required=True,
        help="Path to data/slot_labels.parquet (Phase 81 output)",
    )
    parser.add_argument(
        "--regime-breakdown",
        required=True,
        help="Path to docs/reports/v4.8-regime-v2/regime_breakdown.json (Phase 82 output)",
    )
    parser.add_argument(
        "--regime-cuts",
        default=_SEALED_REGIME_CUTS_PATH,
        help=(
            "Path to regime_cuts.json for config drift SHA check. "
            f"Default: sealed spec path (last commit = {REGIME_COMMIT})"
        ),
    )
    parser.add_argument(
        "--out",
        default="docs/reports/v4.8-regime-v2/report.json",
        help="Output path for report.json (default: docs/reports/v4.8-regime-v2/report.json)",
    )
    parser.add_argument(
        "--threshold-commit",
        default="6527cbc",
        help="v4.7 Phase 74 threshold anchor commit (default: 6527cbc)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run_verdict_fwer(
        regime_breakdown_path=args.regime_breakdown,
        slot_labels_path=args.slot_labels,
        regime_cuts_path=args.regime_cuts,
        out_path=args.out,
        threshold_commit=args.threshold_commit,
    )
    print(json.dumps(result, indent=2))
