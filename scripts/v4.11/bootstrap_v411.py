"""v4.11 Phase 95 SHIP-01: FWER block bootstrap + Bonferroni-Holm correction.

Fork of v4.10 `scripts/v4.10/bootstrap_v410.py` (D-43 minimal-diff). Only SEAL
path / constant literal / drift-check anchor switched; `bootstrap_pvalue` and the
H0-centered stationary block bootstrap core are byte-identical to v4.10.

D-45 SEAL import-time check: signal_commit_v411 canonical sha256 drift raises
RuntimeError (fail-close, no degraded-mode).
D-44 degenerate branch padding: tested p_raw to Holm @ M_PRIME=64 denominator,
padded (M_PRIME - n_tested) slots to p_adj=1.0 — silent alpha inflation forbidden.

Pre-registered constants (Phase 92 SEAL + Phase 95 CONTEXT):
    _BOOTSTRAP_SEED = 42          (carry from v4.10)
    _N_BOOTSTRAP_SAMPLES = 1000   (carry from v4.10)
    M_PRIME = 64                  (SEAL filter_spec.json.fwer_denominator.post_filter_m_prime)
    signal_commit_v411 = f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
from arch.bootstrap import StationaryBootstrap, optimal_block_length
from statsmodels.stats.multitest import multipletests

# ---------------------------------------------------------------------------
# Pre-registered constants — DO NOT MODIFY (Phase 92 SEAL / SHIP-01)
# ---------------------------------------------------------------------------
_BOOTSTRAP_SEED: int = 42  # carry from v4.10 (Bonferroni bootstrap invariant)
_N_BOOTSTRAP_SAMPLES: int = 1000  # carry from v4.10
# D-44 / SEAL filter_spec.json.fwer_denominator.post_filter_m_prime hardcode.
# runtime_dynamic_prohibited (D-11): bootstrap_v411 MUST NOT compute a
# dynamic denominator from the filtered cell list. Literal 64 is the only legal denominator.
M_PRIME: int = 64
ALPHA: float = 0.05

_SIGNAL_COMMIT_V411: str = (
    "f8ccc8a806b847230c238b12011a479c77f7f10e6aed3f9959e8dbecfaa93bae"
)

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SEAL_DIR: Path = (
    _REPO_ROOT / ".planning" / "phases" / "92-scope-lock-pre-registration-seal" / "SEAL"
)
_P_ADJ_OUTPUT: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "p_adj_v411.json"
)
_CELLS_POST_FILTER: Path = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"
_FILTER_EVAL: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "filter_eval.json"
)
_NEUTRAL_SHIP_DECISION: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "neutral_mode" / "v4_11_ship_decision.json"
)


# ---------------------------------------------------------------------------
# D-45 SEAL import-time check (canonical sha256 replay per D-15)
# ---------------------------------------------------------------------------
def _canonical_sha256(seal_dir: Path) -> str:
    """Replay the D-15 canonical sha256 pipeline in pure Python.

    Reference pipeline (verify_signal_commit_v411.sh):
        for f in $(ls *.json | sort); do jq -cS . "$f"; done | sha256sum

    jq -cS produces:
      - compact output (no spaces) and sorted keys
      - each invocation appends a single trailing newline
      - non-ASCII characters are kept as-is (jq does NOT escape by default)

    Python reproduction (verified against the real SEAL on 2026-04-24,
    variant B in the matrix: ensure_ascii=False + \\n separator per file):
        hasher.update(json.dumps(data, sort_keys=True,
                                 separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8"))
        hasher.update(b"\\n")
    """
    hasher = hashlib.sha256()
    for fpath in sorted(seal_dir.glob("*.json")):
        data = json.loads(fpath.read_text(encoding="utf-8"))
        canonical = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        hasher.update(canonical.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _verify_seal_at_import() -> None:
    """Fail-close at import time if signal_commit_v411 drifts (D-45).

    Compares the recomputed canonical sha256 of _SEAL_DIR/*.json against
    the pre-registered _SIGNAL_COMMIT_V411 literal. Any mismatch is an
    irrecoverable condition (SEAL drift == post-hoc modification); we
    raise RuntimeError immediately. No WARNING log, no degraded mode.
    """
    actual = _canonical_sha256(_SEAL_DIR)
    if actual != _SIGNAL_COMMIT_V411:
        raise RuntimeError(
            f"signal_commit_v411 drift: expected={_SIGNAL_COMMIT_V411}, got={actual}"
        )


_verify_seal_at_import()  # import-time fail-close (D-45)


# ---------------------------------------------------------------------------
# Bootstrap p-value (byte-identical to v4.10)
# ---------------------------------------------------------------------------
def bootstrap_pvalue(
    arr: np.ndarray,
    n_samples: int = _N_BOOTSTRAP_SAMPLES,
    seed: int = _BOOTSTRAP_SEED,
) -> float:
    """Compute one-sided H0-centered block bootstrap p-value for H0: mean <= 0.

    Args:
        arr: PnL return array for a single (cell_id, fold_id) pair.
        n_samples: Number of bootstrap replications.
        seed: RNG seed for reproducibility (pre-registered = 42).

    Returns:
        p-value in [0.0, 1.0]. Returns 1.0 for degenerate inputs.
    """
    arr = arr[~np.isnan(arr)]
    if len(arr) < 4:
        return 1.0

    observed = float(arr.mean())
    if abs(observed) < 1e-12:
        return 1.0

    try:
        bl_df = optimal_block_length(arr)
        block_len = float(bl_df["stationary"].iloc[0])
        if not np.isfinite(block_len) or block_len < 1.0:
            block_len = 1.0
    except Exception:
        block_len = max(1.0, int(len(arr) ** (1 / 3)))

    arr_h0 = arr - observed

    bs = StationaryBootstrap(block_len, arr_h0, seed=seed)
    means = np.array([data[0][0].mean() for data, _ in bs.bootstrap(n_samples)])
    p = float(np.mean(np.abs(means) >= abs(observed)))
    return p


# ---------------------------------------------------------------------------
# Bonferroni-Holm step-down correction (M_PRIME=64 denominator)
# ---------------------------------------------------------------------------
def apply_bonferroni_holm(p_raw: list[float]) -> list[float]:
    """Apply Holm (1979) step-down FWER correction over M_PRIME p-values.

    v4.11 uses M_PRIME=64 (post-filter HIGH bucket grid design invariant per
    SEAL filter_spec.json.fwer_denominator.post_filter_m_prime).

    Input length MUST equal M_PRIME exactly. D-44 degenerate padding is
    applied by main() BEFORE calling this function (tested p_raw first,
    then (M_PRIME - n_tested) slots of 1.0).

    Args:
        p_raw: List of raw p-values. Length must equal M_PRIME (64).

    Returns:
        List of FWER-adjusted p-values in original order, each in [0.0, 1.0].

    Raises:
        AssertionError: If len(p_raw) != M_PRIME.
    """
    assert len(p_raw) == M_PRIME, (
        f"Expected {M_PRIME} p-values (SEAL post_filter_m_prime), got {len(p_raw)}"
    )
    _, p_adj, _, _ = multipletests(p_raw, alpha=ALPHA, method="holm")
    return p_adj.tolist()


# ---------------------------------------------------------------------------
# Per-cell p_raw loader (D-46)
# ---------------------------------------------------------------------------
def _load_per_cell_p_raw(
    tested_cells: list[str], neutral_path: Path
) -> dict[str, float]:
    """Fetch per-cell p_raw for the tested cells.

    Strategy (D-46):
      (a) Short-circuit: if tested_cells is empty (full degenerate branch —
          n_tested=0, as per Phase 93 kill_switch_fired=true + Phase 94
          post_filter_cell_count=0), return empty dict without consulting
          downstream sources. This is the honest-closure path: with zero
          tested cells there is nothing to load.
      (b) Primary: reports/v4.11/neutral_mode/v4_11_ship_decision.json may embed
          per_cell_p_raw (top-level or nested under ship_metrics).
      (c) Fallback: reports/v4.11/active_mode/per_cell_metrics.json emitted by
          active_mode_emit.py — missing both is a RuntimeError (upstream fix
          required, silent fallback to synthesized p_raw forbidden per D-17).
    """
    if not tested_cells:
        # Full degenerate branch — nothing to load.
        return {}

    neutral_doc = json.loads(neutral_path.read_text(encoding="utf-8"))
    raw: dict
    if "per_cell_p_raw" in neutral_doc:
        raw = neutral_doc["per_cell_p_raw"]
    elif (
        "ship_metrics" in neutral_doc
        and "per_cell_p_raw" in neutral_doc["ship_metrics"]
    ):
        raw = neutral_doc["ship_metrics"]["per_cell_p_raw"]
    else:
        fallback = (
            _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "per_cell_metrics.json"
        )
        if not fallback.exists():
            raise RuntimeError(
                f"per_cell_p_raw absent from {neutral_path} and fallback "
                f"{fallback} missing. Phase 94 or active_mode_emit.py must "
                "emit per-cell p_raw before bootstrap_v411 runs (D-46)."
            )
        rows = json.loads(fallback.read_text(encoding="utf-8"))
        raw = {r["cell_id"]: r["p_raw"] for r in rows if "p_raw" in r}

    missing = [c for c in tested_cells if c not in raw]
    if missing:
        raise RuntimeError(
            f"per_cell_p_raw missing for tested cells: {missing[:5]}... "
            f"(n={len(missing)})"
        )
    return {c: float(raw[c]) for c in tested_cells}


# ---------------------------------------------------------------------------
# Main: emit p_adj_v411.json with D-44 degenerate padding
# ---------------------------------------------------------------------------
def main(
    cells_post_filter_path: Path = _CELLS_POST_FILTER,
    neutral_ship_decision_path: Path = _NEUTRAL_SHIP_DECISION,
    filter_eval_path: Path = _FILTER_EVAL,
    output_path: Path = _P_ADJ_OUTPUT,
    n_samples: int = _N_BOOTSTRAP_SAMPLES,
) -> None:
    """Run FWER pipeline per D-44.

    Pipeline:
      1. Load cells_post_filter.parquet and select pass_flag=true cells.
      2. Source per-cell p_raw (neutral ship decision or active_mode fallback).
      3. Build length-M_PRIME vector: tested p_raw then padding to 1.0.
      4. Run Bonferroni-Holm at M_PRIME=64 denominator.
      5. Emit 64-row p_adj_v411.json with provenance block (n_tested,
         n_padded, kill_switch_consumed, signal_commit_v411, seed).
    """
    # 1. Load Phase 94 mask — deterministic order
    cells_df = pl.read_parquet(cells_post_filter_path)
    tested_cells = cells_df.filter(pl.col("pass_flag")).get_column("cell_id").to_list()
    n_tested = len(tested_cells)

    filter_eval = json.loads(filter_eval_path.read_text(encoding="utf-8"))
    kill_switch_consumed = bool(filter_eval.get("kill_switch_consumed", False))

    # 2. Source per-cell p_raw (empty dict when degenerate)
    p_raw_by_cell = _load_per_cell_p_raw(tested_cells, neutral_ship_decision_path)

    # 3. Build padded vector of length M_PRIME
    p_raw_vec: list[float] = [p_raw_by_cell[c] for c in tested_cells]
    padded_count = M_PRIME - n_tested
    if padded_count < 0:
        raise RuntimeError(
            f"n_tested={n_tested} exceeds M_PRIME={M_PRIME} -- "
            "SEAL denominator violated (runtime_dynamic_prohibited)"
        )
    p_raw_vec_padded = p_raw_vec + [1.0] * padded_count

    # 4. Holm @ M_PRIME=64
    p_adj_vec = apply_bonferroni_holm(p_raw_vec_padded)

    # 5. Emit 64 rows: tested then padded
    results: list[dict] = []
    for i, cell_id in enumerate(tested_cells):
        results.append(
            {
                "cell_id": cell_id,
                "status": "tested",
                "p_raw": float(p_raw_vec[i]),
                "p_adj_holm": float(p_adj_vec[i]),
            }
        )
    for j in range(padded_count):
        results.append(
            {
                "cell_id": f"__padded_slot_{j:02d}__",
                "status": "padded",
                "p_raw": None,
                "p_adj_holm": float(p_adj_vec[n_tested + j]),
            }
        )

    # Provenance block (D-44 audit visibility)
    output_doc = {
        "provenance": {
            "signal_commit_v411": _SIGNAL_COMMIT_V411,
            "m_prime": M_PRIME,
            "n_tested": n_tested,
            "n_padded": padded_count,
            "kill_switch_consumed": kill_switch_consumed,
            "seed": _BOOTSTRAP_SEED,
            "n_bootstrap_samples": n_samples,
            "source_per_cell": str(neutral_ship_decision_path),
            "source_mask": str(cells_post_filter_path),
        },
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"[bootstrap_v411] Wrote {len(results)} rows "
        f"(tested={n_tested}, padded={padded_count}) -> {output_path}"
    )


if __name__ == "__main__":
    main()
