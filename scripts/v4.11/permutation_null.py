"""v4.11 Phase 95 SHIP-05: permutation null via cell-level global label shuffle.

D-47: Shuffle unit = cell (64-slot space, SEAL post_filter_m_prime);
      bucket distribution (VOL_HIGH × n_hi, ...) preserved (sample without
      replacement over label pool — rng.permutation of the multiset of labels).
D-48: Seed = 20260425 (Phase 95 fixed literal, SEAL 外 pin); single
      numpy Generator for all B iters.
D-49: Null statistic = edge_count_p_adj_005 single primary.
D-50: ship_verdict condition = observed > null_95th strict inequality
      (tie → fail side).

Input artifacts (Plan 1 upstream):
  - data/v4.11/cells_post_filter.parquet  (cell_id, pass_flag, bucket)
  - reports/v4.11/active_mode/p_adj_v411.json  (per-cell p_raw for tested cells)
  - reports/v4.11/active_mode/filter_eval.json  (kill_switch_consumed provenance)

Output artifact:
  - reports/v4.11/active_mode/permutation_null_v411.json

Import pattern (D-35 / conftest.py): scripts/v4.11 has a dot in the path,
so `from bootstrap_v411 import ...` is used (not `scripts.v4.11.bootstrap_v411`).
A sys.path guard is inserted for direct `uv run python` invocation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

# D-35 flat import path: scripts/v4.11 contains a dot, cannot be a package.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from bootstrap_v411 import (  # noqa: E402
    M_PRIME,
    _SIGNAL_COMMIT_V411,
    apply_bonferroni_holm,
)

# ---------------------------------------------------------------------------
# Pre-registered constants (D-48 / D-50)
# ---------------------------------------------------------------------------
PERMUTATION_SEED: int = 20260425
B_SAMPLES: int = 2000
ALPHA: float = 0.05
SHUFFLE_UNIT: str = "cell"

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CELLS_POST_FILTER: Path = _REPO_ROOT / "data" / "v4.11" / "cells_post_filter.parquet"
_P_ADJ_REAL: Path = _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "p_adj_v411.json"
_FILTER_EVAL: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "filter_eval.json"
)
_OUTPUT: Path = (
    _REPO_ROOT / "reports" / "v4.11" / "active_mode" / "permutation_null_v411.json"
)

# Phase 93 D-34 runtime expansion of SEAL allowed_buckets=["HIGH"]
# (the SEAL literal is bucket family "HIGH"; vol classifier emits VOL_HIGH
# in the parquet). See 95-CONTEXT.md D-47 / D-34.
_ALLOWED_BUCKETS: frozenset[str] = frozenset({"VOL_HIGH"})


# ---------------------------------------------------------------------------
# Per-cell p_raw loader (shuffle labels, keep outcomes — see 95-CONTEXT specifics)
# ---------------------------------------------------------------------------
def _load_per_cell_p_raw(p_adj_path: Path = _P_ADJ_REAL) -> dict[str, float]:
    """Extract per-cell p_raw for *tested* cells from p_adj_v411.json.

    Padded rows (status="padded") are not shuffled — they are re-synthesized
    per-iter after shuffle to re-pad the permuted sample to M_PRIME for
    Bonferroni-Holm.
    """
    doc = json.loads(p_adj_path.read_text(encoding="utf-8"))
    return {
        r["cell_id"]: float(r["p_raw"])
        for r in doc["results"]
        if r["status"] == "tested" and r["p_raw"] is not None
    }


def permute_one(
    rng: np.random.Generator,
    all_cells: list[str],
    bucket_of: dict[str, str],
    per_cell_p_raw: dict[str, float],
    allowed_buckets: frozenset[str] | set[str] = _ALLOWED_BUCKETS,
) -> int:
    """Execute one permutation iteration.

    D-47 bucket distribution preserved (rng.permutation of label multiset):
      - Collect bucket labels for every cell (including original pass_flag=false
        cells — preserves the 64 / 192 global denominator geometry).
      - rng.permutation over the label multiset → new assignment cell → bucket.
      - Derive permuted pass_flag = (bucket_new ∈ allowed_buckets).
      - For permuted-pass cells: use their ORIGINAL p_raw (shuffle labels not
        outcomes — 95-CONTEXT specifics item 4). Cells with no real p_raw
        (originally pass_flag=false) use 1.0 (same padding rule as D-44).
      - Truncate to M_PRIME if the permuted pass set exceeds the denominator.
      - Run apply_bonferroni_holm on length-M_PRIME vector (padded), count
        entries strictly below ALPHA → edge_count_p_adj_005 for this iter.
    """
    labels = [bucket_of[c] for c in all_cells]
    permuted = rng.permutation(labels).tolist()
    permuted_pass = [c for c, b in zip(all_cells, permuted) if b in allowed_buckets]
    n_pass = len(permuted_pass)
    if n_pass > M_PRIME:
        permuted_pass = permuted_pass[:M_PRIME]
        n_pass = M_PRIME
    p_raw_vec = [per_cell_p_raw.get(c, 1.0) for c in permuted_pass]
    p_raw_vec_padded = p_raw_vec + [1.0] * (M_PRIME - n_pass)
    p_adj = apply_bonferroni_holm(p_raw_vec_padded)
    return int(sum(1 for p in p_adj if p < ALPHA))


def compute_observed_edge_count(p_adj_path: Path = _P_ADJ_REAL) -> int:
    """Count real p_adj_holm entries strictly below ALPHA."""
    doc = json.loads(p_adj_path.read_text(encoding="utf-8"))
    return int(
        sum(
            1
            for r in doc["results"]
            if r["p_adj_holm"] is not None and r["p_adj_holm"] < ALPHA
        )
    )


def ship_condition_met(observed: int, null_dist: list[int]) -> bool:
    """D-50: observed > null_95th_percentile strict. Tie → False.

    Explicit strict inequality: `float(observed) > p95`. Do not relax to `>=`.
    """
    p95 = float(np.percentile(null_dist, 95))
    return float(observed) > p95


def _build_null_distribution(
    all_cells: list[str],
    bucket_of: dict[str, str],
    per_cell_p_raw: dict[str, float],
    allowed_buckets: frozenset[str] | set[str] = _ALLOWED_BUCKETS,
    b_samples: int = B_SAMPLES,
    seed: int = PERMUTATION_SEED,
) -> list[int]:
    """Run B permutations with a single numpy Generator stream (D-48)."""
    rng = np.random.default_rng(seed)
    return [
        permute_one(rng, all_cells, bucket_of, per_cell_p_raw, allowed_buckets)
        for _ in range(b_samples)
    ]


def main(output_path: Path = _OUTPUT) -> None:
    cells_df = pl.read_parquet(_CELLS_POST_FILTER)
    all_cells: list[str] = cells_df.get_column("cell_id").to_list()
    bucket_of: dict[str, str] = dict(
        zip(
            cells_df.get_column("cell_id").to_list(),
            cells_df.get_column("bucket").to_list(),
        )
    )

    per_cell_p_raw = _load_per_cell_p_raw(_P_ADJ_REAL)
    filter_eval = json.loads(_FILTER_EVAL.read_text(encoding="utf-8"))
    kill_switch_consumed = bool(filter_eval.get("kill_switch_consumed", False))

    null_edge_counts = _build_null_distribution(
        all_cells=all_cells,
        bucket_of=bucket_of,
        per_cell_p_raw=per_cell_p_raw,
        allowed_buckets=_ALLOWED_BUCKETS,
        b_samples=B_SAMPLES,
        seed=PERMUTATION_SEED,
    )

    observed = compute_observed_edge_count()
    p50 = float(np.percentile(null_edge_counts, 50))
    p95 = float(np.percentile(null_edge_counts, 95))
    p99 = float(np.percentile(null_edge_counts, 99))
    condition_met = ship_condition_met(observed, null_edge_counts)

    doc = {
        "provenance": {
            "signal_commit_v411": _SIGNAL_COMMIT_V411,
            "B": B_SAMPLES,
            "seed": PERMUTATION_SEED,
            "shuffle_unit": SHUFFLE_UNIT,
            "allowed_buckets": sorted(_ALLOWED_BUCKETS),
            "m_prime": M_PRIME,
            "kill_switch_consumed": kill_switch_consumed,
        },
        "observed_edge_count_p_adj_005": int(observed),
        "null_edge_counts": null_edge_counts,
        "null_percentiles": {"p50": p50, "p95": p95, "p99": p99},
        "ship_condition_met": bool(condition_met),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"[permutation_null] B={B_SAMPLES} seed={PERMUTATION_SEED} "
        f"observed={observed} p95={p95} condition_met={condition_met} "
        f"-> {output_path}"
    )


if __name__ == "__main__":
    main()
