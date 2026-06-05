"""v4.10 Phase 91 SHIP-01: FWER block bootstrap + Bonferroni-Holm correction.

H0-centered stationary block bootstrap over 192 cell_ids × 2 fold_ids (M=384).
D-14 SEAL import-time check: sizing_exit_commit_v410 drift raises RuntimeError.
D-30: v4.8 verdict_fwer.py pattern rewrite — same structure, v4.10 native context.
Copy-paste from verdict_fwer.py is prohibited per D-30 protocol constraint.

Pre-registered constants (91-SEAL.md, 2026-04-23):
    _BOOTSTRAP_SEED = 42
    _N_BOOTSTRAP_SAMPLES = 1000
    M_HYPOTHESES = 384
    sizing_exit_commit_v410 = a5f71831...
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
from arch.bootstrap import StationaryBootstrap, optimal_block_length
from statsmodels.stats.multitest import multipletests

# ---------------------------------------------------------------------------
# Pre-registered constants — DO NOT MODIFY (91-SEAL.md / SHIP-01)
# ---------------------------------------------------------------------------
_BOOTSTRAP_SEED: int = 42
_N_BOOTSTRAP_SAMPLES: int = 1000
M_HYPOTHESES: int = 384  # 192 cell_ids × 2 fold_ids
ALPHA: float = 0.05

_SIZING_EXIT_COMMIT_V410: str = (
    "a5f71831851bc09fea1ac5f1335e8f3e01465913ec1a4e771c1c53072b51f27f"
)
_SHIP_DECISION_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "reports"
    / "v4.10"
    / "v4_10_ship_decision.json"
)


# ---------------------------------------------------------------------------
# D-14 SEAL import-time check
# ---------------------------------------------------------------------------
def _verify_seal_at_import() -> None:
    """Fail-close at import time if sizing_exit_commit_v410 drifts.

    Reads quint_pin_stamp.sizing_exit_commit_v410 from v4_10_ship_decision.json
    and compares against the module constant. Raises RuntimeError on mismatch.
    """
    data = json.loads(_SHIP_DECISION_PATH.read_text(encoding="utf-8"))
    actual = data["overlay_evaluation"]["quint_pin_stamp"]["sizing_exit_commit_v410"]
    if actual != _SIZING_EXIT_COMMIT_V410:
        raise RuntimeError(
            "SEAL drift detected: sizing_exit_commit_v410 mismatch\n"
            f"  expected: {_SIZING_EXIT_COMMIT_V410}\n"
            f"  actual:   {actual}"
        )


_verify_seal_at_import()  # import-time fail-close (D-14)


# ---------------------------------------------------------------------------
# Bootstrap p-value
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

    # Block length via Politis-White / Patton-Politis-White correction
    try:
        bl_df = optimal_block_length(arr)
        block_len = float(bl_df["stationary"].iloc[0])
        if not np.isfinite(block_len) or block_len < 1.0:
            block_len = 1.0
    except Exception:
        block_len = max(1.0, int(len(arr) ** (1 / 3)))

    # H0-centering: subtract observed mean so bootstrap distribution is under H0
    arr_h0 = arr - observed

    bs = StationaryBootstrap(block_len, arr_h0, seed=seed)
    means = np.array([data[0][0].mean() for data, _ in bs.bootstrap(n_samples)])
    p = float(np.mean(np.abs(means) >= abs(observed)))
    return p


# ---------------------------------------------------------------------------
# Bonferroni-Holm step-down correction
# ---------------------------------------------------------------------------
def apply_bonferroni_holm(p_raw: list[float]) -> list[float]:
    """Apply Holm (1979) step-down FWER correction over M_HYPOTHESES p-values.

    Args:
        p_raw: List of raw p-values. Length must equal M_HYPOTHESES (384).

    Returns:
        List of FWER-adjusted p-values in original order, each in [0.0, 1.0].

    Raises:
        AssertionError: If len(p_raw) != M_HYPOTHESES.
    """
    assert len(p_raw) == M_HYPOTHESES, (
        f"Expected {M_HYPOTHESES} p-values (192 cells × 2 folds), got {len(p_raw)}"
    )
    _, p_adj, _, _ = multipletests(p_raw, alpha=ALPHA, method="holm")
    return p_adj.tolist()


# ---------------------------------------------------------------------------
# Main: emit p_adj_v410.json
# ---------------------------------------------------------------------------
def main(
    dd_traces_path: str = "data/v4.10/dd_traces.parquet",
    output_path: str = "reports/v4.10/p_adj_v410.json",
    n_samples: int = _N_BOOTSTRAP_SAMPLES,
) -> None:
    """Read dd_traces.parquet, run FWER bootstrap, write p_adj_v410.json.

    Args:
        dd_traces_path: Path to Phase 90 dd_traces.parquet (no pnl column —
            pnl is derived via equity.diff().over([cell_id, fold_id])).
        output_path: Destination JSON path (384 rows).
        n_samples: Bootstrap replications (default = pre-registered 1000).
    """
    df = pl.read_parquet(dd_traces_path)

    # Derive pnl from equity (Pitfall 1: parquet has no pnl column)
    df = df.with_columns(
        pl.col("equity").diff().over(["cell_id", "fold_id"]).alias("pnl")
    ).filter(pl.col("pnl").is_not_null())

    # Enumerate all (cell_id, fold_id) pairs in sorted order
    pairs_df = df.select(["cell_id", "fold_id"]).unique().sort(["cell_id", "fold_id"])
    pairs = list(pairs_df.iter_rows())

    p_raw_list: list[float] = []
    pair_meta: list[tuple[str, int]] = []

    for cell_id, fold_id in pairs:
        pnl = (
            df.filter((pl.col("cell_id") == cell_id) & (pl.col("fold_id") == fold_id))
            .get_column("pnl")
            .to_numpy()
        )
        p = bootstrap_pvalue(pnl, n_samples=n_samples, seed=_BOOTSTRAP_SEED)
        p_raw_list.append(p)
        pair_meta.append((str(cell_id), int(fold_id)))

    p_adj_list = apply_bonferroni_holm(p_raw_list)

    results = [
        {
            "cell_id": cid,
            "fold_id": fid,
            "p_raw": float(p_raw),
            "p_adj_holm": float(p_adj),
        }
        for (cid, fid), p_raw, p_adj in zip(pair_meta, p_raw_list, p_adj_list)
    ]

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[bootstrap_v410] Wrote {len(results)} rows → {out}")


if __name__ == "__main__":
    main()
